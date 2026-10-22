"""The rubro learns: situations several businesses wrote alike (feature 017, phase 4).

    GET  /platform-admin/learning/candidates?rubro=   situations two businesses wrote alike
    POST /platform-admin/learning/adopt               offer one as a suggested situation

Only the platform operator (`PlatformAdminDep`, 404 to anybody else). Only the text of a rule
from the businesses that allowed it; adopting writes to the rubro's open draft (creating it if
there is none), which still has to pass the evals before it is published like any change.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, StringConstraints

from app.api.deps import PlatformAdminDep, PlatformLLMDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.learning.anonymizer import redact
from app.learning.rubro import candidates
from app.models.catalog import CatalogSuggestedSituation
from app.repositories.catalog import CatalogRepository
from app.services.admin import record_audit

router = APIRouter(prefix="/platform-admin/learning", tags=["platform-admin"])
ADMIN = ((401, "`not_authenticated`"), (404, "`not_found` for anybody but the operator"))


class CandidateOut(BaseModel):
    titulo: str
    cuando_aplica: str
    que_hace: str
    negocios: int
    variantes: list[str]
    con_datos: bool


class CandidateList(BaseModel):
    rubro: str
    items: list[CandidateOut]


class AdoptIn(BaseModel):
    rubro: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    titulo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    cuando_aplica: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ]
    que_hace: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]


class AdoptOut(BaseModel):
    version: int
    creada: bool


@router.get(
    "/candidates",
    summary="Situations at least two businesses of a rubro wrote alike",
    responses=error_responses(*ADMIN),
)
def list_candidates(
    rubro: str, _: PlatformAdminDep, session: SessionDep, llm: PlatformLLMDep
) -> CandidateList:
    found = candidates(session, rubro, model=llm.embedding_model)
    return CandidateList(
        rubro=rubro,
        items=[
            CandidateOut(
                titulo=c.titulo,
                cuando_aplica=c.cuando_aplica,
                que_hace=c.que_hace,
                negocios=c.negocios,
                variantes=c.variantes,
                con_datos=c.con_datos,
            )
            for c in found
        ],
    )


@router.post(
    "/adopt",
    summary="Offer a situation as a suggested one of the rubro (in its open draft)",
    responses=error_responses(
        *ADMIN,
        (404, "`not_found`: unknown rubro"),
        (409, "`already_there`: the draft has it"),
        (422, "`personal_data`: the text still has data of a person"),
    ),
)
def adopt(body: AdoptIn, admin: PlatformAdminDep, session: SessionDep) -> AdoptOut:
    catalog = CatalogRepository(session)
    if catalog.rubro(body.rubro) is None:
        raise ApiError(status_code=404, error="not_found", message="Unknown rubro.")
    text = f"{body.titulo} {body.cuando_aplica} {body.que_hace}"
    if redact(text) != text:
        raise ApiError(
            status_code=422,
            error="personal_data",
            message="El texto todavía trae datos de una persona: edítalo antes de ofrecerlo.",
        )
    draft = next((v for v in catalog.versions("rubro", body.rubro) if v.estado == "borrador"), None)
    created = draft is None
    if draft is None:
        draft = catalog.new_draft("rubro", body.rubro)
    existing = catalog.suggested_situations(draft.id)
    if any(s.titulo == body.titulo for s in existing):
        raise ApiError(
            status_code=409, error="already_there", message="El borrador ya tiene esa situación."
        )
    session.add(
        CatalogSuggestedSituation(
            version_id=draft.id,
            orden=len(existing) + 1,
            titulo=body.titulo,
            cuando_aplica=body.cuando_aplica,
            que_hace=body.que_hace,
            campos={},
        )
    )
    record_audit(
        session,
        actor_id=admin.id,
        action="catalog.learned_situation_adopted",
        detail={"rubro": body.rubro, "version": draft.version, "titulo": body.titulo},
    )
    session.commit()
    return AdoptOut(version=draft.version, creada=created)
