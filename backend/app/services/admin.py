"""Platform administration (TASK-018).

The one surface that crosses tenant boundaries, which is why every action here
is audited (RF-012). The audit log is not paperwork: this is where a person
can read another business's data, connect a number on their behalf and extend
their trial, and the only thing separating "operating the platform" from
"quietly doing whatever" is a record nobody can write around.

So the audit entry is written in the **same transaction** as the change. A log
written afterwards is one that a crash, an exception or a `return` can skip,
and an audit trail with holes is worse than none - it invites the belief that
the absence of an entry means the absence of an action.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.business import Product
from app.models.channel import WebhookEvent
from app.models.conversation import REAL_CHANNELS, Conversation, Message
from app.models.knowledge import KnowledgeSource
from app.models.tenant import Tenant, TenantUser
from app.models.user import User
from app.models.whatsapp import OPEN_REQUEST_STATUSES, ConnectionRequest, WhatsAppNumber
from app.providers.whatsapp.base import InvalidCredentials, NumberCredentials, WhatsAppProvider
from app.repositories.channel import WebhookEventRepository
from app.services.connection import store_credentials

OPEN_STATUSES = ("requested", "in_progress")
TERMINAL_STATUSES = ("connected", "rejected")

# What an admin may move a request to, from where. Written out rather than
# left implicit because the illegal transitions are the interesting ones: a
# connected request must never be reopened, since the number is live and a
# second connection would be two businesses on one line.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "requested": ("in_progress", "connected", "rejected"),
    "in_progress": ("connected", "rejected"),
    "connected": (),
    "rejected": (),
}


class AdminError(RuntimeError):
    """Base for every refused administrative action."""


class RequestNotFound(AdminError):
    """No connection request with that id."""


class DeadLetterNotFound(AdminError):
    """No dead-lettered webhook with that id."""


class TenantNotFound(AdminError):
    """No tenant with that id."""


class IllegalTransition(AdminError):
    """That status change is not allowed from the current one."""


class CredentialsRequired(AdminError):
    """Connecting a number needs the provider credentials."""


class RejectionReasonRequired(AdminError):
    """A rejection the customer cannot act on is not a rejection."""


class NumberAlreadyConnected(AdminError):
    """Another business already holds that number (RF-008)."""


@dataclass(frozen=True)
class NotificationTarget:
    """Who to tell that something was done to their account."""

    email: str
    tenant_name: str


@dataclass(frozen=True)
class TenantUsage:
    """What an operator needs to answer "is this account healthy?"."""

    tenant: Tenant
    owner_email: str | None
    members: int
    products: int
    knowledge_chars: int
    has_number: bool


def record_audit(
    session: Session,
    *,
    actor_id: uuid.UUID,
    action: str,
    tenant_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    """Append one audit entry. Never committed on its own.

    The caller commits it together with the change it describes, so the two
    cannot disagree. `detail` must never carry a credential - the log is read
    far more widely than the table it describes.
    """
    entry = AuditLog(
        actor_id=actor_id,
        tenant_id=tenant_id,
        action=action,
        detail=detail or {},
    )
    session.add(entry)
    session.flush()
    return entry


class PlatformAdminService:
    """Cross-tenant operations. Every one of them audited."""

    def __init__(self, session: Session, *, actor: User) -> None:
        self._session = session
        self._actor = actor

    # ------------------------------------------------------- request queue

    def queue(self, *, status: str | None = None, limit: int = 50) -> Sequence[ConnectionRequest]:
        """The work queue, oldest first - it is a queue, not a feed.

        Without a status filter the open requests come first (oldest first), and
        the closed ones after them, newest first: otherwise, once there are more
        closed requests than fit in a page, a new one never shows up.
        """
        statement = select(ConnectionRequest)
        if status:
            statement = statement.where(ConnectionRequest.status == status)
            statement = statement.order_by(ConnectionRequest.created_at)
        else:
            open_first = case((ConnectionRequest.status.in_(OPEN_REQUEST_STATUSES), 0), else_=1)
            oldest_if_open = case(
                (
                    ConnectionRequest.status.in_(OPEN_REQUEST_STATUSES),
                    func.extract("epoch", ConnectionRequest.created_at),
                ),
                else_=-func.extract("epoch", ConnectionRequest.created_at),
            )
            statement = statement.order_by(open_first, oldest_if_open)
        return self._session.execute(statement.limit(limit)).scalars().all()

    def get_request(self, request_id: uuid.UUID) -> ConnectionRequest:
        request = self._session.get(ConnectionRequest, request_id)
        if request is None:
            raise RequestNotFound(str(request_id))
        return request

    def transition(
        self,
        request_id: uuid.UUID,
        *,
        status: str,
        credentials: NumberCredentials | None = None,
        rejection_reason: str | None = None,
        admin_notes: str | None = None,
        provider: WhatsAppProvider,
    ) -> ConnectionRequest:
        """Move a request forward, doing whatever that status implies."""
        request = self.get_request(request_id)
        allowed = ALLOWED_TRANSITIONS.get(request.status, ())
        if status not in allowed:
            raise IllegalTransition(f"cannot move a {request.status!r} request to {status!r}")

        now = datetime.now(UTC)
        if admin_notes is not None:
            request.admin_notes = admin_notes

        if status == "connected":
            if credentials is None:
                raise CredentialsRequired("connecting a number needs its credentials")
            self._connect(request, credentials=credentials, provider=provider, now=now)
        elif status == "rejected":
            if not rejection_reason:
                # A rejection the customer cannot act on just becomes a
                # support ticket asking what to change.
                raise RejectionReasonRequired("say why, so the customer can fix it")
            request.rejection_reason = rejection_reason

        request.status = status
        request.processed_by = self._actor.id

        record_audit(
            self._session,
            actor_id=self._actor.id,
            tenant_id=request.tenant_id,
            action=f"connection_request.{status}",
            detail={
                "request_id": str(request.id),
                "phone_e164": request.phone_e164,
                # Deliberately no credentials. The audit log is read far more
                # widely than `whatsapp_numbers`, and the whole point of
                # encrypting them is defeated by logging them in clear.
                "rejection_reason": rejection_reason,
            },
        )
        self._session.commit()
        return request

    def _connect(
        self,
        request: ConnectionRequest,
        *,
        credentials: NumberCredentials,
        provider: WhatsAppProvider,
        now: datetime,
    ) -> WhatsAppNumber:
        try:
            provider.validate(credentials)
        except InvalidCredentials as exc:
            raise CredentialsRequired(str(exc)) from exc

        number = WhatsAppNumber(
            tenant_id=request.tenant_id,
            phone_e164=request.phone_e164,
            display_name=request.display_name,
            provider=credentials.provider,
            status="connected",
            connected_at=now,
        )
        try:
            # The global unique index is the arbiter. Between an admin opening
            # the queue and pressing connect, another request for the same
            # number may have been completed.
            #
            # `add` goes *inside* the savepoint deliberately. Rolling one back
            # restores the session to its state when the savepoint opened, so
            # an object added beforehand survives as pending and the very next
            # flush retries the same failing INSERT - which surfaces later, on
            # an unrelated statement, as a PendingRollbackError.
            with self._session.begin_nested():
                self._session.add(number)
                self._session.flush()
        except IntegrityError as exc:
            raise NumberAlreadyConnected(request.phone_e164) from exc

        store_credentials(self._session, number, credentials=credentials, provider=provider)
        return number

    def notification_target(self, tenant_id: uuid.UUID) -> NotificationTarget | None:
        """The owner's address, for telling them what we did.

        The owner rather than every member: connecting a number is an account
        event, not an operational one, and copying the whole team on it would
        train people to ignore the messages that matter.
        """
        row = self._session.execute(
            select(User.email, Tenant.name)
            .join(TenantUser, TenantUser.user_id == User.id)
            .join(Tenant, Tenant.id == TenantUser.tenant_id)
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.role == "owner",
                TenantUser.status == "active",
            )
            .limit(1)
        ).first()
        if row is None:
            return None
        return NotificationTarget(email=row[0], tenant_name=row[1])

    # ------------------------------------------------------------- tenants

    def list_tenants(
        self,
        *,
        search: str | None = None,
        status: str | None = None,
        limit: int = 50,
        include_test: bool = False,
    ) -> Sequence[Tenant]:
        statement = select(Tenant)
        if not include_test:
            # The end-to-end suite's businesses (TASK-455).
            statement = statement.where(Tenant.es_prueba.is_(False))
        if search:
            statement = statement.where(Tenant.name.ilike(f"%{search}%"))
        if status:
            statement = statement.where(Tenant.status == status)
        return (
            self._session.execute(statement.order_by(Tenant.created_at.desc()).limit(limit))
            .scalars()
            .all()
        )

    def tenant_usage(self, tenant_id: uuid.UUID) -> TenantUsage:
        tenant = self._session.get(Tenant, tenant_id)
        if tenant is None:
            raise TenantNotFound(str(tenant_id))

        owner_email = self._session.execute(
            select(User.email)
            .join(TenantUser, TenantUser.user_id == User.id)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.role == "owner")
            .limit(1)
        ).scalar_one_or_none()

        # Active memberships only. A disabled one is a person who cannot sign
        # in, and counting them would overstate how large the account is -
        # which is precisely the number a seat limit is checked against.
        members = self._session.execute(
            select(func.count())
            .select_from(TenantUser)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.status == "active")
        ).scalar_one()
        products = self._session.execute(
            select(func.count())
            .select_from(Product)
            .where(Product.tenant_id == tenant_id, Product.deleted_at.is_(None))
        ).scalar_one()
        knowledge_chars = self._session.execute(
            select(func.coalesce(func.sum(KnowledgeSource.char_count), 0)).where(
                KnowledgeSource.tenant_id == tenant_id, KnowledgeSource.deleted_at.is_(None)
            )
        ).scalar_one()
        has_number = (
            self._session.execute(
                select(WhatsAppNumber.id).where(
                    WhatsAppNumber.tenant_id == tenant_id,
                    WhatsAppNumber.status == "connected",
                )
            ).first()
            is not None
        )

        return TenantUsage(
            tenant=tenant,
            owner_email=owner_email,
            members=members,
            products=products,
            knowledge_chars=knowledge_chars,
            has_number=has_number,
        )

    # ------------------------------------------------------- the dead letters

    def dead_letters(self, *, limit: int = 50, offset: int = 0) -> Sequence[DeadLetter]:
        """Webhooks the pipeline gave up on, newest first.

        This queue is the whole reason `dead_letter` is not a discard. Every
        row is a customer's message that never got an answer, and it stays
        readable and replayable until a person decides otherwise (RF-007).
        """
        events = WebhookEventRepository(self._session).list_by_status(
            "dead_letter", limit=limit, offset=offset
        )
        return [_as_dead_letter(event) for event in events]

    def retry_dead_letter(self, event_id: uuid.UUID) -> uuid.UUID:
        """Put one back in the queue. Returns the id to enqueue.

        The row is moved back to `received` and its attempt count cleared, so
        the retry gets a full budget rather than one last chance - whatever
        was fixed in between deserves a fair run.

        The enqueue itself is the caller's: this service owns the database,
        and a job queued inside a transaction that then rolls back is a worker
        reading a row that does not exist.
        """
        events = WebhookEventRepository(self._session)
        event = events.get(event_id)
        if event is None or event.status != "dead_letter":
            raise DeadLetterNotFound(str(event_id))

        event.status = "received"
        event.attempts = 0
        event.error_msg = None
        record_audit(
            self._session,
            actor_id=self._actor.id,
            action="webhook.retried",
            detail={
                "event_id": str(event_id),
                "provider_event_id": event.provider_event_id,
            },
        )
        self._session.commit()
        return event.id

    # ------------------------------------------------------- channel metrics

    def channel_metrics(self, tenant_id: uuid.UUID, *, since: datetime) -> ChannelMetrics:
        """Traffic and answer latency for one tenant (RF-008).

        Latency is measured from the customer's message to the *first* reply
        after it, which is what they actually experienced. Measuring from the
        job's start would report how fast the model was and hide the eight
        seconds of debounce and any time the message spent in a queue.
        """
        counted = (
            select(func.count())
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.tenant_id == tenant_id,
                Conversation.channel.in_(REAL_CHANNELS),
                Message.created_at >= since,
            )
        )
        messages_in = self._session.execute(
            counted.where(Message.direction == "inbound")
        ).scalar_one()
        messages_out = self._session.execute(
            counted.where(Message.direction == "outbound")
        ).scalar_one()
        failed = self._session.execute(
            counted.where(Message.delivery_status == "failed")
        ).scalar_one()

        latencies = self._response_latencies(tenant_id, since=since)
        return ChannelMetrics(
            messages_in=messages_in,
            messages_out=messages_out,
            failed_sends=failed,
            p50_response_ms=_percentile(latencies, 50),
            p95_response_ms=_percentile(latencies, 95),
        )

    def _response_latencies(self, tenant_id: uuid.UUID, *, since: datetime) -> list[int]:
        """Milliseconds from each answered question to its answer."""
        rows = (
            self._session.execute(
                select(Message.conversation_id, Message.direction, Message.created_at)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(
                    Message.tenant_id == tenant_id,
                    Conversation.channel.in_(REAL_CHANNELS),
                    Message.created_at >= since,
                    Message.author.in_(("customer", "agent")),
                )
                .order_by(Message.conversation_id, Message.created_at)
            )
            .tuples()
            .all()
        )

        latencies: list[int] = []
        asked_at: dict[uuid.UUID, datetime] = {}
        for conversation_id, direction, created_at in rows:
            if direction == "inbound":
                # The *first* unanswered message of a burst. Somebody who
                # sends four lines waited from the first one, not the last.
                asked_at.setdefault(conversation_id, created_at)
                continue
            asked = asked_at.pop(conversation_id, None)
            if asked is not None:
                latencies.append(int((created_at - asked).total_seconds() * 1000))
        return latencies

    def extend_trial(self, tenant_id: uuid.UUID, *, days: int, reason: str) -> Tenant:
        """Give a trial more time (Clarification 012).

        Extended from **now** when the trial has already lapsed, rather than
        from the old end date - otherwise "seven more days" on a trial that
        expired last week grants nothing, and the customer is compensated for
        our delay with an expiry in the past.
        """
        tenant = self._session.get(Tenant, tenant_id)
        if tenant is None:
            raise TenantNotFound(str(tenant_id))

        now = datetime.now(UTC)
        base = tenant.trial_ends_at if tenant.trial_ends_at and tenant.trial_ends_at > now else now
        previous = tenant.trial_ends_at
        tenant.trial_ends_at = base + timedelta(days=days)

        # A paused tenant that is given more time has to be un-paused, or the
        # extension is a date change with no effect the customer can see.
        if tenant.status == "paused":
            tenant.status = "trial"
            tenant.paused_at = None

        record_audit(
            self._session,
            actor_id=self._actor.id,
            tenant_id=tenant_id,
            action="trial.extended",
            detail={
                "days": days,
                "reason": reason,
                "previous_ends_at": previous.isoformat() if previous else None,
                "new_ends_at": tenant.trial_ends_at.isoformat(),
            },
        )
        self._session.commit()
        return tenant


@dataclass(frozen=True)
class DeadLetter:
    """One webhook the pipeline gave up on, as an operator needs to see it."""

    id: uuid.UUID
    provider: str
    provider_event_id: str
    attempts: int
    error_msg: str | None
    created_at: datetime
    # Drawn out of the payload so the queue is scannable without expanding
    # every row. Absent when the payload is the reason it failed.
    event_type: str | None
    phone_e164: str | None


@dataclass(frozen=True)
class ChannelMetrics:
    """How one tenant's number behaved over a period (RF-008)."""

    messages_in: int
    messages_out: int
    failed_sends: int
    # Milliseconds between a customer's message and the reply to it. `None`
    # when nothing was answered in the period - which is different from zero,
    # and a dashboard that shows 0 ms for a silent day is worse than one that
    # shows nothing.
    p50_response_ms: int | None
    p95_response_ms: int | None


def _as_dead_letter(event: WebhookEvent) -> DeadLetter:
    """Flatten one stored delivery into what an operator has to see.

    The type and the phone number are dug out of the payload so the queue is
    scannable without expanding every row - and read defensively, because a
    malformed payload is one of the reasons an event lands here in the first
    place.
    """
    payload = event.payload if isinstance(event.payload, dict) else {}
    inbound = payload.get("whatsappInboundMessage")
    outbound = payload.get("whatsappMessage")
    named = inbound if isinstance(inbound, dict) else outbound
    phone = named.get("from") if isinstance(named, dict) else None

    return DeadLetter(
        id=event.id,
        provider=event.provider,
        provider_event_id=event.provider_event_id,
        attempts=event.attempts,
        error_msg=event.error_msg,
        created_at=event.created_at,
        event_type=str(payload["type"]) if isinstance(payload.get("type"), str) else None,
        phone_e164=str(phone) if phone else None,
    )


def _percentile(values: list[int], percentile: int) -> int | None:
    """Nearest-rank percentile, or None for an empty sample.

    Nearest-rank rather than interpolated: these are latencies of real
    messages, and "the 95th slowest reply took 4.2 seconds" is a thing that
    happened, while an interpolated 4.17 is a number nobody can go and look
    at.

    `None` rather than 0 for no data. A dashboard showing 0 ms on a day nobody
    wrote in is worse than one showing nothing, because it reads as very fast.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]
