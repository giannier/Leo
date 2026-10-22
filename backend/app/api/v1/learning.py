"""The agent proposes, a person decides (feature 017, phase 3; 019, phase H).

    GET  /assistant/learning                       on or off, what it has spent, what waits
    PUT  /assistant/learning                       turn it on, the weekly ceiling, the rubro
    GET  /assistant/suggestions                    the inbox
    GET  /assistant/suggestions/{id}               one, with the conversations that gave rise to it
    POST /assistant/suggestions/{id}/draft         a situation as a draft the simulator can try
    POST /assistant/suggestions/{id}/approve       put it in force, as it is or edited
    POST /assistant/suggestions/{id}/reject        discard it (it is not proposed again)
    GET  /assistant/history                        what changed, with what it was before
    POST /assistant/history/{id}/undo              put the "before" back

The reviewers of the business (the owner, the admins and whoever has the permission) read and
decide; turning learning on and its ceiling are for the owner and the admins: every analysis costs.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import ConfigurationManagerDep, JobQueueDep, ReviewerDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.api.v1.assistant import _embed_later
from app.api.v1.reviews import Line, _lines, _who
from app.learning.budget import LearningBudget
from app.learning.history import ConfigHistoryService, HistoryError
from app.learning.suggestions import SuggestionError, SuggestionService
from app.models.learning import SUGGESTION_SOURCES, SUGGESTION_STATES, SUGGESTION_TYPES, Suggestion
from app.repositories.learning import ConfigHistoryRepository, SuggestionRepository
from app.services import reviews
from app.services.faqs import index_faq

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["learning"])

AUTH = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)
NOT_FOUND = (404, "`not_found`")
DECISION_ERRORS = (
    (409, "`not_pending`: it was decided already"),
    (422, "`invalid_edit`, `answer_required`, `not_a_situation`"),
)
MAX_EXCERPT = 14


class LearningOut(BaseModel):
    activo: bool
    tope_semanal: int
    usadas: int
    quedan: int
    minutos_audio: float
    compartir_con_rubro: bool
    pendientes: dict[str, int]
    total_pendientes: int


class LearningIn(BaseModel):
    activo: bool | None = None
    tope_semanal: Annotated[int, Field(ge=0, le=500)] | None = None
    compartir_con_rubro: bool | None = None


class SuggestionRow(BaseModel):
    id: uuid.UUID
    tipo: str
    fuente: str
    estado: str
    resumen: str
    choques: int
    creada: datetime


class SuggestionList(BaseModel):
    items: list[SuggestionRow]


class Origin(BaseModel):
    conversation_id: uuid.UUID
    cliente: str
    canal: str
    mensajes: list[Line]


class Clash(BaseModel):
    tipo: str
    id: str
    titulo: str
    motivo: str


class SuggestionDetail(SuggestionRow):
    propuesta: dict[str, Any]
    choques_detalle: list[Clash]
    origen: list[Origin]
    nota: str | None
    decidida: datetime | None
    situation_id: uuid.UUID | None


class ApproveIn(BaseModel):
    # The card as the reviewer edited it; absent fields stay as proposed.
    editado: dict[str, Any] | None = None
    # A fact needs the answer the reviewer writes.
    respuesta: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = (
        None
    )


class RejectIn(BaseModel):
    nota: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class DecisionOut(BaseModel):
    sugerencia: SuggestionDetail
    # A fact is added to the knowledge after the decision; false if that could not be done now.
    indexada: bool = True


class DraftOut(BaseModel):
    situation_id: uuid.UUID


class HistoryRow(BaseModel):
    id: uuid.UUID
    entidad: str
    accion: str
    resumen: str
    cuando: datetime
    por_sugerencia: bool
    deshecho: bool
    se_puede_deshacer: bool


class HistoryList(BaseModel):
    items: list[HistoryRow]


# ----------------------------------------------------------------- helpers

ERROR_STATUS = {"not_found": 404, "not_pending": 409}


def _error(exc: SuggestionError | HistoryError) -> ApiError:
    return ApiError(
        status_code=ERROR_STATUS.get(exc.code, 422 if exc.code != "already_undone" else 409),
        error=exc.code,
        message=str(exc),
    )


def _row(suggestion: Suggestion) -> SuggestionRow:
    return SuggestionRow(
        id=suggestion.id,
        tipo=suggestion.tipo,
        fuente=suggestion.fuente,
        estado=suggestion.estado,
        resumen=suggestion.resumen,
        choques=len(suggestion.choques),
        creada=suggestion.created_at,
    )


def _origins(session: SessionDep, tenant_id: uuid.UUID, suggestion: Suggestion) -> list[Origin]:
    out: list[Origin] = []
    for conversation_id in suggestion.conversaciones[:5]:
        item = reviews.excerpt_of(session, tenant_id, conversation_id, limit=MAX_EXCERPT)
        if item is None:
            continue
        out.append(
            Origin(
                conversation_id=item.conversation.id,
                cliente=_who(item),
                canal=item.conversation.channel,
                mensajes=_lines(item),
            )
        )
    return out


def _detail(session: SessionDep, tenant_id: uuid.UUID, suggestion: Suggestion) -> SuggestionDetail:
    draft = suggestion.aplicada.get("situation_id")
    return SuggestionDetail(
        **_row(suggestion).model_dump(),
        propuesta=suggestion.propuesta,
        choques_detalle=[Clash.model_validate(c) for c in suggestion.choques],
        origen=_origins(session, tenant_id, suggestion),
        nota=suggestion.nota,
        decidida=suggestion.decidida_en,
        situation_id=uuid.UUID(draft) if draft else None,
    )


def _learning(session: SessionDep, tenant_id: uuid.UUID) -> LearningOut:
    budget = LearningBudget(session, tenant_id)
    settings = budget.settings
    pending = SuggestionRepository(session, tenant_id).pending_by_type()
    return LearningOut(
        activo=settings.activo,
        tope_semanal=settings.tope_semanal,
        usadas=budget.used,
        quedan=budget.left,
        minutos_audio=float(budget.audio_minutes),
        compartir_con_rubro=settings.compartir_con_rubro,
        pendientes=pending,
        total_pendientes=sum(pending.values()),
    )


def _suggestion(session: SessionDep, tenant_id: uuid.UUID, suggestion_id: uuid.UUID) -> Suggestion:
    row = SuggestionRepository(session, tenant_id).get_for_update(suggestion_id)
    if row is None:
        raise ApiError(status_code=404, error="not_found", message="Not found.")
    return row


# ---------------------------------------------------------------- settings


@router.get(
    "/learning",
    summary="Learning: on or off, what it spent, what waits",
    responses=error_responses(*AUTH),
)
def get_learning(principal: ReviewerDep, session: SessionDep) -> LearningOut:
    return _learning(session, principal.tenant_id)


@router.put(
    "/learning",
    summary="Turn learning on, set its weekly ceiling, share with the rubro",
    responses=error_responses(*AUTH),
)
def put_learning(
    body: LearningIn, principal: ConfigurationManagerDep, session: SessionDep
) -> LearningOut:
    from datetime import UTC

    settings = LearningBudget(session, principal.tenant_id).settings
    if body.activo is not None:
        settings.activo = body.activo
    if body.tope_semanal is not None:
        settings.tope_semanal = body.tope_semanal
    if (
        body.compartir_con_rubro is not None
        and body.compartir_con_rubro != settings.compartir_con_rubro
    ):
        settings.compartir_con_rubro = body.compartir_con_rubro
        settings.compartir_por = principal.user_id if body.compartir_con_rubro else None
        settings.compartir_en = datetime.now(UTC) if body.compartir_con_rubro else None
    session.commit()
    return _learning(session, principal.tenant_id)


# ------------------------------------------------------------- suggestions


@router.get("/suggestions", summary="The inbox of suggestions", responses=error_responses(*AUTH))
def list_suggestions(
    principal: ReviewerDep,
    session: SessionDep,
    estado: Annotated[Literal[*SUGGESTION_STATES] | None, Query()] = "pendiente",  # type: ignore[valid-type]
    tipo: Annotated[Literal[*SUGGESTION_TYPES] | None, Query()] = None,  # type: ignore[valid-type]
    fuente: Annotated[Literal[*SUGGESTION_SOURCES] | None, Query()] = None,  # type: ignore[valid-type]
) -> SuggestionList:
    rows = SuggestionRepository(session, principal.tenant_id).listing(estado=estado, tipo=tipo)
    return SuggestionList(items=[_row(r) for r in rows if fuente is None or r.fuente == fuente])


@router.get(
    "/suggestions/{suggestion_id}",
    summary="One suggestion with the conversations that gave rise to it",
    responses=error_responses(NOT_FOUND, *AUTH),
)
def get_suggestion(
    suggestion_id: uuid.UUID, principal: ReviewerDep, session: SessionDep
) -> SuggestionDetail:
    return _detail(
        session, principal.tenant_id, _suggestion(session, principal.tenant_id, suggestion_id)
    )


@router.post(
    "/suggestions/{suggestion_id}/draft",
    summary="Try a suggested situation in the simulator without putting it in force",
    responses=error_responses(NOT_FOUND, *DECISION_ERRORS, *AUTH),
)
async def draft_suggestion(
    suggestion_id: uuid.UUID, principal: ReviewerDep, session: SessionDep, queue: JobQueueDep
) -> DraftOut:
    suggestion = _suggestion(session, principal.tenant_id, suggestion_id)
    service = SuggestionService(session, principal.tenant_id, principal.user_id)
    try:
        situation_id = service.draft(suggestion)
    except SuggestionError as exc:
        raise _error(exc) from exc
    session.commit()
    await _embed_later(queue, principal.tenant_id)
    return DraftOut(situation_id=situation_id)


@router.post(
    "/suggestions/{suggestion_id}/approve",
    summary="Put a suggestion in force, as it is or edited",
    responses=error_responses(NOT_FOUND, *DECISION_ERRORS, *AUTH),
)
async def approve_suggestion(
    suggestion_id: uuid.UUID,
    body: ApproveIn,
    principal: ReviewerDep,
    session: SessionDep,
    queue: JobQueueDep,
) -> DecisionOut:
    suggestion = _suggestion(session, principal.tenant_id, suggestion_id)
    service = SuggestionService(session, principal.tenant_id, principal.user_id)
    try:
        service.approve(suggestion, edited=body.editado, answer=body.respuesta)
    except SuggestionError as exc:
        raise _error(exc) from exc
    session.commit()
    indexed = True
    faq_id = suggestion.aplicada.get("faq_id")
    if faq_id:
        from sqlalchemy import select

        from app.models.sales_config import Faq

        faq = session.execute(
            select(Faq).where(Faq.tenant_id == principal.tenant_id, Faq.id == uuid.UUID(faq_id))
        ).scalar_one()
        try:
            await index_faq(session, principal.tenant_id, queue, faq)
        except Exception:  # noqa: BLE001 - the answer is saved; the indexing can be retried
            logger.warning("a learned fact could not be indexed", exc_info=True)
            session.rollback()
            indexed = False
    if suggestion.aplicada.get("situation_id"):
        await _embed_later(queue, principal.tenant_id)
    return DecisionOut(
        sugerencia=_detail(session, principal.tenant_id, suggestion), indexada=indexed
    )


@router.post(
    "/suggestions/{suggestion_id}/reject",
    summary="Discard a suggestion: it is not proposed again",
    responses=error_responses(NOT_FOUND, (409, "`not_pending`"), *AUTH),
)
def reject_suggestion(
    suggestion_id: uuid.UUID, body: RejectIn, principal: ReviewerDep, session: SessionDep
) -> SuggestionDetail:
    suggestion = _suggestion(session, principal.tenant_id, suggestion_id)
    service = SuggestionService(session, principal.tenant_id, principal.user_id)
    try:
        service.reject(suggestion, body.nota)
    except SuggestionError as exc:
        raise _error(exc) from exc
    session.commit()
    return _detail(session, principal.tenant_id, suggestion)


# ----------------------------------------------------------------- history


@router.get(
    "/history", summary="What changed, with what it was before", responses=error_responses(*AUTH)
)
def list_history(
    principal: ReviewerDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> HistoryList:
    rows = ConfigHistoryRepository(session, principal.tenant_id).recent(limit)
    return HistoryList(
        items=[
            HistoryRow(
                id=r.id,
                entidad=r.entidad,
                accion=r.accion,
                resumen=r.resumen,
                cuando=r.created_at,
                por_sugerencia=r.sugerencia_id is not None,
                deshecho=r.deshecho_por is not None,
                se_puede_deshacer=r.deshecho_por is None
                and r.accion != "deshacer"
                and r.entidad in ("situacion", "interruptor", "perfil", "faq"),
            )
            for r in rows
        ]
    )


@router.post(
    "/history/{entry_id}/undo",
    summary="Put the «before» of a change back",
    responses=error_responses(NOT_FOUND, (409, "`already_undone`"), (422, "`not_undoable`"), *AUTH),
)
async def undo_change(
    entry_id: uuid.UUID, principal: ReviewerDep, session: SessionDep, queue: JobQueueDep
) -> HistoryRow:
    entry = ConfigHistoryRepository(session, principal.tenant_id).get(entry_id)
    if entry is None:
        raise ApiError(status_code=404, error="not_found", message="Not found.")
    try:
        undone = ConfigHistoryService(session, principal.tenant_id, principal.user_id).undo(entry)
    except HistoryError as exc:
        raise _error(exc) from exc
    session.commit()
    if entry.entidad == "situacion":
        await _embed_later(queue, principal.tenant_id)
    from app.process.service import DEFAULT_CACHE

    DEFAULT_CACHE.invalidate(principal.tenant_id)
    return HistoryRow(
        id=undone.id,
        entidad=undone.entidad,
        accion=undone.accion,
        resumen=undone.resumen,
        cuando=undone.created_at,
        por_sugerencia=undone.sugerencia_id is not None,
        deshecho=False,
        se_puede_deshacer=False,
    )
