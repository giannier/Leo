"""What the inbox lists (Feature 007, TASK-704 and TASK-706).

One query per page, always scoped to the tenant, ordered by the last message
(`last_message_at`, then id) with a cursor of those two values, so a conversation
that moves while somebody scrolls is neither lost nor shown twice on the next page.

Filters (HU-01):

* `todas`: every open conversation.
* `agente`: open, nobody has it, not waiting for a person.
* `necesitan_humano`: the assistant handed it over (`derivado` in force) and nobody
  has taken it yet (HU-03's queue).
* `tomadas`: somebody has it (`mias` narrows to the caller).
* `cerradas`.

The search (`q`) matches the contact's name or phone, or the words of any message of
the conversation through the Spanish full-text index (`ix_messages_content_search`).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, and_, cast, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.channel import Contact
from app.models.conversation import Conversation, Message
from app.models.conversation_state import ConversationState
from app.repositories.conversation import EVENT

Filter = Literal["todas", "agente", "necesitan_humano", "tomadas", "mias", "cerradas"]
PAGE = 30


@dataclass(frozen=True)
class Row:
    conversation: Conversation
    contact: Contact | None
    stage: str | None
    handed_over: bool
    last_text: str | None
    last_author: str | None


def _handed_over(now: datetime):  # type: ignore[no-untyped-def]
    hasta = cast(ConversationState.facts["derivado"]["hasta"].astext, DateTime(timezone=True))
    return and_(ConversationState.facts.has_key("derivado"), hasta > now)


def encode_cursor(conversation: Conversation) -> str:
    moment = conversation.last_message_at or conversation.created_at
    return f"{moment.isoformat()}|{conversation.id}"


def decode_cursor(raw: str) -> tuple[datetime, uuid.UUID] | None:
    try:
        moment, ident = raw.split("|", 1)
        return datetime.fromisoformat(moment), uuid.UUID(ident)
    except ValueError:
        return None


def list_conversations(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    now: datetime,
    filtro: Filter = "todas",
    q: str | None = None,
    sandbox: bool = False,
    cursor: str | None = None,
    limit: int = PAGE,
) -> tuple[list[Row], str | None]:
    handed = _handed_over(now)
    statement = (
        select(Conversation, Contact, ConversationState.stage_key, handed)
        .outerjoin(
            Contact, and_(Contact.id == Conversation.contact_id, Contact.tenant_id == tenant_id)
        )
        .outerjoin(
            ConversationState,
            and_(
                ConversationState.conversation_id == Conversation.id,
                ConversationState.tenant_id == tenant_id,
            ),
        )
        .where(
            Conversation.tenant_id == tenant_id,
            Conversation.channel == ("sandbox" if sandbox else "whatsapp"),
        )
    )
    if filtro == "cerradas":
        statement = statement.where(Conversation.status == "closed")
    else:
        statement = statement.where(Conversation.status == "open")
    if filtro == "agente":
        statement = statement.where(
            Conversation.takeover_user_id.is_(None), or_(ConversationState.facts.is_(None), ~handed)
        )
    elif filtro == "necesitan_humano":
        statement = statement.where(Conversation.takeover_user_id.is_(None), handed)
    elif filtro == "tomadas":
        statement = statement.where(Conversation.takeover_user_id.is_not(None))
    elif filtro == "mias":
        statement = statement.where(Conversation.takeover_user_id == user_id)

    words = " ".join((q or "").split())
    if words:
        like = f"%{words}%"
        in_messages = exists().where(
            Message.tenant_id == tenant_id,
            Message.conversation_id == Conversation.id,
            func.to_tsvector("spanish", Message.content).op("@@")(
                func.plainto_tsquery("spanish", words)
            ),
        )
        statement = statement.where(
            or_(Contact.profile_name.ilike(like), Contact.phone_e164.ilike(like), in_messages)
        )

    order_key = func.coalesce(Conversation.last_message_at, Conversation.created_at)
    decoded = decode_cursor(cursor) if cursor else None
    if decoded is not None:
        moment, ident = decoded
        statement = statement.where(
            or_(order_key < moment, and_(order_key == moment, Conversation.id < ident))
        )
    statement = statement.order_by(order_key.desc(), Conversation.id.desc()).limit(limit + 1)

    rows = session.execute(statement).all()
    page = rows[:limit]
    last = _last_messages(session, tenant_id, [r[0].id for r in page])
    out = [
        Row(
            conversation=r[0],
            contact=r[1],
            stage=r[2],
            handed_over=bool(r[3]),
            last_text=last.get(r[0].id, (None, None))[0],
            last_author=last.get(r[0].id, (None, None))[1],
        )
        for r in page
    ]
    next_cursor = encode_cursor(page[-1][0]) if len(rows) > limit and page else None
    return out, next_cursor


def _last_messages(
    session: Session, tenant_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str]]:
    if not ids:
        return {}
    statement = (
        select(Message.conversation_id, Message.content, Message.author)
        .where(
            Message.tenant_id == tenant_id,
            Message.conversation_id.in_(ids),
            Message.msg_type != EVENT,
        )
        .distinct(Message.conversation_id)
        .order_by(Message.conversation_id, Message.created_at.desc())
    )
    return {row[0]: (row[1], row[2]) for row in session.execute(statement).all()}


def count_waiting(session: Session, tenant_id: uuid.UUID, *, now: datetime) -> int:
    """How many conversations wait for a person (the queue's badge)."""
    statement = (
        select(func.count())
        .select_from(Conversation)
        .join(
            ConversationState,
            and_(
                ConversationState.conversation_id == Conversation.id,
                ConversationState.tenant_id == tenant_id,
            ),
        )
        .where(
            Conversation.tenant_id == tenant_id,
            Conversation.channel == "whatsapp",
            Conversation.status == "open",
            Conversation.takeover_user_id.is_(None),
            _handed_over(now),
        )
    )
    return int(session.execute(statement).scalar_one())
