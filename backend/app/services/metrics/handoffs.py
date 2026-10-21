"""How fast a person answers once the assistant hands a conversation over (feature 019, phase C).

A warm handover is not only the summary the person reads: it is also how long the customer waits
after being told "una persona te escribe". This measures it from the stage event that moves the
conversation to DERIVADO to the first message a person of the team writes in it. Handovers nobody
has answered are counted apart, because the median of the answered ones would hide them.

Live queries over the period's events (hundreds, not millions), apart from the daily rollup.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation import Conversation, Message
from app.models.conversation_state import ConversationStageEvent
from app.services.metrics.rollup import bounds, zone_of

# A reply later than this is another conversation's business, not the answer to this handover.
ANSWER_WINDOW = timedelta(days=7)
MAX_EVENTS = 5000


def handoff_response(
    session: Session, tenant_id: uuid.UUID, start: date, end: date
) -> dict[str, Any]:
    """Handovers of the range, how many a person answered, and how long that took (minutes)."""
    zone = zone_of(session, tenant_id)
    since, _ = bounds(start, zone)
    _, until = bounds(end, zone)
    events = session.execute(
        select(ConversationStageEvent.conversation_id, ConversationStageEvent.created_at)
        .join(Conversation, Conversation.id == ConversationStageEvent.conversation_id)
        .where(
            ConversationStageEvent.tenant_id == tenant_id,
            ConversationStageEvent.to_stage == "DERIVADO",
            ConversationStageEvent.created_at >= since,
            ConversationStageEvent.created_at < until,
            Conversation.channel != "sandbox",
        )
        .order_by(ConversationStageEvent.created_at)
        .limit(MAX_EVENTS)
    ).all()
    replies: dict[uuid.UUID, list[datetime]] = defaultdict(list)
    if events:
        ids = {conversation_id for conversation_id, _ in events}
        for conversation_id, written in session.execute(
            select(Message.conversation_id, Message.created_at)
            .where(
                Message.tenant_id == tenant_id,
                Message.conversation_id.in_(ids),
                Message.direction == "outbound",
                Message.author == "human_operator",
            )
            .order_by(Message.created_at)
        ).all():
            replies[conversation_id].append(written)
    waits: list[float] = []
    for conversation_id, handed_at in events:
        first = next(
            (w for w in replies[conversation_id] if handed_at < w <= handed_at + ANSWER_WINDOW),
            None,
        )
        if first is not None:
            waits.append((first - handed_at).total_seconds() / 60)
    waits.sort()
    return {
        "total": len(events),
        "atendidos": len(waits),
        "sin_atender": len(events) - len(waits),
        "mediana_minutos": round(statistics.median(waits), 1) if waits else None,
        "p90_minutos": round(waits[min(len(waits) - 1, int(len(waits) * 0.9))], 1)
        if waits
        else None,
    }
