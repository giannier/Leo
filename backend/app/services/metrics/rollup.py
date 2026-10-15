"""Daily metrics per business (Feature 010, TASK-1002).

One row per business per **local** day, in the business's own time zone (its
profile; America/Lima by default): "today" for a bakery in Lima starts at its
midnight, not UTC's. Only the real channel counts (WhatsApp): the simulator is
where the business tries the product, not work the agent did.

What each number means, so the panel never inflates (C-001):

* conversations: those with at least one message that day;
  `with_person` if a person of the business wrote or took it that day,
  `agent_only` the rest.
* first response: for each conversation whose first message ever arrived that
  day, the seconds until the first answer (agent or person).
* handovers: conversations the agent handed to a person that day, by reason.
* orders and sales: orders confirmed that day (`confirmed_at`), outside the
  simulator; sales summed **per currency**, never across.
* tokens and cost: every agent trace of the day (the simulator included: it
  costs the platform the same), cost from the price in force (`model_costs`).

`store_day` replaces the row, so recomputing a day is always safe; the nightly
job recomputes the last few days to catch late events.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.models.business import BusinessProfile
from app.models.conversation import AgentTrace, Conversation, Message
from app.models.conversation_state import ConversationStageEvent
from app.models.metrics import MetricsDaily
from app.models.orders import Order
from app.models.tenant import Tenant

DEFAULT_ZONE = "America/Lima"
CHANNEL = "whatsapp"
RECOMPUTE_DAYS = 3
MAX_BACKFILL_DAYS = 400
TAKEN_EVENTS = ("tomada", "robada")


@dataclass
class DayMetrics:
    day: date
    conversations: int = 0
    conversations_agent_only: int = 0
    conversations_with_person: int = 0
    messages_in: int = 0
    messages_out_agent: int = 0
    messages_out_person: int = 0
    first_responses: int = 0
    first_response_seconds: int = 0
    handovers: int = 0
    handover_reasons: dict[str, int] = field(default_factory=dict)
    orders_confirmed: int = 0
    sales: dict[str, str] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = Decimal("0")

    def columns(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "day"}


def zone_of(session: Session, tenant_id: uuid.UUID) -> ZoneInfo:
    name = session.execute(
        select(BusinessProfile.timezone).where(BusinessProfile.tenant_id == tenant_id)
    ).scalar_one_or_none()
    try:
        return ZoneInfo(name or DEFAULT_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_ZONE)


def bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """The local day as a UTC interval [start, end)."""
    start = datetime.combine(day, time.min, tzinfo=zone)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def local_today(now: datetime, zone: ZoneInfo) -> date:
    return now.astimezone(zone).date()


def compute_day(session: Session, tenant_id: uuid.UUID, day: date, zone: ZoneInfo) -> DayMetrics:
    start, end = bounds(day, zone)
    out = DayMetrics(day=day)

    real = and_(
        Message.tenant_id == tenant_id,
        Message.created_at >= start,
        Message.created_at < end,
        Conversation.channel == CHANNEL,
    )
    rows = session.execute(
        select(
            Message.conversation_id,
            Message.direction,
            Message.author,
            Message.msg_type,
            Message.payload,
        )
        .join(
            Conversation,
            and_(Conversation.id == Message.conversation_id, Conversation.tenant_id == tenant_id),
        )
        .where(real)
    ).all()
    talked: set[uuid.UUID] = set()
    person: set[uuid.UUID] = set()
    for conversation_id, direction, author, msg_type, payload in rows:
        if msg_type == "event":
            if isinstance(payload, dict) and payload.get("evento") in TAKEN_EVENTS:
                person.add(conversation_id)
            continue
        talked.add(conversation_id)
        if direction == "inbound":
            out.messages_in += 1
        elif author == "agent":
            out.messages_out_agent += 1
        elif author == "human_operator":
            out.messages_out_person += 1
            person.add(conversation_id)
    out.conversations = len(talked)
    out.conversations_with_person = len(person & talked)
    out.conversations_agent_only = out.conversations - out.conversations_with_person

    _first_responses(session, tenant_id, start, end, out)
    _handovers(session, tenant_id, start, end, out)
    _orders(session, tenant_id, start, end, out)

    tokens = session.execute(
        select(
            func.coalesce(func.sum(AgentTrace.input_tokens), 0),
            func.coalesce(func.sum(AgentTrace.output_tokens), 0),
            func.coalesce(func.sum(AgentTrace.cost_usd), 0),
        ).where(
            AgentTrace.tenant_id == tenant_id,
            AgentTrace.created_at >= start,
            AgentTrace.created_at < end,
        )
    ).one()
    out.tokens_in, out.tokens_out = int(tokens[0]), int(tokens[1])
    out.cost_usd = Decimal(tokens[2])
    return out


def _first_responses(
    session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime, out: DayMetrics
) -> None:
    first_in = (
        select(Message.conversation_id, func.min(Message.created_at).label("at"))
        .join(
            Conversation,
            and_(Conversation.id == Message.conversation_id, Conversation.tenant_id == tenant_id),
        )
        .where(
            Message.tenant_id == tenant_id,
            Message.direction == "inbound",
            Conversation.channel == CHANNEL,
        )
        .group_by(Message.conversation_id)
        .subquery()
    )
    answer = (
        select(func.min(Message.created_at))
        .where(
            Message.tenant_id == tenant_id,
            Message.conversation_id == first_in.c.conversation_id,
            Message.direction == "outbound",
            Message.msg_type != "event",
            Message.author.in_(("agent", "human_operator")),
            Message.created_at > first_in.c.at,
        )
        .scalar_subquery()
    )
    for asked, answered in session.execute(
        select(first_in.c.at, answer).where(first_in.c.at >= start, first_in.c.at < end)
    ).all():
        if answered is not None:
            out.first_responses += 1
            out.first_response_seconds += int((answered - asked).total_seconds())


def _handovers(
    session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime, out: DayMetrics
) -> None:
    reasons: Counter[str] = Counter()
    for (snapshot,) in session.execute(
        select(ConversationStageEvent.facts_snapshot)
        .join(
            Conversation,
            and_(
                Conversation.id == ConversationStageEvent.conversation_id,
                Conversation.tenant_id == tenant_id,
            ),
        )
        .where(
            ConversationStageEvent.tenant_id == tenant_id,
            ConversationStageEvent.to_stage == "DERIVADO",
            ConversationStageEvent.created_at >= start,
            ConversationStageEvent.created_at < end,
            Conversation.channel == CHANNEL,
        )
    ).all():
        handed = snapshot.get("derivado") if isinstance(snapshot, dict) else None
        motive = handed.get("motivo") if isinstance(handed, dict) else None
        reasons[str(motive or "sin motivo")] += 1
    out.handovers = sum(reasons.values())
    out.handover_reasons = dict(reasons)


def _orders(
    session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime, out: DayMetrics
) -> None:
    for currency, count, total in session.execute(
        select(Order.moneda, func.count(), func.coalesce(func.sum(Order.total), 0))
        .where(
            Order.tenant_id == tenant_id,
            Order.is_sandbox.is_(False),
            Order.confirmed_at >= start,
            Order.confirmed_at < end,
        )
        .group_by(Order.moneda)
    ).all():
        out.orders_confirmed += int(count)
        out.sales[str(currency)] = str(Decimal(total).quantize(Decimal("0.01")))


def store_day(session: Session, tenant_id: uuid.UUID, metrics: DayMetrics) -> MetricsDaily:
    """Replace the business's row for that day (recomputing is always safe)."""
    session.execute(
        delete(MetricsDaily).where(
            MetricsDaily.tenant_id == tenant_id, MetricsDaily.day == metrics.day
        )
    )
    row = MetricsDaily(tenant_id=tenant_id, day=metrics.day, **metrics.columns())
    session.add(row)
    session.flush()
    return row


def rollup(session: Session, *, now: datetime) -> int:
    """Compute the closed days of every live business. Returns the rows written."""
    written = 0
    tenants = session.execute(
        select(Tenant.id, Tenant.created_at).where(Tenant.status != "archived")
    ).all()
    for tenant_id, created_at in tenants:
        zone = zone_of(session, tenant_id)
        yesterday = local_today(now, zone) - timedelta(days=1)
        last = session.execute(
            select(func.max(MetricsDaily.day)).where(MetricsDaily.tenant_id == tenant_id)
        ).scalar_one_or_none()
        first = (
            last - timedelta(days=RECOMPUTE_DAYS - 1)
            if last is not None
            else max(
                created_at.astimezone(zone).date(), yesterday - timedelta(days=MAX_BACKFILL_DAYS)
            )
        )
        day = first
        while day <= yesterday:
            store_day(session, tenant_id, compute_day(session, tenant_id, day, zone))
            written += 1
            day += timedelta(days=1)
    return written
