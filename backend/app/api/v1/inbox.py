"""The inbox's API (Feature 007, TASK-704 and TASK-706).

Any member of the business works the inbox (RF-008); stealing a conversation from a
colleague and the notification settings are for the owner and admins. Another
business's conversation is a 404, never a 403. Every change publishes an event on
the tenant's channel after it commits (`app/core/events.py`).

* `GET /tenant/conversations`: list with filter, search and cursor.
* `GET /tenant/conversations/{id}`: the header (contact, who has it, window, stage).
* `GET /tenant/conversations/{id}/messages`: the thread, newest page first,
  events included; opening it marks the conversation as read.
* `POST .../takeover | release | steal | close | reopen | opt-out`.
* `POST .../messages`: a person writes as the business (C-002: 403 outside 24 h).
* `GET | POST .../notes`.
* `GET | PUT /tenant/notification-settings`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select

from app.api.deps import (
    MANAGES_CONFIGURATION,
    ConfigurationManagerDep,
    MemberDep,
    SessionDep,
    WhatsAppProviderDep,
    get_storage_provider,
)
from app.api.errors import ApiError, error_responses
from app.core.config import get_settings
from app.core.events import publish
from app.models.conversation import Conversation, Message
from app.models.inbox import NotificationSettings
from app.models.orders import Order
from app.models.user import User
from app.process.facts import FactsLoader
from app.process.inbound import stored_image_of
from app.process.service import ProcessConfigService
from app.process.state import StateRepo
from app.providers.storage.base import StorageProvider
from app.repositories.conversation import MessageRepository
from app.services import inbox as service
from app.services.channel import NoConnectedNumber, ReplySender, SessionWindowClosed
from app.services.inbox_queries import Filter, count_waiting, list_conversations

router = APIRouter(tags=["inbox"])
StorageDep = Annotated[StorageProvider, Depends(get_storage_provider)]

AUTH = (
    (401, "`not_authenticated`, `token_expired` or `invalid_token`"),
    (403, "`email_not_verified`, `no_active_tenant` or `tenant_access_denied`"),
)
NOT_FOUND = (404, "`not_found`")
CONFLICT = (409, "`already_taken` (with `by`), `not_holder`, `closed` or `another_open`")
WINDOW = (403, "`outside_24h_window`: the customer has not written in 24 hours")
BASE = "/tenant/conversations"
THREAD_PAGE = 50
WINDOW_HOURS = 24
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
NoteText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


# ------------------------------------------------------------------ schemas


class Holder(BaseModel):
    id: uuid.UUID
    nombre: str | None


class ContactOut(BaseModel):
    id: uuid.UUID | None
    nombre: str | None
    telefono: str | None
    opt_out: bool
    posible_duplicado: bool


class ListItem(BaseModel):
    id: uuid.UUID
    canal: str
    estado: str
    contacto: ContactOut
    ultimo_mensaje: str | None
    ultimo_autor: str | None
    ultimo_en: datetime | None
    no_leidos: int
    tomada_por: Holder | None
    necesita_humano: bool
    etapa: str | None


class ConversationPage(BaseModel):
    items: list[ListItem]
    siguiente: str | None
    esperando: int


class Window(BaseModel):
    abierta: bool
    cierra_en: datetime | None
    por_cerrar: bool


class HandoverOut(BaseModel):
    motivo: str
    resumen: str | None
    hasta: datetime


class ConversationDetail(BaseModel):
    id: uuid.UUID
    canal: str
    estado: str
    contacto: ContactOut
    tomada_por: Holder | None
    tomada_desde: datetime | None
    derivado: HandoverOut | None
    etapa: str | None
    ventana: Window
    pedido_id: uuid.UUID | None


class MessageOut(BaseModel):
    id: uuid.UUID
    autor: str
    tipo: str
    texto: str
    en: datetime
    estado_entrega: str | None
    operador: str | None = None
    evento: str | None = None
    descartado: bool = False
    opciones: list[str] = Field(default_factory=list)
    imagen: bool = False


class ThreadPage(BaseModel):
    items: list[MessageOut]
    anteriores: uuid.UUID | None


class SendBody(BaseModel):
    texto: Text


class SendResult(BaseModel):
    mensaje: MessageOut
    aviso: Literal["enviado", "en_cola", "sin_numero", "simulador"]


class NoteBody(BaseModel):
    texto: NoteText


class NoteOut(BaseModel):
    id: uuid.UUID
    autor: str | None
    texto: str
    en: datetime


class NoteList(BaseModel):
    items: list[NoteOut]


class SettingsBody(BaseModel):
    unattended_queue_minutes: int = Field(ge=1, le=1440)
    auto_return_minutes: int | None = Field(default=None, ge=5, le=1440)


class SettingsOut(SettingsBody):
    pass


class ImageLink(BaseModel):
    url: str
    expira_en: int


# ------------------------------------------------------------------ helpers


def _error(exc: service.InboxError) -> ApiError:
    if isinstance(exc, service.NotFound):
        return ApiError(status_code=404, error="not_found", message="Conversation not found.")
    if isinstance(exc, service.AlreadyTaken):
        return ApiError(
            status_code=status.HTTP_409_CONFLICT,
            error="already_taken",
            message=f"{exc.by or 'Otra persona'} ya tiene esta conversación.",
            fields={"by": exc.by or ""},
        )
    if isinstance(exc, service.OutsideWindow):
        return ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            error="outside_24h_window",
            message="El cliente no escribe hace más de 24 horas: podrás responder cuando escriba.",
        )
    codes = {
        service.NotHolder: "not_holder",
        service.Closed: "closed",
        service.AnotherOpen: "another_open",
    }
    return ApiError(
        status_code=status.HTTP_409_CONFLICT,
        error=codes.get(type(exc), "conflict"),
        message=str(exc),
    )


def _contact(inbox: service.Inbox, conversation: Conversation) -> ContactOut:
    contact = inbox.contact(conversation)
    if contact is None:
        return ContactOut(
            id=None, nombre=None, telefono=None, opt_out=False, posible_duplicado=False
        )
    name = contact.custom_data.get("nombres") if isinstance(contact.custom_data, dict) else None
    return ContactOut(
        id=contact.id,
        nombre=name or contact.profile_name,
        telefono=contact.phone_e164,
        opt_out=contact.opted_out_at is not None,
        posible_duplicado=contact.posible_duplicado_de is not None,
    )


def _holder(session: SessionDep, conversation: Conversation) -> Holder | None:
    if conversation.takeover_user_id is None:
        return None
    user = session.get(User, conversation.takeover_user_id)
    return Holder(id=conversation.takeover_user_id, nombre=user.full_name if user else None)


def _message(message: Message) -> MessageOut:
    payload: dict[str, Any] = message.payload or {}
    options = [str(b.get("title", "")) for b in payload.get("botones") or [] if isinstance(b, dict)]
    for section in payload.get("secciones") or payload.get("filas") or []:
        if isinstance(section, dict):
            for row in section.get("rows") or section.get("filas") or []:
                if isinstance(row, dict):
                    options.append(str(row.get("title", "")))
    return MessageOut(
        id=message.id,
        autor=message.author,
        tipo=message.msg_type,
        texto=message.content,
        en=message.created_at,
        estado_entrega=message.delivery_status,
        operador=payload.get("operador"),
        evento=payload.get("evento"),
        descartado=payload.get("descartado") is not None,
        opciones=options,
        imagen=stored_image_of(payload) is not None,
    )


def _detail(
    session: SessionDep, tenant_id: uuid.UUID, conversation: Conversation
) -> ConversationDetail:
    inbox = service.Inbox(session, tenant_id)
    now = datetime.now(UTC)
    config = ProcessConfigService(session).effective(tenant_id)
    facts = FactsLoader(session, tenant_id, config.facts_config()).load(conversation, now=now)
    state = StateRepo(session, tenant_id).get(conversation.id)
    contact = inbox.contact(conversation)
    closes: datetime | None = None
    if conversation.channel == "whatsapp" and contact is not None and contact.last_inbound_at:
        closes = contact.last_inbound_at + timedelta(hours=WINDOW_HOURS)
    open_ = inbox.window_open(conversation)
    order = session.execute(
        select(Order.id)
        .where(Order.tenant_id == tenant_id, Order.conversation_id == conversation.id)
        .order_by(Order.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    handover = facts.derivado if facts.derivado_activo(now) else None
    return ConversationDetail(
        id=conversation.id,
        canal=conversation.channel,
        estado=conversation.status,
        contacto=_contact(inbox, conversation),
        tomada_por=_holder(session, conversation),
        tomada_desde=conversation.takeover_started_at,
        derivado=HandoverOut(**handover.model_dump()) if handover is not None else None,
        etapa=state.stage_key if state else None,
        ventana=Window(
            abierta=open_,
            cierra_en=closes,
            por_cerrar=bool(open_ and closes and closes - now < timedelta(hours=4)),
        ),
        pedido_id=order,
    )


def _changed(tenant_id: uuid.UUID, conversation_id: uuid.UUID, **extra: Any) -> None:
    publish(tenant_id, "conversation.updated", conversation_id=conversation_id, **extra)


# ------------------------------------------------------------------ list and read


@router.get(BASE, summary="The inbox", responses=error_responses(*AUTH))
def list_inbox(
    principal: MemberDep,
    session: SessionDep,
    filtro: Filter = "todas",
    q: Annotated[str | None, Query(max_length=100)] = None,
    pruebas: bool = False,
    cursor: str | None = None,
) -> ConversationPage:
    now = datetime.now(UTC)
    rows, next_cursor = list_conversations(
        session,
        principal.tenant_id,
        user_id=principal.user_id,
        now=now,
        filtro=filtro,
        q=q,
        sandbox=pruebas,
        cursor=cursor,
    )
    inbox = service.Inbox(session, principal.tenant_id)
    items = [
        ListItem(
            id=row.conversation.id,
            canal=row.conversation.channel,
            estado=row.conversation.status,
            contacto=_contact(inbox, row.conversation),
            ultimo_mensaje=row.last_text,
            ultimo_autor=row.last_author,
            ultimo_en=row.conversation.last_message_at,
            no_leidos=row.conversation.unread_count,
            tomada_por=_holder(session, row.conversation),
            necesita_humano=row.handed_over and row.conversation.takeover_user_id is None,
            etapa=row.stage,
        )
        for row in rows
    ]
    return ConversationPage(
        items=items,
        siguiente=next_cursor,
        esperando=count_waiting(session, principal.tenant_id, now=now),
    )


@router.get(
    BASE + "/{conversation_id}",
    summary="One conversation's header",
    responses=error_responses(*AUTH, NOT_FOUND),
)
def conversation_detail(
    conversation_id: uuid.UUID, principal: MemberDep, session: SessionDep
) -> ConversationDetail:
    inbox = service.Inbox(session, principal.tenant_id)
    try:
        conversation = inbox.conversation(conversation_id)
    except service.InboxError as exc:
        raise _error(exc) from exc
    return _detail(session, principal.tenant_id, conversation)


@router.get(
    BASE + "/{conversation_id}/messages",
    summary="The thread, newest page first; opening it marks it read",
    responses=error_responses(*AUTH, NOT_FOUND),
)
def conversation_thread(
    conversation_id: uuid.UUID,
    principal: MemberDep,
    session: SessionDep,
    antes_de: uuid.UUID | None = None,
) -> ThreadPage:
    inbox = service.Inbox(session, principal.tenant_id)
    try:
        conversation = inbox.conversation(conversation_id)
    except service.InboxError as exc:
        raise _error(exc) from exc
    messages = list(
        MessageRepository(session, principal.tenant_id).list_for_conversation(
            conversation.id, include_events=True
        )
    )
    if antes_de is not None:
        index = next((i for i, m in enumerate(messages) if m.id == antes_de), len(messages))
        messages = messages[:index]
    page = messages[-THREAD_PAGE:]
    earlier = page[0].id if len(messages) > THREAD_PAGE and page else None
    if antes_de is None and conversation.unread_count:
        inbox.mark_read(conversation)
        session.commit()
        _changed(principal.tenant_id, conversation.id)
    return ThreadPage(items=[_message(m) for m in page], anteriores=earlier)


@router.get(
    BASE + "/{conversation_id}/messages/{message_id}/image",
    summary="A signed link to an image the customer sent (10 minutes)",
    responses=error_responses(*AUTH, NOT_FOUND),
)
def message_image(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    principal: MemberDep,
    session: SessionDep,
    storage: StorageDep,
) -> ImageLink:
    message = MessageRepository(session, principal.tenant_id).get(message_id)
    stored = stored_image_of(message.payload) if message is not None else None
    if message is None or message.conversation_id != conversation_id or stored is None:
        raise ApiError(status_code=404, error="not_found", message="Image not found.")
    return ImageLink(url=storage.presigned_url(stored[0], expires_in=600), expira_en=600)


# ------------------------------------------------------------------ takeover


@router.post(
    BASE + "/{conversation_id}/takeover",
    summary="Take the conversation: the assistant stops at once",
    responses=error_responses(*AUTH, NOT_FOUND, CONFLICT),
)
def take_conversation(
    conversation_id: uuid.UUID, principal: MemberDep, session: SessionDep
) -> ConversationDetail:
    try:
        conversation = service.Inbox(session, principal.tenant_id).take(
            conversation_id, principal.user
        )
    except service.InboxError as exc:
        raise _error(exc) from exc
    session.commit()
    _changed(principal.tenant_id, conversation.id, por=principal.user_id)
    return _detail(session, principal.tenant_id, conversation)


@router.post(
    BASE + "/{conversation_id}/release",
    summary="Give the conversation back to the assistant",
    responses=error_responses(*AUTH, NOT_FOUND, CONFLICT),
)
def release_conversation(
    conversation_id: uuid.UUID, principal: MemberDep, session: SessionDep
) -> ConversationDetail:
    try:
        conversation = service.Inbox(session, principal.tenant_id).release(
            conversation_id, principal.user, may_force=principal.role in MANAGES_CONFIGURATION
        )
    except service.InboxError as exc:
        raise _error(exc) from exc
    session.commit()
    _changed(principal.tenant_id, conversation.id)
    return _detail(session, principal.tenant_id, conversation)


@router.post(
    BASE + "/{conversation_id}/steal",
    summary="Take a conversation a colleague has (owner or admin)",
    responses=error_responses(*AUTH, NOT_FOUND, CONFLICT),
)
def steal_conversation(
    conversation_id: uuid.UUID, principal: ConfigurationManagerDep, session: SessionDep
) -> ConversationDetail:
    try:
        conversation, previous = service.Inbox(session, principal.tenant_id).steal(
            conversation_id, principal.user
        )
    except service.InboxError as exc:
        raise _error(exc) from exc
    session.commit()
    _changed(principal.tenant_id, conversation.id, por=principal.user_id, anterior=previous)
    return _detail(session, principal.tenant_id, conversation)


# ------------------------------------------------------------------ writing


@router.post(
    BASE + "/{conversation_id}/messages",
    summary="Write to the customer as the business",
    responses=error_responses(*AUTH, NOT_FOUND, CONFLICT, WINDOW),
)
async def send_message(
    conversation_id: uuid.UUID,
    body: SendBody,
    principal: MemberDep,
    session: SessionDep,
    whatsapp: WhatsAppProviderDep,
) -> SendResult:
    try:
        message = service.Inbox(session, principal.tenant_id).send(
            conversation_id, principal.user, body.texto
        )
    except service.InboxError as exc:
        raise _error(exc) from exc
    session.commit()
    notice: Literal["enviado", "en_cola", "sin_numero", "simulador"] = "simulador"
    if message.delivery_status == "queued":
        sender = ReplySender(
            session,
            principal.tenant_id,
            provider=whatsapp,
            max_attempts=get_settings().channel_send_max_attempts,
        )
        try:
            await sender.send(message)
            notice = "enviado" if message.delivery_status != "queued" else "en_cola"
        except NoConnectedNumber:
            session.rollback()
            notice = "sin_numero"
        except SessionWindowClosed:
            session.rollback()
            raise _error(service.OutsideWindow(str(conversation_id))) from None
        session.commit()
    publish(principal.tenant_id, "message.new", conversation_id=conversation_id)
    return SendResult(mensaje=_message(message), aviso=notice)


@router.get(
    BASE + "/{conversation_id}/notes",
    summary="The team's internal notes",
    responses=error_responses(*AUTH, NOT_FOUND),
)
def list_notes(conversation_id: uuid.UUID, principal: MemberDep, session: SessionDep) -> NoteList:
    try:
        notes = service.Inbox(session, principal.tenant_id).notes(conversation_id)
    except service.InboxError as exc:
        raise _error(exc) from exc
    authors = {n.author_id for n in notes}
    names = (
        {u.id: u.full_name for u in session.query(User).filter(User.id.in_(authors))}
        if authors
        else {}
    )
    return NoteList(
        items=[
            NoteOut(id=n.id, autor=names.get(n.author_id), texto=n.content, en=n.created_at)
            for n in notes
        ]
    )


@router.post(
    BASE + "/{conversation_id}/notes",
    status_code=status.HTTP_201_CREATED,
    summary="Add an internal note (never sent to the customer)",
    responses=error_responses(*AUTH, NOT_FOUND),
)
def add_note(
    conversation_id: uuid.UUID, body: NoteBody, principal: MemberDep, session: SessionDep
) -> NoteOut:
    try:
        note = service.Inbox(session, principal.tenant_id).note(
            conversation_id, principal.user, body.texto
        )
    except service.InboxError as exc:
        raise _error(exc) from exc
    session.commit()
    _changed(principal.tenant_id, conversation_id)
    return NoteOut(
        id=note.id, autor=principal.user.full_name, texto=note.content, en=note.created_at
    )


# ------------------------------------------------------------------ status


def _status_action(
    action: Literal["close", "reopen", "opt_out"],
    conversation_id: uuid.UUID,
    principal: Any,
    session: SessionDep,
) -> ConversationDetail:
    inbox = service.Inbox(session, principal.tenant_id)
    try:
        if action == "close":
            conversation = inbox.close(conversation_id, principal.user)
        elif action == "reopen":
            conversation = inbox.reopen(conversation_id, principal.user)
        else:
            inbox.opt_out(conversation_id, principal.user)
            conversation = inbox.conversation(conversation_id)
    except service.InboxError as exc:
        raise _error(exc) from exc
    session.commit()
    _changed(principal.tenant_id, conversation.id)
    return _detail(session, principal.tenant_id, conversation)


@router.post(BASE + "/{conversation_id}/close", responses=error_responses(*AUTH, NOT_FOUND))
def close_conversation(
    conversation_id: uuid.UUID, principal: MemberDep, session: SessionDep
) -> ConversationDetail:
    return _status_action("close", conversation_id, principal, session)


@router.post(
    BASE + "/{conversation_id}/reopen", responses=error_responses(*AUTH, NOT_FOUND, CONFLICT)
)
def reopen_conversation(
    conversation_id: uuid.UUID, principal: MemberDep, session: SessionDep
) -> ConversationDetail:
    return _status_action("reopen", conversation_id, principal, session)


@router.post(BASE + "/{conversation_id}/opt-out", responses=error_responses(*AUTH, NOT_FOUND))
def opt_out_contact(
    conversation_id: uuid.UUID, principal: MemberDep, session: SessionDep
) -> ConversationDetail:
    return _status_action("opt_out", conversation_id, principal, session)


# ------------------------------------------------------------------ settings


@router.get("/tenant/notification-settings", responses=error_responses(*AUTH))
def get_settings_(principal: MemberDep, session: SessionDep) -> SettingsOut:
    row = service.settings_for(session, principal.tenant_id)
    return SettingsOut(
        unattended_queue_minutes=row.unattended_queue_minutes,
        auto_return_minutes=row.auto_return_minutes,
    )


@router.put("/tenant/notification-settings", responses=error_responses(*AUTH))
def put_settings(
    body: SettingsBody, principal: ConfigurationManagerDep, session: SessionDep
) -> SettingsOut:
    row = session.get(NotificationSettings, principal.tenant_id)
    if row is None:
        row = NotificationSettings(tenant_id=principal.tenant_id)
        session.add(row)
    row.unattended_queue_minutes = body.unattended_queue_minutes
    row.auto_return_minutes = body.auto_return_minutes
    row.updated_at = datetime.now(UTC)
    session.commit()
    return SettingsOut(
        unattended_queue_minutes=row.unattended_queue_minutes,
        auto_return_minutes=row.auto_return_minutes,
    )
