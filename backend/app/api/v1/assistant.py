"""The assistant's configuration by layers (Feature 017): `/assistant/...`.

This module holds the parts the 017 adds: situations (TASK-462) and the rules of
the process template and of the rubro with their switches (TASK-435). The
business profile, FAQs, promotions and media (TASK-434, TASK-436) live in
`assistant_info.py`.

Writes need the configuration role (`ConfigurationManagerDep`). Switching rules
and marking news seen are also open to a member with the reviewer permission.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import ConfigurationManagerDep, JobQueueDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.models.situation import SITUATION_ORIGINS, SITUATION_STATES, Situation
from app.repositories.business import BusinessProfileRepository
from app.services.situations import (
    EMBED_JOB,
    MAX_QUE_HACE,
    SituationError,
    SituationInput,
    SituationService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])

AUTH_RESPONSES = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (
        403,
        "`email_not_verified`, `no_active_tenant`, `tenant_access_denied` or `insufficient_role`",
    ),
)

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
When = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
What = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUE_HACE)
]
Example = Annotated[str, StringConstraints(strip_whitespace=True, max_length=600)]
State = Literal["borrador", "activa", "inactiva"]
Origin = Literal["manual", "sugerida_rubro", "migrada", "aprendida"]

assert set(State.__args__) == set(SITUATION_STATES)  # type: ignore[attr-defined]
assert set(Origin.__args__) == set(SITUATION_ORIGINS)  # type: ignore[attr-defined]


class SituationRequest(BaseModel):
    titulo: Title
    cuando_aplica: When
    que_hace: What
    media_asset_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5)
    derivar: bool = False
    ejemplo_respuesta: Example | None = None
    # None or empty: every stage.
    etapas: list[str] | None = Field(default=None, max_length=11)
    siempre: bool = False

    def to_input(self) -> SituationInput:
        return SituationInput(
            titulo=self.titulo,
            cuando_aplica=self.cuando_aplica,
            que_hace=self.que_hace,
            media_asset_ids=self.media_asset_ids,
            derivar=self.derivar,
            ejemplo_respuesta=self.ejemplo_respuesta,
            etapas=self.etapas or None,
            siempre=self.siempre,
        )


class SituationResponse(BaseModel):
    id: uuid.UUID
    titulo: str
    cuando_aplica: str
    que_hace: str
    media_asset_ids: list[uuid.UUID]
    derivar: bool
    ejemplo_respuesta: str | None
    etapas: list[str] | None
    siempre: bool
    estado: State
    origen: Origin
    # False until the worker computed the embedding: until then it only enters
    # a turn if it is marked "siempre".
    lista_para_coincidir: bool
    created_at: datetime
    updated_at: datetime


class SituationList(BaseModel):
    items: list[SituationResponse]


class FromSuggestedRequest(BaseModel):
    # {campo: valor} for every field the suggestion asks for.
    valores: dict[str, Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]] = (
        Field(default_factory=dict)
    )


def _response(situation: Situation) -> SituationResponse:
    return SituationResponse(
        id=situation.id,
        titulo=situation.titulo,
        cuando_aplica=situation.cuando_aplica,
        que_hace=situation.que_hace,
        media_asset_ids=list(situation.media_asset_ids),
        derivar=situation.derivar,
        ejemplo_respuesta=situation.ejemplo_respuesta,
        etapas=list(situation.etapas) if situation.etapas else None,
        siempre=situation.siempre,
        estado=situation.estado,
        origen=situation.origen,
        lista_para_coincidir=situation.embedding is not None,
        created_at=situation.created_at,
        updated_at=situation.updated_at,
    )


def _invalid(exc: SituationError) -> ApiError:
    return ApiError(status_code=422, error=exc.code, message=str(exc))


def _not_found() -> ApiError:
    return ApiError(status_code=404, error="situation_not_found", message="Situation not found.")


async def _embed_later(queue: JobQueueDep, tenant_id: uuid.UUID) -> None:
    """Ask the worker for the embedding. A lost job is picked up by the hourly sweep."""
    try:
        await queue.enqueue(EMBED_JOB, str(tenant_id))
    except Exception:  # noqa: BLE001 - the sweep embeds it within the hour
        logger.warning("could not enqueue the situation embedding; the sweep will do it")


def _load(service: SituationService, situation_id: uuid.UUID) -> Situation:
    situation = service.get(situation_id)
    if situation is None:
        raise _not_found()
    return situation


@router.get(
    "/situations",
    response_model=SituationList,
    responses=error_responses(*AUTH_RESPONSES),
)
def list_situations(
    principal: ConfigurationManagerDep,
    session: SessionDep,
    estado: Annotated[State | None, Query()] = None,
    origen: Annotated[Origin | None, Query()] = None,
) -> SituationList:
    service = SituationService(session, principal.tenant_id)
    return SituationList(
        items=[_response(s) for s in service.listing(estado=estado, origen=origen)]
    )


@router.post(
    "/situations",
    status_code=status.HTTP_201_CREATED,
    response_model=SituationResponse,
    responses=error_responses(
        *AUTH_RESPONSES, (422, "`que_hace_too_long`, `unknown_stage` or `unknown_media`")
    ),
)
async def create_situation(
    body: SituationRequest,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> SituationResponse:
    service = SituationService(session, principal.tenant_id)
    try:
        situation = service.create(body.to_input(), by=principal.user_id)
    except SituationError as exc:
        raise _invalid(exc) from exc
    session.commit()
    await _embed_later(queue, principal.tenant_id)
    return _response(situation)


@router.post(
    "/situations/desde-sugerida/{suggested_id}",
    status_code=status.HTTP_201_CREATED,
    response_model=SituationResponse,
    responses=error_responses(*AUTH_RESPONSES, (422, "`unknown_suggestion` or `missing_fields`")),
)
async def create_from_suggested(
    suggested_id: uuid.UUID,
    body: FromSuggestedRequest,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> SituationResponse:
    profile = BusinessProfileRepository(session, principal.tenant_id).current()
    service = SituationService(session, principal.tenant_id)
    try:
        situation = service.from_suggested(
            suggested_id,
            profile.industry if profile else None,
            body.valores,
            by=principal.user_id,
        )
    except SituationError as exc:
        raise _invalid(exc) from exc
    session.commit()
    await _embed_later(queue, principal.tenant_id)
    return _response(situation)


@router.get(
    "/situations/{situation_id}",
    response_model=SituationResponse,
    responses=error_responses(*AUTH_RESPONSES, (404, "`situation_not_found`")),
)
def get_situation(
    situation_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> SituationResponse:
    return _response(_load(SituationService(session, principal.tenant_id), situation_id))


@router.put(
    "/situations/{situation_id}",
    response_model=SituationResponse,
    responses=error_responses(
        *AUTH_RESPONSES,
        (404, "`situation_not_found`"),
        (422, "`que_hace_too_long`, `unknown_stage` or `unknown_media`"),
    ),
)
async def update_situation(
    situation_id: uuid.UUID,
    body: SituationRequest,
    principal: ConfigurationManagerDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> SituationResponse:
    service = SituationService(session, principal.tenant_id)
    situation = _load(service, situation_id)
    try:
        service.update(situation, body.to_input())
    except SituationError as exc:
        raise _invalid(exc) from exc
    session.commit()
    if situation.embedding is None:
        await _embed_later(queue, principal.tenant_id)
    return _response(situation)


@router.delete(
    "/situations/{situation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(*AUTH_RESPONSES, (404, "`situation_not_found`")),
)
def delete_situation(
    situation_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> Response:
    service = SituationService(session, principal.tenant_id)
    service.delete(_load(service, situation_id))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _set_state(
    situation_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep, estado: str
) -> SituationResponse:
    service = SituationService(session, principal.tenant_id)
    situation = service.set_state(_load(service, situation_id), estado)
    session.commit()
    return _response(situation)


@router.post(
    "/situations/{situation_id}/activar",
    response_model=SituationResponse,
    responses=error_responses(*AUTH_RESPONSES, (404, "`situation_not_found`")),
)
def activate_situation(
    situation_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> SituationResponse:
    return _set_state(situation_id, principal, session, "activa")


@router.post(
    "/situations/{situation_id}/desactivar",
    response_model=SituationResponse,
    responses=error_responses(*AUTH_RESPONSES, (404, "`situation_not_found`")),
)
def deactivate_situation(
    situation_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> SituationResponse:
    return _set_state(situation_id, principal, session, "inactiva")
