"""Billing: what a business owes the platform and what it paid (Feature 009).

Money is `NUMERIC` in the database and `Decimal` in Python, never float
(Constitution). Prices and limits live in tables (`plans`, `plan_addons`), never
in code (RF-008).

v1 collects payments by hand (Giannier, 2026-10-14): a `Charge` is what is owed,
with a reference the business quotes when it pays by transfer or Yape; an admin
records the `Payment` against it, which has the same effect a gateway webhook
would. Every movement of money or state appends a row to `billing_ledger`,
which the database refuses to update or delete.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_check

SUBSCRIPTION_STATUSES = ("trialing", "active", "past_due", "paused", "canceled")
BILLING_CYCLES = ("monthly", "annual")
CHARGE_KINDS = ("subscription", "renewal", "upgrade", "addon")
CHARGE_STATUSES = ("pending", "paid", "canceled", "expired")
PAYMENT_STATUSES = ("approved", "failed", "refunded")

Money = Numeric(10, 2)


class Subscription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One per business: its plan, its period and where it stands."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        enum_check("status", SUBSCRIPTION_STATUSES, "subscriptions_status"),
        enum_check("billing_cycle", BILLING_CYCLES, "subscriptions_billing_cycle"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, unique=True
    )
    plan_id: Mapped[str] = mapped_column(Text, ForeignKey("plans.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    billing_cycle: Mapped[str] = mapped_column(Text, nullable=False)
    # The price this business pays until its next renewal, whatever the list says (C-004).
    price_locked_usd: Mapped[Decimal] = mapped_column(Money, nullable=False)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # A downgrade waits for the end of the period.
    pending_plan_id: Mapped[str | None] = mapped_column(Text, ForeignKey("plans.id"), nullable=True)
    pending_cycle: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Reminders already sent for the current grace (1, 3, 5), so a job re-run sends none twice.
    reminders_sent: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class Charge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What a business owes: the amount, why, and the reference it pays with."""

    __tablename__ = "charges"
    __table_args__ = (
        enum_check("kind", CHARGE_KINDS, "charges_kind"),
        enum_check("status", CHARGE_STATUSES, "charges_status"),
        CheckConstraint("amount_usd >= 0", name="charges_amount_not_negative"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("subscriptions.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    plan_id: Mapped[str | None] = mapped_column(Text, ForeignKey("plans.id"), nullable=True)
    addon_id: Mapped[str | None] = mapped_column(Text, ForeignKey("plan_addons.id"), nullable=True)
    billing_cycle: Mapped[str | None] = mapped_column(Text, nullable=True)
    list_amount_usd: Mapped[Decimal] = mapped_column(Money, nullable=False)
    discount_usd: Mapped[Decimal] = mapped_column(Money, nullable=False, server_default="0")
    credit_usd: Mapped[Decimal] = mapped_column(Money, nullable=False, server_default="0")
    amount_usd: Mapped[Decimal] = mapped_column(Money, nullable=False)
    coupon_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Short, unambiguous, quoted by the business when it pays by transfer or Yape.
    reference: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    # The period a subscription or renewal charge pays for.
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Payment(Base, UUIDPrimaryKeyMixin):
    """Money received, against a charge. Idempotent by the provider's payment id."""

    __tablename__ = "payments"
    __table_args__ = (
        enum_check("kind", CHARGE_KINDS, "payments_kind"),
        enum_check("status", PAYMENT_STATUSES, "payments_status"),
        CheckConstraint("amount_usd > 0", name="payments_amount_positive"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("subscriptions.id"), nullable=True
    )
    charge_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("charges.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    # The bank operation number or the gateway's payment id: a replay cannot pay twice.
    provider_payment_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Money, nullable=False)
    amount_local: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    currency_local: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    registered_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PlanAddon(Base):
    """A one-off pack bought for the current period (RF-005)."""

    __tablename__ = "plan_addons"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ai_messages: Mapped[int] = mapped_column(Integer, nullable=False)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class UsagePeriod(Base, UUIDPrimaryKeyMixin):
    """AI messages per billing period (RF-004): the plan's limit plus the packs bought."""

    __tablename__ = "usage_periods"
    __table_args__ = (UniqueConstraint("tenant_id", "period_start"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ai_messages_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ai_messages_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class Coupon(Base):
    """A percentage off, created by an admin only (RF-006)."""

    __tablename__ = "coupons"
    __table_args__ = (CheckConstraint("percent_off BETWEEN 1 AND 100", name="coupons_percent_off"),)

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    percent_off: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_redemptions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CouponRedemption(Base):
    """One use per business."""

    __tablename__ = "coupon_redemptions"

    coupon_code: Mapped[str] = mapped_column(Text, ForeignKey("coupons.code"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True
    )
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingLedger(Base):
    """Append-only record of every money and state movement (the database refuses edits)."""

    __tablename__ = "billing_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    amount_usd: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
