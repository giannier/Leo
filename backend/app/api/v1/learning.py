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
    POST /assistant/situations/redactar            a card from free text, warning of clashes
    POST /assistant/imports/inspect                who wrote in an exported chat (stores nothing)
    POST /assistant/imports                        hand the chat over to learn from it (authorised)
    GET  /assistant/imports                        the chats handed over and what came out
    GET  /assistant/examples                       the real conversations the assistant consults

The reviewers of the business (the owner, the admins and whoever has the permission) read and
decide; turning learning on and its ceiling are for the owner and the admins: every analysis costs.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import BaseModel, Field, StringConstraints

from app.api.deps import (
    ConfigurationManagerDep,
    JobQueueDep,
    PlatformLLMDep,
    RedisDep,
    ReviewerDep,
    SessionDep,
)
from app.api.errors import ApiError, error_responses
from app.api.v1.assistant import _embed_later
from app.api.v1.reviews import Line, _lines, _who
from app.learning.budget import LearningBudget
from app.learning.drafter import DraftError, as_dict, draft_card
from app.learning.history import ConfigHistoryService, HistoryError
from app.learning.importer import CALLS_NEEDED
from app.learning.suggestions import SuggestionError, SuggestionService
from app.learning.whatsapp_export import ExportError, conversations, participants, read_upload
from app.models.learning import SUGGESTION_SOURCES, SUGGESTION_STATES, SUGGESTION_TYPES, Suggestion
from app.repositories.audit import AuditLogRepository
from app.repositories.learning import (
    ChatImportRepository,
    ConfigHistoryRepository,
    ExampleRepository,
    SuggestionRepository,
)
from app.services import reviews
from app.services.faqs import index_faq
from app.workers.chat_import import (
    IMPORT_JOB,
    ORIGINAL_TTL_SECONDS,
    original_key,
    sellers_key,
)

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
    if suggestion.aplicada.get("situation_id") or suggestion.aplicada.get("example_id"):
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
                and r.entidad in ("situacion", "interruptor", "perfil", "faq", "ejemplo", "estilo"),
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


# ---------------------------------------------------------- batch approval


class BatchIn(BaseModel):
    ids: Annotated[list[uuid.UUID], Field(min_length=1, max_length=100)]


class BatchOut(BaseModel):
    aprobadas: int


@router.post(
    "/suggestions/approve-batch",
    summary="Approve several real examples at once",
    responses=error_responses(*AUTH),
)
async def approve_batch(
    body: BatchIn, principal: ReviewerDep, session: SessionDep, queue: JobQueueDep
) -> BatchOut:
    """Examples are many and low-risk, so they are approved together; anything else in the list
    (a situation, a fact) is left for a person to read."""
    repo = SuggestionRepository(session, principal.tenant_id)
    rows = repo.many(body.ids)
    service = SuggestionService(session, principal.tenant_id, principal.user_id)
    done = service.approve_many(rows)
    session.commit()
    if done:
        await _embed_later(queue, principal.tenant_id)
    return BatchOut(aprobadas=len(done))


# ----------------------------------------------------------------- imports

MAX_CHAT_BYTES = 40 * 1024 * 1024


class Participant(BaseModel):
    nombre: str
    mensajes: int


class Inspection(BaseModel):
    participantes: list[Participant]
    mensajes: int
    conversaciones: int
    audios: int
    desde: datetime
    hasta: datetime
    # How many analyses the week still has against what this chat needs.
    analisis_necesarios: int
    analisis_disponibles: int


class ImportRow(BaseModel):
    id: uuid.UUID
    estado: str
    mensajes: int
    conversaciones: int
    audios: int
    audios_transcritos: int
    minutos_audio: float
    sugerencias: int
    error: str | None
    creada: datetime
    terminada: datetime | None


class ImportList(BaseModel):
    items: list[ImportRow]


def _import_row(record: Any) -> ImportRow:
    return ImportRow(
        id=record.id,
        estado=record.estado,
        mensajes=record.mensajes,
        conversaciones=record.conversaciones,
        audios=record.audios,
        audios_transcritos=record.audios_transcritos,
        minutos_audio=float(record.minutos_audio),
        sugerencias=record.sugerencias,
        error=record.error,
        creada=record.creada_en,
        terminada=record.terminada_en,
    )


async def _chat(file: UploadFile) -> bytes:
    data = await file.read(MAX_CHAT_BYTES + 1)
    if len(data) > MAX_CHAT_BYTES:
        raise ApiError(
            status_code=422,
            error="file_too_large",
            message="El chat pesa más de 40 MB: exporta uno más corto o sin archivos.",
        )
    return data


@router.post(
    "/imports/inspect",
    summary="Who wrote in an exported chat (nothing is stored)",
    responses=error_responses(
        (409, "`learning_off`"),
        (422, "`empty_file`, `no_messages`, `bad_zip`, `no_chat`, `zip_too_big`, `file_too_large`"),
        *AUTH,
    ),
)
async def inspect_chat(
    principal: ConfigurationManagerDep, session: SessionDep, file: Annotated[UploadFile, File()]
) -> Inspection:
    """Read in memory and forgotten: the business picks who the sellers are first."""
    budget = LearningBudget(session, principal.tenant_id)
    if not budget.enabled:
        raise ApiError(
            status_code=409,
            error="learning_off",
            message="Enciende el aprendizaje antes de importar un chat.",
        )
    try:
        upload = read_upload(await _chat(file))
    except ExportError as exc:
        raise ApiError(status_code=422, error=exc.code, message=str(exc)) from exc
    return Inspection(
        participantes=[Participant(nombre=n, mensajes=c) for n, c in participants(upload.messages)],
        mensajes=len(upload.messages),
        conversaciones=len(conversations(upload.messages)),
        audios=sum(1 for m in upload.messages if m.kind == "audio"),
        desde=min(m.when for m in upload.messages),
        hasta=max(m.when for m in upload.messages),
        analisis_necesarios=CALLS_NEEDED,
        analisis_disponibles=budget.left,
    )


@router.post(
    "/imports",
    summary="Hand a chat over to learn from it",
    responses=error_responses(
        (409, "`learning_off`, `budget`, `already_running`"),
        (422, "`authorization_required`, `sellers_required`, `file_too_large`, or the file's own"),
        *AUTH,
    ),
)
async def start_import(
    principal: ConfigurationManagerDep,
    session: SessionDep,
    queue: JobQueueDep,
    redis: RedisDep,
    file: Annotated[UploadFile, File()],
    vendedores: Annotated[str, Form()],
    autorizo: Annotated[bool, Form()],
) -> ImportRow:
    """The business authorises the use of the chat (recorded with who and when). The original waits
    in a temporary store for the worker and is deleted when it is processed, also if that fails."""
    if not autorizo:
        raise ApiError(
            status_code=422,
            error="authorization_required",
            message="Debes autorizar el uso de estos chats para que el asistente aprenda de ellos.",
        )
    try:
        sellers = [str(n) for n in json.loads(vendedores) if str(n).strip()]
    except ValueError:
        sellers = []
    if not sellers:
        raise ApiError(
            status_code=422, error="sellers_required", message="Elige quién es el vendedor."
        )
    budget = LearningBudget(session, principal.tenant_id)
    if not budget.enabled:
        raise ApiError(
            status_code=409, error="learning_off", message="El aprendizaje está apagado."
        )
    if budget.left < CALLS_NEEDED:
        raise ApiError(
            status_code=409,
            error="budget",
            message=(
                f"Este chat necesita {CALLS_NEEDED} análisis y esta semana quedan {budget.left}."
            ),
        )
    imports = ChatImportRepository(session, principal.tenant_id)
    if imports.running() is not None:
        raise ApiError(
            status_code=409, error="already_running", message="Ya hay un chat procesándose."
        )
    data = await _chat(file)
    try:
        read_upload(data)  # refuse a file that is not a chat before anything is kept
    except ExportError as exc:
        raise ApiError(status_code=422, error=exc.code, message=str(exc)) from exc

    record = imports.add(autorizado_por=principal.user_id)
    AuditLogRepository(session, principal.tenant_id).record(
        actor_id=principal.user_id,
        action="learning.import_authorized",
        detail={"import_id": str(record.id)},
    )
    session.commit()
    await redis.set(original_key(record.id), data, ex=ORIGINAL_TTL_SECONDS)
    await redis.set(sellers_key(record.id), json.dumps(sellers), ex=ORIGINAL_TTL_SECONDS)
    try:
        await queue.enqueue(IMPORT_JOB, str(record.id), str(principal.tenant_id))
    except Exception as exc:  # noqa: BLE001 - nothing is processing: do not leave the original around
        await redis.delete(original_key(record.id), sellers_key(record.id))
        record.estado = "fallido"
        record.error = "No se pudo poner en marcha el proceso. Inténtalo de nuevo."
        session.commit()
        raise ApiError(
            status_code=503, error="queue_unavailable", message="No se pudo iniciar el proceso."
        ) from exc
    return _import_row(record)


@router.get("/imports", summary="The chats handed over", responses=error_responses(*AUTH))
def list_imports(principal: ConfigurationManagerDep, session: SessionDep) -> ImportList:
    rows = ChatImportRepository(session, principal.tenant_id).listing()
    return ImportList(items=[_import_row(r) for r in rows])


@router.get(
    "/imports/{import_id}",
    summary="One chat handed over (to follow it while it is processed)",
    responses=error_responses(NOT_FOUND, *AUTH),
)
def get_import(
    import_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> ImportRow:
    record = ChatImportRepository(session, principal.tenant_id).get(import_id)
    if record is None:
        raise ApiError(status_code=404, error="not_found", message="Not found.")
    return _import_row(record)


# ---------------------------------------------------------------- examples


class ExampleOut(BaseModel):
    id: uuid.UUID
    titulo: str
    tema: str
    origen: str
    estado: str
    mensajes: list[dict[str, str]]
    creado: datetime


class ExampleList(BaseModel):
    items: list[ExampleOut]


class ExampleState(BaseModel):
    activo: bool


def _example_out(example: Any) -> ExampleOut:
    return ExampleOut(
        id=example.id,
        titulo=example.titulo,
        tema=example.tema,
        origen=example.origen,
        estado=example.estado,
        mensajes=[{"de": str(m.get("de")), "texto": str(m.get("texto"))} for m in example.mensajes],
        creado=example.created_at,
    )


@router.get(
    "/examples",
    summary="The real conversations the assistant consults",
    responses=error_responses(*AUTH),
)
def list_examples(principal: ReviewerDep, session: SessionDep) -> ExampleList:
    rows = ExampleRepository(session, principal.tenant_id).listing()
    return ExampleList(items=[_example_out(r) for r in rows])


@router.put(
    "/examples/{example_id}/estado",
    summary="Turn a real example on or off",
    responses=error_responses(NOT_FOUND, *AUTH),
)
def set_example_state(
    example_id: uuid.UUID, body: ExampleState, principal: ReviewerDep, session: SessionDep
) -> ExampleOut:
    example = ExampleRepository(session, principal.tenant_id).get(example_id)
    if example is None:
        raise ApiError(status_code=404, error="not_found", message="Not found.")
    example.estado = "activo" if body.activo else "inactivo"
    session.commit()
    return _example_out(example)


@router.delete(
    "/examples/{example_id}",
    status_code=204,
    summary="Remove a real example",
    responses=error_responses(NOT_FOUND, *AUTH),
)
def delete_example(example_id: uuid.UUID, principal: ReviewerDep, session: SessionDep) -> None:
    example = ExampleRepository(session, principal.tenant_id).get(example_id)
    if example is None:
        raise ApiError(status_code=404, error="not_found", message="Not found.")
    session.delete(example)
    session.commit()


# ------------------------------------------------------------ the assisted writer


class DraftIn(BaseModel):
    texto: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class DraftClash(BaseModel):
    con: str
    tipo: str
    motivo: str


class CardDraft(BaseModel):
    titulo: str
    cuando_aplica: str
    que_hace: str
    derivar: bool
    siempre: bool
    choques: list[DraftClash]
    avisos: list[str]
    quedan: int


@router.post(
    "/situations/redactar",
    summary="Turn a rule written in free words into a card, warning of what it clashes with",
    responses=error_responses(
        (409, "`learning_off`, `budget`"),
        (422, "`too_short`, `too_long`, `unreadable`"),
        *AUTH,
    ),
)
async def redact_situation(
    body: DraftIn, principal: ConfigurationManagerDep, session: SessionDep, llm: PlatformLLMDep
) -> CardDraft:
    """Nothing is saved: the card goes back to the form for the owner to read and save."""
    try:
        draft = await draft_card(session, principal.tenant_id, llm, body.texto)
    except DraftError as exc:
        status_code = 409 if exc.code in ("learning_off", "budget") else 422
        raise ApiError(status_code=status_code, error=exc.code, message=str(exc)) from exc
    session.commit()
    return CardDraft(**as_dict(draft), quedan=LearningBudget(session, principal.tenant_id).left)
