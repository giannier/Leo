"""The weekly review of real conversations (feature 019, phase C: quality that needs no model call).

Every week the business looks at a sample of what the assistant really said and says whether it
was good: bien, regular or mal, and why. The sample is **the same all week** (conversations
ordered by a hash of their id and the week), so refreshing the page does not change what there is
to review, and a rated conversation stays in the sample with its mark. Only conversations of a
real customer where the assistant spoke are sampled.

The ratings feed nothing automatically: they tell the owner whether to trust the assistant with
more of their customers (pilot mode) and which kind of mistake to fix first.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Text, cast, exists, func, select
from sqlalchemy.orm import Session

from app.models.channel import Contact
from app.models.conversation import REAL_CHANNELS, Conversation, Message
from app.models.feedback import ConversationReview

SAMPLE_SIZE = 10
WINDOW = timedelta(days=7)
EXCERPT = 14
RATINGS = (-1, 0, 1)
MOTIVOS = (
    "invento_un_dato",
    "tono",
    "no_resolvio",
    "debio_derivar",
    "muy_largo",
    "repetitivo",
)


class ReviewError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Reviewable:
    conversation: Conversation
    contact: Contact | None
    review: ConversationReview | None
    excerpt: list[Message]


def week_label(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def sample(
    session: Session, tenant_id: uuid.UUID, now: datetime, *, size: int = SAMPLE_SIZE
) -> list[Reviewable]:
    """This week's conversations to review: the same ones all week."""
    since = now - WINDOW
    spoke = exists().where(
        Message.conversation_id == Conversation.id,
        Message.tenant_id == tenant_id,
        Message.direction == "outbound",
        Message.author == "agent",
    )
    heard = exists().where(
        Message.conversation_id == Conversation.id,
        Message.tenant_id == tenant_id,
        Message.direction == "inbound",
    )
    week = week_label(now)
    rows = (
        session.execute(
            select(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.channel.in_(REAL_CHANNELS),
                func.coalesce(Conversation.last_message_at, Conversation.created_at) >= since,
                spoke,
                heard,
            )
            .order_by(func.md5(cast(Conversation.id, Text) + week), Conversation.id)
            .limit(size)
        )
        .scalars()
        .all()
    )
    reviews = {
        r.conversation_id: r
        for r in session.execute(
            select(ConversationReview).where(
                ConversationReview.tenant_id == tenant_id,
                ConversationReview.conversation_id.in_([c.id for c in rows]),
            )
        ).scalars()
    }
    out: list[Reviewable] = []
    for conversation in rows:
        contact = session.get(Contact, conversation.contact_id) if conversation.contact_id else None
        excerpt = _excerpt(session, tenant_id, conversation.id, EXCERPT)
        out.append(Reviewable(conversation, contact, reviews.get(conversation.id), excerpt))
    return out


def _excerpt(
    session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID, limit: int
) -> list[Message]:
    newest = session.execute(
        select(Message)
        .where(
            Message.tenant_id == tenant_id,
            Message.conversation_id == conversation_id,
            Message.msg_type.in_(("text", "interactive", "template")),
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
    ).scalars()
    return list(newest)[::-1]


def excerpt_of(
    session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID, *, limit: int = EXCERPT
) -> Reviewable | None:
    """The last messages of a conversation of this business, to read; None if it is not its own."""
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.tenant_id != tenant_id:
        return None
    contact = session.get(Contact, conversation.contact_id) if conversation.contact_id else None
    return Reviewable(
        conversation, contact, None, _excerpt(session, tenant_id, conversation_id, limit)
    )


def rate(
    session: Session,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    rating: int,
    motivos: list[str],
    nota: str | None,
    user_id: uuid.UUID,
) -> ConversationReview:
    """Rate (or change the rating of) a conversation of this business."""
    if rating not in RATINGS:
        raise ReviewError("invalid_rating", "La calificación es bien, regular o mal.")
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.tenant_id != tenant_id:
        raise ReviewError("not_found", "Esa conversación no existe.")
    clean = [m for m in dict.fromkeys(motivos) if m in MOTIVOS]
    row = session.execute(
        select(ConversationReview).where(ConversationReview.conversation_id == conversation_id)
    ).scalar_one_or_none()
    if row is None:
        row = ConversationReview(tenant_id=tenant_id, conversation_id=conversation_id)
        session.add(row)
    row.rating = rating
    # What went wrong only makes sense for a conversation that was not good.
    row.motivos = clean if rating < 1 else []
    row.nota = (nota or "").strip()[:500] or None
    row.created_by = user_id
    session.flush()
    return row


def summary(
    session: Session, tenant_id: uuid.UUID, now: datetime, *, days: int = 30
) -> dict[str, Any]:
    """How the last `days` days of reviews came out, and what went wrong most."""
    rows = session.execute(
        select(ConversationReview.rating, ConversationReview.motivos).where(
            ConversationReview.tenant_id == tenant_id,
            ConversationReview.updated_at >= now - timedelta(days=days),
        )
    ).all()
    counts: Counter[int] = Counter(r for r, _ in rows)
    faults: Counter[str] = Counter(m for _, motivos in rows for m in motivos)
    total = len(rows)
    return {
        "total": total,
        "bien": counts[1],
        "regular": counts[0],
        "mal": counts[-1],
        "porcentaje_bien": round(100 * counts[1] / total) if total else None,
        "fallas": dict(faults.most_common()),
    }
