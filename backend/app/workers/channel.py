"""`process_webhook_event`: everything that happens after the 200.

The endpoint stored the delivery and answered. This is where it is understood,
and it is written so that being interrupted is survivable: the row records how
many attempts it has had, one event is one transaction, and nothing outside
the database happens before the row says it did.

Three outcomes, and the third is the one that matters:

* **processed** - understood and applied, or understood and deliberately
  ignored (a template status this version has no use for).
* **failed** - something went wrong that another attempt might fix. Retried.
* **dead_letter** - it has failed enough times (`MAX_WEBHOOK_ATTEMPTS`). This
  is *not* a discard: the raw payload is still there, an admin can see it, and
  it can be replayed once whatever broke is fixed. The Constitution's rule is
  graceful degradation with a visible alert, never a silent failure - and on
  this path a silent failure is a customer whose message nobody ever answers.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.locks import LockStore, acquire_lock, claim_once
from app.core.queue import ArqJobQueue, JobQueue
from app.models.llm_settings import TenantLLMSettings
from app.providers.llm.factory import provider_for_tenant
from app.providers.whatsapp.base import (
    InboundMessage,
    QualityUpdate,
    StatusUpdate,
    WhatsAppProvider,
)
from app.repositories.channel import WebhookEventRepository
from app.services.channel import (
    ChannelEventProcessor,
    ConversationResponder,
    UnknownNumberError,
    debounce_key,
    lock_key,
)

logger = logging.getLogger(__name__)

# The names the API and the worker enqueue by. The web process never imports
# this module, so it never pulls the worker's dependencies into a request
# handler.
PROCESS_EVENT_JOB = "process_webhook_event"
ANSWER_JOB = "answer_conversation"

# How long to wait before trying a locked conversation again. Short, because
# the thing holding the lock is one model call.
LOCK_RETRY_SECONDS = 5

# Reported for observability; none of these are state transitions.
OUTCOME_MISSING = "missing"
OUTCOME_ALREADY_DONE = "already_done"
OUTCOME_IGNORED = "ignored"
OUTCOME_APPLIED = "applied"
OUTCOME_UNKNOWN_NUMBER = "unknown_number"
OUTCOME_UNREADABLE = "unreadable"
OUTCOME_LOCKED = "locked"
OUTCOME_ANSWERED = "answered"
OUTCOME_NOTHING_TO_SAY = "nothing_to_say"


@dataclass(frozen=True)
class EventOutcome:
    """What became of one delivery."""

    event_id: uuid.UUID
    outcome: str
    # Set when the event produced an inbound message that still has to be
    # answered. The ARQ shell reads these to schedule the reply; a stored
    # message nobody is told about is a customer nobody answers.
    inbound_message_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None

    def __str__(self) -> str:
        return self.outcome


def process_event(
    session: Session,
    event_id: uuid.UUID,
    *,
    provider: WhatsAppProvider,
    now: datetime | None = None,
) -> EventOutcome:
    """Understand one stored delivery and apply it.

    Takes its session and its provider as arguments so an integration test can
    drive the exact production path inside a rolled-back transaction, with no
    Redis and no network.
    """
    at = now or datetime.now(UTC)
    events = WebhookEventRepository(session)
    event = events.get(event_id)
    if event is None:
        # The job outlived its row, which only happens if something deleted
        # it. Nothing to retry.
        logger.warning("webhook event no longer exists", extra={"event_id": str(event_id)})
        return EventOutcome(event_id, OUTCOME_MISSING)

    if event.status in ("processed", "dead_letter"):
        # A duplicate job for an event that is already finished. Terminal
        # states are terminal: re-applying an inbound message here is how one
        # customer gets answered twice.
        return EventOutcome(event_id, OUTCOME_ALREADY_DONE)

    processor = ChannelEventProcessor(session)
    try:
        parsed = provider.parse_event(event.payload)
        if parsed is None:
            # A complete, valid event with no behaviour attached - a template
            # status, a payment notice. Not a failure, and explicitly not a
            # retry: attempting it again produces the same nothing.
            events.mark_processed(event, at=at)
            session.commit()
            return EventOutcome(event_id, OUTCOME_IGNORED)

        outcome = _apply(processor, parsed, event_id)
        events.mark_processed(event, at=at)
        session.commit()
        return outcome
    except UnknownNumberError as exc:
        # Retrying will not conjure the number, but a human has to see it: it
        # means either a number was disconnected while somebody was still
        # writing to it, or this deployment is receiving another one's
        # traffic. Straight to the dead-letter queue.
        session.rollback()
        event = _reload(events, event_id)
        if event is not None:
            event.attempts = max(event.attempts, 1)
            event.status = "dead_letter"
            event.error_msg = str(exc)[:1000]
            session.commit()
        logger.warning("webhook for an unknown number", extra={"event_id": str(event_id)})
        return EventOutcome(event_id, OUTCOME_UNKNOWN_NUMBER)
    except Exception as exc:  # noqa: BLE001 - the point is that nothing escapes
        # Nothing may escape this function. An exception that reaches ARQ
        # loses the attempt count with the transaction, and the event either
        # spins forever or stops being retried with no record of why.
        session.rollback()
        event = _reload(events, event_id)
        if event is not None:
            events.mark_failed(event, error=f"{type(exc).__name__}: {exc}")
            session.commit()
        logger.exception("could not process a webhook event", extra={"event_id": str(event_id)})
        return EventOutcome(event_id, OUTCOME_UNREADABLE)


def _apply(processor: ChannelEventProcessor, parsed: object, event_id: uuid.UUID) -> EventOutcome:
    """Route one parsed event to the thing that knows what to do with it."""
    if isinstance(parsed, InboundMessage):
        result = processor.receive_inbound(parsed)
        return EventOutcome(
            event_id,
            OUTCOME_APPLIED,
            # Only a genuinely new message needs answering. A redelivery has
            # already been dealt with, and replying again would send the
            # customer the same answer twice.
            inbound_message_id=result.message_id if result.is_new else None,
            tenant_id=result.tenant_id,
            conversation_id=result.conversation_id,
        )
    if isinstance(parsed, StatusUpdate):
        processor.apply_status(parsed)
        return EventOutcome(event_id, OUTCOME_APPLIED)
    if isinstance(parsed, QualityUpdate):
        processor.apply_quality(parsed)
        return EventOutcome(event_id, OUTCOME_APPLIED)
    # Unreachable through `parse_event`, which returns exactly these three or
    # None. Here so that adding a fourth kind fails loudly rather than being
    # silently dropped.
    raise TypeError(f"no handler for parsed event of type {type(parsed).__name__}")


def _reload(events: WebhookEventRepository, event_id: uuid.UUID) -> Any:
    """Re-read the row after a rollback discarded the in-memory copy."""
    return events.get(event_id)


async def schedule_answer(
    queue: JobQueue,
    store: LockStore,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    debounce_seconds: int,
) -> bool:
    """Claim the burst window for this thread and schedule one reply.

    The first message of a burst claims the marker and books the job for
    `debounce_seconds` later; the ones behind it find the marker taken and
    book nothing. That is the whole of RF-005: five messages in eight seconds
    produce one answer.

    A *fixed* window from the first message, never one that restarts on each -
    a restarting timer means somebody who keeps typing is never answered at
    all, and the feature's own acceptance criterion is a reply inside ten
    seconds.
    """
    if not await claim_once(store, debounce_key(conversation_id), ttl_seconds=debounce_seconds):
        return False
    await queue.enqueue(
        ANSWER_JOB,
        str(tenant_id),
        str(conversation_id),
        defer_seconds=debounce_seconds,
    )
    return True


async def process_webhook_event(ctx: dict[str, Any], event_id: str) -> str:
    """ARQ entry point. Registered in `app/workers/settings.py`."""
    session_factory = ctx["session_factory"]
    with session_factory() as session:
        outcome = process_event(session, uuid.UUID(event_id), provider=ctx["whatsapp"])

    if (
        outcome.inbound_message_id is not None
        and outcome.tenant_id is not None
        and outcome.conversation_id is not None
    ):
        settings = ctx["settings"]
        await schedule_answer(
            ArqJobQueue(ctx["redis"]),
            ctx["redis"],
            tenant_id=outcome.tenant_id,
            conversation_id=outcome.conversation_id,
            debounce_seconds=settings.channel_debounce_seconds,
        )
    return str(outcome)


async def answer_conversation(ctx: dict[str, Any], tenant_id: str, conversation_id: str) -> str:
    """Answer everything one customer has said since the last reply.

    Serialised per conversation (RF-006). Different customers are answered in
    parallel; two replies to the *same* customer computed at once would read
    the same history and answer the same question twice.
    """
    tenant = uuid.UUID(tenant_id)
    conversation = uuid.UUID(conversation_id)
    store = ctx["redis"]

    lock = await acquire_lock(store, lock_key(conversation))
    if lock is None:
        # Somebody else is mid-turn on this thread. Come back rather than
        # give up: they may have read the transcript before this message
        # landed, and dropping it would leave a customer unanswered.
        await ArqJobQueue(store).enqueue(
            ANSWER_JOB, tenant_id, conversation_id, defer_seconds=LOCK_RETRY_SECONDS
        )
        return OUTCOME_LOCKED

    try:
        # Released before the transcript is read, not after. A message that
        # arrives from here on belongs to the *next* burst and has to be able
        # to book its own job - otherwise it waits for one that has already
        # decided what to say.
        await store.delete(debounce_key(conversation))

        session_factory = ctx["session_factory"]
        with session_factory() as session:
            llm = provider_for_tenant(session.get(TenantLLMSettings, tenant))
            responder = ConversationResponder(session, tenant, llm=llm)
            answer = await responder.respond(conversation)
            session.commit()
        return OUTCOME_ANSWERED if answer is not None else OUTCOME_NOTHING_TO_SAY
    finally:
        await lock.release()
