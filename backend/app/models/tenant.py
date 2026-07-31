"""Tenants, their memberships, and registration anti-abuse.

A tenant is the unit of isolation: every business table in the system carries
`tenant_id` and every repository query filters on it (Constitution).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import CITEXT
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


class TrialPhoneLock(Base, CreatedAtMixin):
    """One live trial per verified contact phone (Clarification 005).

    The data model first sketches this as a partial unique index over a
    subquery. PostgreSQL does not allow subqueries in index expressions, so
    the rule is enforced by this table instead: the registration service
    inserts the lock in the same transaction that creates the trial tenant,
    and the primary key is what actually rejects the second attempt.
    """

    __tablename__ = "trial_phone_locks"

    phone_e164: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


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
