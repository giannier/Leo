"""Tenants, their memberships, and registration anti-abuse.

A tenant is the unit of isolation: every business table in the system carries
`tenant_id` and every repository query filters on it (Constitution).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_check,
)

TENANT_STATUSES = ("trial", "active", "paused", "archived")
TENANT_ROLES = ("owner", "tenant_admin", "operator")
MEMBERSHIP_STATUSES = ("active", "disabled")
INVITABLE_ROLES = ("tenant_admin", "operator")

# One-shot notices, each sent at most once per tenant. The trial ones are the
# day -3 / -1 / 0 warnings of Clarification 004; `wizard_reminder` is the
# single nudge for an abandoned checklist.
TRIAL_ENDING_SOON = "trial_ending_3d"
TRIAL_ENDING_TOMORROW = "trial_ending_1d"
TRIAL_ENDED = "trial_ended"
WIZARD_REMINDER = "wizard_reminder"
# Sent the moment the counter crosses 80% (Feature 002, HU-04). The other
# three are about the calendar; this one is about the meter, and a trial can
# run out of messages a week before it runs out of days.
QUOTA_80 = "quota_80"

# The notices that go out once per tenant: the table refuses a second row of these
# kinds (a partial unique index), which is how the jobs know they already sent one.
ONE_SHOT_KINDS = (
    TRIAL_ENDING_SOON,
    TRIAL_ENDING_TOMORROW,
    TRIAL_ENDED,
    WIZARD_REMINDER,
    QUOTA_80,
)

# Events of the sales engine (Feature 004, `contracts/api.md` section 10): each one
# happens many times and is its own row.
NEW_RECEIPT = "nuevo_comprobante"
CASH_ORDER = "pedido_contra_entrega"
HANDOFF = "derivacion"
CUSTOMER_UNREACHED = "ventana_cerrada"
LLM_DOWN = "llm_caido"

EVENT_KINDS = (NEW_RECEIPT, CASH_ORDER, HANDOFF, CUSTOMER_UNREACHED, LLM_DOWN)

NOTIFICATION_KINDS = (*ONE_SHOT_KINDS, *EVENT_KINDS)

# Where the warning fires. Not 90%: at 250 messages that is 25 left, which on
# a busy afternoon is an hour's notice - too late to do anything but watch the
# agent stop.
QUOTA_WARNING_RATIO = 0.8


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A customer business account.

    Lifecycle is `trial -> paused -> archived`, plus `active` once billing
    lands in Feature 009. Pausing never discards inbound messages; it only
    stops the agent from replying.
    """

    __tablename__ = "tenants"
    __table_args__ = (enum_check("status", TENANT_STATUSES, "tenants_status"),)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="trial")
    plan_id: Mapped[str] = mapped_column(
        Text, ForeignKey("plans.id"), nullable=False, server_default="trial"
    )

    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Counts agent replies on the real WhatsApp channel only. Sandbox is free
    # and unlimited (Clarification 002).
    ai_messages_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantNotification(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """A notice to the business: a one-shot one already sent, or an event.

    **Events** (`EVENT_KINDS`, Feature 004, migration 0022): a receipt arrived, a
    cash-on-delivery order waits, a conversation was handed to a person, the customer
    could not be told, the model went down. Many per tenant, each with a `payload`
    that says what it is about.

    **One-shot kinds** (TASK-019), whose guarantee is now a partial unique index
    instead of the primary key:

    The trial notifier runs daily and the wizard reminder sends "un email
    unico" (Clarification 004, contracts §jobs). Both need to know what has
    already gone out, and a job that re-reads a date and re-decides would mail
    the customer again on every run, after every worker restart, and twice if
    two workers pick up the same tick.

    The unique index *is* the guarantee. An `INSERT` either succeeds or violates
    it, and that violation is the answer to "was this already sent?" - the same
    reasoning as the connection request indexes: let the database decide, never a
    check in front of it.
    """

    __tablename__ = "tenant_notifications"
    __table_args__ = (
        enum_check("kind", NOTIFICATION_KINDS, "tenant_notifications_kind"),
        Index(
            "uq_tenant_notifications_tenant_id_kind",
            "tenant_id",
            "kind",
            unique=True,
            postgresql_where=text(
                "kind IN (" + ", ".join(f"'{kind}'" for kind in ONE_SHOT_KINDS) + ")"
            ),
        ),
        Index(
            "ix_tenant_notifications_tenant_id_kind_created_at",
            "tenant_id",
            "kind",
            text("created_at DESC"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # What the notice is about: the order number, the conversation, the motive.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class TenantUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Membership of a user in a tenant, with its role."""

    __tablename__ = "tenant_users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_users_tenant_id_user_id"),
        enum_check("role", TENANT_ROLES, "tenant_users_role"),
        enum_check("status", MEMBERSHIP_STATUSES, "tenant_users_status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")


class Invitation(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """Pending invitation for someone to join a tenant."""

    __tablename__ = "invitations"
    __table_args__ = (enum_check("role", INVITABLE_ROLES, "invitations_role"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    # 'owner' is deliberately absent: ownership transfers are not an invite.
    role: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invited_by: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
