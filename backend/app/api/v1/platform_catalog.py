"""The platform catalogue: versions of templates and rubros (017, C-04, TASK-466).

Only the platform operator writes here (`PlatformAdminDep`, 404 to anybody
else). A version is a draft until it is published; publishing needs evals that
passed on the draft's current content (409 otherwise). Every write is audited.

After publishing, every business sees the new rules on its next turn (the
process cache is cleared here; other workers within 60 s), switched-off keys
stay off, the reviewer gets the news, and the rubro knowledge is re-indexed.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.api.deps import JobQueueDep, PlatformAdminDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.models.catalog import (
    CatalogFaq,
    CatalogObjection,
    CatalogRule,
    CatalogSuggestedSituation,
    CatalogVersion,
)
from app.process.service import DEFAULT_CACHE
from app.process.templates import TEMPLATES
from app.process.templates.base import Stage
from app.repositories.catalog import CatalogRepository
from app.services.admin import record_audit
from app.services.catalog_evals import EVALS_JOB, evals_allow_publishing
from app.services.catalog_knowledge import INDEX_CATALOG_JOB

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform-admin/catalog", tags=["platform-admin"])
ADMIN_RESPONSES = ((401, "`not_authenticated`"), (404, "`not_found` for anybody but the operator"))

Kind = Literal["proceso", "rubro"]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Long = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
RuleKey = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z_]+\.[a-z0-9_]{1,60}$")
]


class RuleIn(BaseModel):
    rule_key: RuleKey
    etapa: str | None = None
    texto: Long

    @field_validator("etapa")
    @classmethod
    def _stage(cls, value: str | None) -> str | None:
        if value is not None and value not in {s.value for s in Stage}:
            raise ValueError(f"etapa desconocida: {value}")
        return value


class FaqIn(BaseModel):
    pregunta: Short
    respuesta: Long


class ObjectionIn(BaseModel):
    objecion: Short
    manejo: Long


class SuggestedIn(BaseModel):
    titulo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    cuando_aplica: Short
    que_hace: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]
    campos: dict[str, Short] = Field(default_factory=dict)


class VersionContent(BaseModel):
    reglas: list[RuleIn] = Field(max_length=200)
    faqs: list[FaqIn] = Field(default_factory=list, max_length=30)
    objeciones: list[ObjectionIn] = Field(default_factory=list, max_length=30)
    situaciones_sugeridas: list[SuggestedIn] = Field(default_factory=list, max_length=10)
    notas: Annotated[str, StringConstraints(max_length=500)] | None = None


class VersionView(VersionContent):
    kind: str
    key: str
    version: int
    estado: str
    evals: dict[str, Any] | None
    evals_vigentes: bool
    publicada_en: str | None


class VersionSummary(BaseModel):
    version: int
    estado: str
    evals_ok: bool | None
    publicada_en: str | None


class CatalogEntry(BaseModel):
    kind: str
    key: str
    nombre: str
    publicada: int | None
    versiones: list[VersionSummary]


class CatalogList(BaseModel):
    items: list[CatalogEntry]


def _keys(session: SessionDep) -> list[tuple[str, str, str]]:
    rubros = CatalogRepository(session).rubros()
    return [("proceso", t.key, t.nombre) for t in TEMPLATES.values()] + [
        ("rubro", r.key, r.nombre) for r in rubros
    ]


def _known(session: SessionDep, kind: str, key: str) -> None:
    if (kind, key) not in {(k, key_) for k, key_, _ in _keys(session)}:
        raise ApiError(status_code=404, error="not_found", message="Unknown template or rubro.")


def _view(session: SessionDep, version: CatalogVersion) -> VersionView:
    catalog = CatalogRepository(session)
    return VersionView(
        kind=version.kind,
        key=version.key,
        version=version.version,
        estado=version.estado,
        evals=version.evals,
        evals_vigentes=evals_allow_publishing(session, version),
        publicada_en=version.publicada_en.isoformat() if version.publicada_en else None,
        notas=version.notas,
        reglas=[
            RuleIn(rule_key=r.rule_key, etapa=r.etapa, texto=r.texto)
            for r in catalog.rules(version.id)
        ],
        faqs=[FaqIn(pregunta=f.pregunta, respuesta=f.respuesta) for f in catalog.faqs(version.id)],
        objeciones=[
            ObjectionIn(objecion=o.objecion, manejo=o.manejo)
            for o in catalog.objections(version.id)
        ],
        situaciones_sugeridas=[
            SuggestedIn(
                titulo=s.titulo,
                cuando_aplica=s.cuando_aplica,
                que_hace=s.que_hace,
                campos=dict(s.campos),
            )
            for s in catalog.suggested_situations(version.id)
        ],
    )


def _version(session: SessionDep, kind: str, key: str, number: int) -> CatalogVersion:
    version = CatalogRepository(session).version(kind, key, number)
    if version is None:
        raise ApiError(status_code=404, error="not_found", message="Unknown version.")
    return version


def _frozen() -> ApiError:
    return ApiError(
        status_code=409, error="version_frozen", message="A published version cannot change."
    )


@router.get("", response_model=CatalogList, responses=error_responses(*ADMIN_RESPONSES))
def list_catalog(_: PlatformAdminDep, session: SessionDep) -> CatalogList:
    catalog = CatalogRepository(session)
    items = []
    for kind, key, name in _keys(session):
        versions = catalog.versions(kind, key)
        published = catalog.published(kind, key)
        items.append(
            CatalogEntry(
                kind=kind,
                key=key,
                nombre=name,
                publicada=published.version if published else None,
                versiones=[
                    VersionSummary(
                        version=v.version,
                        estado=v.estado,
                        evals_ok=(v.evals or {}).get("ok") if v.evals else None,
                        publicada_en=v.publicada_en.isoformat() if v.publicada_en else None,
                    )
                    for v in versions
                ],
            )
        )
    return CatalogList(items=items)


@router.get(
    "/{kind}/{key}/versions/{number}",
    response_model=VersionView,
    responses=error_responses(*ADMIN_RESPONSES),
)
def get_version(
    kind: Kind, key: str, number: int, _: PlatformAdminDep, session: SessionDep
) -> VersionView:
    return _view(session, _version(session, kind, key, number))


@router.post(
    "/{kind}/{key}/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=VersionView,
    responses=error_responses(*ADMIN_RESPONSES, (409, "`draft_exists`")),
)
def new_draft(kind: Kind, key: str, admin: PlatformAdminDep, session: SessionDep) -> VersionView:
    """A draft copied from the newest version. One draft at a time per template or rubro."""
    _known(session, kind, key)
    catalog = CatalogRepository(session)
    if any(v.estado == "borrador" for v in catalog.versions(kind, key)):
        raise ApiError(status_code=409, error="draft_exists", message="Edit the open draft.")
    draft = catalog.new_draft(kind, key)
    record_audit(
        session,
        actor_id=admin.id,
        action="catalog.draft_created",
        detail={"kind": kind, "key": key, "version": draft.version},
    )
    session.commit()
    return _view(session, draft)


@router.put(
    "/{kind}/{key}/versions/{number}",
    response_model=VersionView,
    responses=error_responses(
        *ADMIN_RESPONSES, (409, "`version_frozen`"), (422, "`invalid_content`")
    ),
)
def put_draft(
    kind: Kind,
    key: str,
    number: int,
    body: VersionContent,
    admin: PlatformAdminDep,
    session: SessionDep,
) -> VersionView:
    """Replace the draft's content. Its evals no longer apply (the hash changes)."""
    version = _version(session, kind, key, number)
    if version.estado != "borrador":
        raise _frozen()
    keys = [r.rule_key for r in body.reglas]
    if len(keys) != len(set(keys)):
        raise ApiError(status_code=422, error="invalid_content", message="Repeated rule_key.")
    if kind == "proceso" and any(r.etapa is None for r in body.reglas):
        raise ApiError(
            status_code=422, error="invalid_content", message="A process rule needs its stage."
        )
    if kind == "rubro" and (body.reglas and any(r.etapa for r in body.reglas)):
        raise ApiError(
            status_code=422, error="invalid_content", message="A rubro rule has no stage."
        )
    for model in (CatalogRule, CatalogFaq, CatalogObjection, CatalogSuggestedSituation):
        for row in session.query(model).filter(model.version_id == version.id):
            session.delete(row)
    session.flush()
    for i, rule in enumerate(body.reglas, start=1):
        session.add(CatalogRule(version_id=version.id, orden=i, **rule.model_dump()))
    for i, faq in enumerate(body.faqs, start=1):
        session.add(CatalogFaq(version_id=version.id, orden=i, **faq.model_dump()))
    for i, objection in enumerate(body.objeciones, start=1):
        session.add(CatalogObjection(version_id=version.id, orden=i, **objection.model_dump()))
    for i, situation in enumerate(body.situaciones_sugeridas, start=1):
        session.add(
            CatalogSuggestedSituation(version_id=version.id, orden=i, **situation.model_dump())
        )
    version.notas = body.notas
    record_audit(
        session,
        actor_id=admin.id,
        action="catalog.draft_edited",
        detail={"kind": kind, "key": key, "version": number, "reglas": len(body.reglas)},
    )
    session.commit()
    return _view(session, version)


@router.post(
    "/{kind}/{key}/versions/{number}/evals",
    status_code=status.HTTP_202_ACCEPTED,
    responses=error_responses(
        *ADMIN_RESPONSES, (409, "`version_frozen`"), (503, "`queue_unavailable`")
    ),
)
async def run_evals(
    kind: Kind,
    key: str,
    number: int,
    admin: PlatformAdminDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> dict[str, str]:
    """Queue the evals of the draft. The result lands on the version (`evals`)."""
    version = _version(session, kind, key, number)
    if version.estado != "borrador":
        raise _frozen()
    try:
        await queue.enqueue(EVALS_JOB, str(version.id))
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
        raise ApiError(
            status_code=503, error="queue_unavailable", message="Could not queue the evals."
        ) from exc
    record_audit(
        session,
        actor_id=admin.id,
        action="catalog.evals_queued",
        detail={"kind": kind, "key": key, "version": number},
    )
    session.commit()
    return {"estado": "encolado"}


@router.post(
    "/{kind}/{key}/versions/{number}/publicar",
    response_model=VersionView,
    responses=error_responses(*ADMIN_RESPONSES, (409, "`version_frozen` or `evals_not_passed`")),
)
async def publish(
    kind: Kind,
    key: str,
    number: int,
    admin: PlatformAdminDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> VersionView:
    version = _version(session, kind, key, number)
    if version.estado != "borrador":
        raise _frozen()
    if not evals_allow_publishing(session, version):
        raise ApiError(
            status_code=409,
            error="evals_not_passed",
            message="Run the evals on this content and make them pass first.",
        )
    catalog = CatalogRepository(session)
    previous = catalog.published(kind, key)
    catalog.publish(version, by=admin.id)
    if previous is not None:
        previous.estado = "retirada"
    record_audit(
        session,
        actor_id=admin.id,
        action="catalog.published",
        detail={"kind": kind, "key": key, "version": number},
    )
    session.commit()
    DEFAULT_CACHE.clear()
    if kind == "rubro":
        try:
            await queue.enqueue(INDEX_CATALOG_JOB)
        except Exception:  # noqa: BLE001 - the hourly run indexes it
            logger.warning("could not queue the rubro knowledge indexing; the hourly run will")
    return _view(session, version)
