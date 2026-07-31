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

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.business import Product
from app.models.knowledge import KnowledgeSource
from app.models.tenant import Tenant, TenantUser
from app.models.user import User
from app.models.whatsapp import ConnectionRequest, WhatsAppNumber
from app.providers.whatsapp.base import InvalidCredentials, NumberCredentials, WhatsAppProvider
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
        """The work queue, oldest first - it is a queue, not a feed."""
        statement = select(ConnectionRequest)
        if status:
            statement = statement.where(ConnectionRequest.status == status)
        statement = statement.order_by(ConnectionRequest.created_at).limit(limit)
        return self._session.execute(statement).scalars().all()

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
        self, *, search: str | None = None, status: str | None = None, limit: int = 50
    ) -> Sequence[Tenant]:
        statement = select(Tenant)
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
