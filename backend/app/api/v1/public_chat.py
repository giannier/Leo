"""The public web chat (feature 019, phase F): what a visitor's page calls. No login.

    GET  /public/chat/{key}/config      the business, the assistant, its greeting and colour
    POST /public/chat/{key}/session     a new visitor; returns the session token
    POST /public/chat/{key}/messages    the visitor writes (header `X-Chat-Session`)
    GET  /public/chat/{key}/messages    what the assistant and the team wrote after a given moment

A chat that does not exist and one that is switched off answer the same 404. Every write is
throttled by address, by session and by business (`services/webchat.py` explains why).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Header, Query, Request, status
from pydantic import BaseModel, StringConstraints
from redis.asyncio import Redis
from sqlalchemy import select

from app.api.deps import JobQueueDep, RedisDep, SessionDep
from app.api.errors import ApiError, error_responses
from app.core.config import get_settings
from app.core.events import publish
from app.core.locks import LockStore
from app.core.rate_limit import RedisRateLimiter
from app.models.business import AgentConfig, BusinessProfile
from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.models.web_chat import WebChat
from app.services.webchat import (
    MAX_TEXT,
    WebSession,
    by_public_key,
    issue_token,
    open_session,
    read_token,
)
from app.workers.channel import schedule_answer

router = APIRouter(prefix="/public/chat/{key}", tags=["web chat"])

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT)]
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
NOT_FOUND = ApiError(status_code=404, error="not_found", message="Not found.")


class ChatConfig(BaseModel):
    negocio: str
    asistente: str
    saludo: str | None
    color: str


class SessionOut(BaseModel):
    token: str


class MessageIn(BaseModel):
    texto: Text


class ChatMessage(BaseModel):
    id: uuid.UUID
    de: str  # "asistente" or "equipo"
    texto: str
    en: datetime


class ChatMessages(BaseModel):
    items: list[ChatMessage]


def _address(request: Request) -> str:
    """The visitor's address as the proxy reports it (the last hop in front of this server)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[-1].strip() or (request.client.host if request.client else "?")


async def _allow(redis: Redis, namespace: str, key: str, limit: int, window: int) -> None:
    decision = await RedisRateLimiter(
        redis, limit=limit, window_seconds=window, namespace=namespace
    ).hit(key)
    if not decision.allowed:
        raise ApiError(
            status_code=429,
            error="rate_limited",
            message="Demasiados mensajes seguidos: espera un momento.",
        )


def _chat(session: SessionDep, key: str) -> WebChat:
    chat = by_public_key(session, key)
    if chat is None or not chat.enabled:
        raise NOT_FOUND
    return chat


def _visitor(session: SessionDep, chat: WebChat, token: str | None) -> WebSession:
    claims = read_token(token or "")
    if claims is None or claims.tenant_id != chat.tenant_id:
        raise ApiError(status_code=401, error="invalid_session", message="Abre el chat de nuevo.")
    conversation = session.get(Conversation, claims.conversation_id)
    if (
        conversation is None
        or conversation.tenant_id != chat.tenant_id
        or conversation.channel != "web"
        or conversation.contact_id != claims.contact_id
    ):
        raise ApiError(status_code=401, error="invalid_session", message="Abre el chat de nuevo.")
    return claims


@router.get("/config", responses=error_responses((404, "`not_found`")))
def config(key: str, session: SessionDep) -> ChatConfig:
    chat = _chat(session, key)
    profile = session.get(BusinessProfile, chat.tenant_id)
    agent = session.get(AgentConfig, chat.tenant_id)
    tenant = session.get(Tenant, chat.tenant_id)
    return ChatConfig(
        negocio=(profile.business_name if profile and profile.business_name else None)
        or (tenant.name if tenant else ""),
        asistente=(agent.agent_name if agent else None) or "Leo",
        saludo=chat.saludo,
        color=chat.color,
    )


@router.post(
    "/session",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses((404, "`not_found`"), (429, "`rate_limited`")),
)
async def start(key: str, request: Request, session: SessionDep, redis: RedisDep) -> SessionOut:
    chat = _chat(session, key)
    await _allow(redis, "webchat-open", f"{key}:{_address(request)}", 10, 600)
    tenant = session.get(Tenant, chat.tenant_id)
    if tenant is None:
        raise NOT_FOUND
    visitor = open_session(session, tenant)
    session.commit()
    return SessionOut(token=issue_token(visitor))


@router.post(
    "/messages",
    status_code=status.HTTP_202_ACCEPTED,
    responses=error_responses(
        (401, "`invalid_session`"), (404, "`not_found`"), (429, "`rate_limited`")
    ),
)
async def write(
    key: str,
    body: MessageIn,
    session: SessionDep,
    redis: RedisDep,
    queue: JobQueueDep,
    x_chat_session: Annotated[str | None, Header()] = None,
) -> ChatMessage:
    chat = _chat(session, key)
    visitor = _visitor(session, chat, x_chat_session)
    # Slow enough for a person typing, too slow for a script; then the business's day.
    await _allow(redis, "webchat-session", str(visitor.conversation_id), 12, 60)
    await _allow(redis, "webchat-day", str(chat.tenant_id), chat.limite_diario, 86_400)

    text = CONTROL.sub("", body.texto).strip()
    if not text:
        raise ApiError(status_code=422, error="empty", message="Escribe un mensaje.")
    message = Message(
        tenant_id=chat.tenant_id,
        conversation_id=visitor.conversation_id,
        direction="inbound",
        author="customer",
        content=text,
        msg_type="text",
    )
    session.add(message)
    contact = session.get(Contact, visitor.contact_id)
    conversation = session.get(Conversation, visitor.conversation_id)
    if contact is not None:
        contact.last_inbound_at = datetime.now(UTC)
    if conversation is not None:
        conversation.last_message_at = datetime.now(UTC)
        conversation.unread_count = (conversation.unread_count or 0) + 1
    session.commit()
    publish(chat.tenant_id, "message.new", conversation_id=visitor.conversation_id)
    await schedule_answer(
        queue,
        cast("LockStore", redis),
        tenant_id=chat.tenant_id,
        conversation_id=visitor.conversation_id,
        debounce_seconds=get_settings().channel_debounce_seconds,
    )
    return ChatMessage(id=message.id, de="visitante", texto=text, en=message.created_at)


@router.get(
    "/messages", responses=error_responses((401, "`invalid_session`"), (404, "`not_found`"))
)
def read(
    key: str,
    session: SessionDep,
    x_chat_session: Annotated[str | None, Header()] = None,
    after: Annotated[datetime | None, Query()] = None,
) -> ChatMessages:
    """What the assistant and the team wrote in this visitor's conversation after `after`."""
    chat = _chat(session, key)
    visitor = _visitor(session, chat, x_chat_session)
    statement = (
        select(Message)
        .where(
            Message.tenant_id == chat.tenant_id,
            Message.conversation_id == visitor.conversation_id,
            Message.direction == "outbound",
            Message.author.in_(("agent", "human_operator")),
            Message.msg_type == "text",
        )
        .order_by(Message.created_at, Message.id)
    )
    if after is not None:
        statement = statement.where(Message.created_at > after.astimezone(UTC))
    rows = session.execute(statement.limit(50)).scalars().all()
    return ChatMessages(
        items=[
            ChatMessage(
                id=m.id,
                de="equipo" if m.author == "human_operator" else "asistente",
                texto=m.content,
                en=m.created_at,
            )
            for m in rows
            # A bubble still being written (`queued`) is not shown: the page reads what was said.
            if m.delivery_status in ("sent", "delivered", "read", None)
        ]
    )
