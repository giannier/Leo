"""Trial metering and the tenant lifecycle (TASK-019).

Two ways a trial ends, and whichever happens first wins (Clarification 001):
**14 days** or **250 AI messages**. Only replies on the real WhatsApp channel
count; the sandbox is free and unlimited, because it is where the customer
adjusts tone and RAG and decides the product is worth paying for
(Clarification 002).

Ending a trial means `paused`, not off (Clarification 004). Inbound messages
are still received and stored, the panel stays readable, and the agent stops
replying. Nothing is deleted for 30 days, after which the tenant becomes
`archived` - soft, recoverable by an admin. Permanent deletion happens only at
the account holder's request (Ley 29733).

Every function that needs the time of day takes it as an argument. That keeps
the transitions testable without patching the clock globally, and it means a
job and its tests agree on what "now" was for the whole run rather than each
call re-reading a clock that moved.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.business import AgentConfig
from app.models.conversation import Conversation, Message
from app.models.plan import Plan
from app.models.tenant import (
    TRIAL_ENDED,
    TRIAL_ENDING_SOON,
    TRIAL_ENDING_TOMORROW,
    Tenant,
    TenantNotification,
)

# How long a paused tenant keeps its data before being archived (RF-006).
RETENTION_DAYS = 30

# How long an abandoned checklist waits before the single nudge (§jobs).
WIZARD_IDLE_HOURS = 24

# Used when a tenant never configured one. Neutral, and it does not promise a
# date we cannot keep.
DEFAULT_FAREWELL = (
    "Gracias por escribirnos. En este momento no podemos responder "
    "automaticamente, pero registramos tu mensaje y alguien del equipo te "
    "contactara pronto."
)


@dataclass(frozen=True)
class MeteringResult:
    """What one metered reply did to the trial."""

    counted: bool
    used: int
    limit: int

    @property
    def exhausted(self) -> bool:
        """True once the allowance is gone.

        `>=` rather than `==`: a limit lowered by an admin, or a row that
        somehow overshot, must still read as exhausted. Equality would let a
        tenant past the gate forever.
        """
        return self.counted and self.used >= self.limit


def record_ai_message(session: Session, tenant_id: uuid.UUID, *, channel: str) -> MeteringResult:
    """Count one agent reply against the trial allowance (RF-004).

    Sandbox replies are not counted and do not even lock the row - the whole
    point of Clarification 002 is that experimenting is free.

    The tenant row is locked with `SELECT ... FOR UPDATE` before the read, the
    same as every other read-then-write on a tenant counter in this codebase.
    Two concurrent replies would otherwise both read 249, both write 250, and
    the customer would get one message more than they paid for - and, worse,
    the enforcer would see a count that never passed the limit.
    """
    tenant = session.execute(
        select(Tenant).where(Tenant.id == tenant_id).with_for_update()
    ).scalar_one_or_none()
    if tenant is None:
        return MeteringResult(counted=False, used=0, limit=0)

    plan = session.get(Plan, tenant.plan_id)
    limit = plan.max_ai_messages if plan else 0

    if channel != "whatsapp":
        return MeteringResult(counted=False, used=tenant.ai_messages_used, limit=limit)

    tenant.ai_messages_used += 1
    session.flush()
    return MeteringResult(counted=True, used=tenant.ai_messages_used, limit=limit)


def agent_may_reply(session: Session, tenant: Tenant) -> bool:
    """Whether the agent is allowed to answer on the real channel.

    The seam Feature 002's webhook calls before invoking the agent, and the
    thing that makes `paused` mean anything: without it, pausing a tenant
    changes a column and nothing else.

    Deliberately **not** consulted for the sandbox. Trying your own agent stays
    free after the trial ends - it is how a paused customer decides the product
    is worth paying for (Clarification 002).

    The quota is re-checked here rather than trusted from the last enforcer
    run. The enforcer is hourly, and an hour at the end of a trial is enough
    conversation to hand out a few dozen free replies.
    """
    if tenant.status not in ("trial", "active"):
        return False
    if tenant.status == "trial" and quota_exhausted(session, tenant):
        return False
    config = session.get(AgentConfig, tenant.id)
    return bool(config and config.is_active)


def quota_exhausted(session: Session, tenant: Tenant) -> bool:
    """Whether this tenant has spent its message allowance."""
    plan = session.get(Plan, tenant.plan_id)
    if plan is None:
        return False
    return tenant.ai_messages_used >= plan.max_ai_messages


def is_expired(session: Session, tenant: Tenant, *, now: datetime) -> bool:
    """Whether the trial is over, by either clock or counter."""
    if tenant.status != "trial":
        return False
    by_date = tenant.trial_ends_at is not None and tenant.trial_ends_at <= now
    return by_date or quota_exhausted(session, tenant)


def due_notice(session: Session, tenant: Tenant, *, now: datetime) -> str | None:
    """Which of the day -3 / -1 / 0 warnings this tenant should get now.

    Thresholds rather than exact dates, checked most urgent first. A daily job
    that misses a tick - a worker restart, a deploy, a slow night - would skip
    the notice entirely on an equality test, and the customer would find out
    their trial ended by discovering the agent stopped answering.

    Returning the same kind on two consecutive days is fine and expected: the
    primary key on `tenant_notifications` refuses the second send.
    """
    if tenant.status != "trial":
        return None
    if quota_exhausted(session, tenant):
        return TRIAL_ENDED
    if tenant.trial_ends_at is None:
        return None

    remaining = tenant.trial_ends_at - now
    if remaining <= timedelta(0):
        return TRIAL_ENDED
    if remaining <= timedelta(days=1):
        return TRIAL_ENDING_TOMORROW
    if remaining <= timedelta(days=3):
        return TRIAL_ENDING_SOON
    return None


def claim_notification(session: Session, tenant_id: uuid.UUID, kind: str) -> bool:
    """Reserve the right to send one notice. True if it is ours to send.

    Claimed *before* the email goes out, not after. The failure this guards
    against is sending twice, and a claim written afterwards leaves a window
    where a crash between send and write repeats the message on the next run.
    The opposite failure - claiming and then failing to send - loses one
    notice, which is the cheaper of the two mistakes and visible in the panel
    anyway.
    """
    try:
        with session.begin_nested():
            session.add(TenantNotification(tenant_id=tenant_id, kind=kind))
            session.flush()
    except IntegrityError:
        return False
    return True


def pause_tenant(session: Session, tenant: Tenant, *, now: datetime) -> list[Conversation]:
    """Move a tenant to `paused` and close its live conversations gracefully.

    "El agente deja de responder; los mensajes entrantes se siguen recibiendo
    y almacenando" - so nothing here touches the inbox and nothing flips
    `agent_configs.is_active`. That switch belongs to the customer: turning it
    off on their behalf would mean that paying to resume leaves the agent
    silent until somebody notices a toggle. The agent is stopped by the tenant
    being paused, and it comes back the moment it is not.

    Open WhatsApp threads get a farewell first (Clarification 004). Leaving a
    customer mid-conversation with silence is the worst version of this: they
    keep typing to a business that has stopped answering and no longer says so.
    """
    tenant.status = "paused"
    tenant.paused_at = now

    config = session.get(AgentConfig, tenant.id)
    farewell = (config.farewell_on_pause if config else None) or DEFAULT_FAREWELL

    open_threads = list(
        session.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant.id,
                Conversation.channel == "whatsapp",
                Conversation.status == "open",
            )
        )
        .scalars()
        .all()
    )
    for conversation in open_threads:
        session.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                direction="outbound",
                # `system`, not `agent`: this is the platform closing the
                # thread, not the model producing an answer. A trace reader
                # looking for what the agent said must not find this.
                author="system",
                content=farewell,
                # `queued`, since Feature 002. Before the real channel existed
                # this row *was* the farewell; now it is only the intention to
                # send one, and leaving it stateless meant nothing ever
                # delivered it. The customer would be left with the silence
                # this whole branch exists to avoid.
                delivery_status="queued",
            )
        )
        conversation.status = "closed"

    session.flush()
    return open_threads


def archive_tenant(tenant: Tenant, *, now: datetime) -> None:
    """Soft-archive a tenant whose retention window has passed (RF-006).

    Nothing is deleted. `archived` is a state an admin can reverse; permanent
    deletion happens only at the account holder's request (Ley 29733).
    """
    tenant.status = "archived"
    tenant.archived_at = now


def expired_trials(session: Session, *, now: datetime, limit: int = 500) -> Sequence[Tenant]:
    """Trials that are over by the clock, oldest first.

    Quota exhaustion is *not* filtered here: it needs the plan row, and
    pushing that into SQL would duplicate `quota_exhausted` in a second
    dialect. The caller re-checks each candidate.
    """
    return (
        session.execute(
            select(Tenant)
            .where(Tenant.status == "trial", Tenant.trial_ends_at <= now)
            .order_by(Tenant.trial_ends_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def trials_over_quota(session: Session, *, limit: int = 500) -> Sequence[Tenant]:
    """Trials that have spent their message allowance."""
    return (
        session.execute(
            select(Tenant)
            .join(Plan, Plan.id == Tenant.plan_id)
            .where(Tenant.status == "trial", Tenant.ai_messages_used >= Plan.max_ai_messages)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def active_trials(session: Session, *, limit: int = 1000) -> Sequence[Tenant]:
    """Every tenant still on trial, for the notifier to look at."""
    return (
        session.execute(select(Tenant).where(Tenant.status == "trial").limit(limit)).scalars().all()
    )


def archivable(session: Session, *, now: datetime, limit: int = 500) -> Sequence[Tenant]:
    """Paused tenants past the 30-day retention window."""
    cutoff = now - timedelta(days=RETENTION_DAYS)
    return (
        session.execute(
            select(Tenant)
            .where(
                Tenant.status == "paused",
                Tenant.paused_at.is_not(None),
                Tenant.paused_at <= cutoff,
            )
            .order_by(Tenant.paused_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )
