"""Billing: prices, subscriptions, charges, payments, usage periods, coupons and ledger (Feature 009, TASK-901)

* `plans`: list prices (Giannier, 2026-10-14: the spec's): Emprende US$29, Crece US$59, Pro US$99 a
  month; annual -20 % (`price_usd_year`). The trial has no price: it is not bought.
* `plan_addons`: +500 AI messages for US$8.
* `subscriptions`, `charges` (what is owed, with a reference to pay by hand), `payments` (what
  arrived, unique by the provider's payment id), `usage_periods` (messages per billing period,
  backfilled from `tenants.ai_messages_used`), `coupons` and `coupon_redemptions`.
* `billing_ledger`: append-only; a trigger refuses UPDATE and DELETE.
* `receipt_number_seq`: the correlative of the receipts (LEO-000001...).

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
MONEY = sa.Numeric(10, 2)


def _id() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
    ]


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.add_column("plans", sa.Column("price_usd_year", sa.Numeric(8, 2), nullable=True))
    op.execute(
        "UPDATE plans SET price_usd_month = v.month, price_usd_year = v.year FROM (VALUES "
        "('emprende', 29.00, 278.40), ('crece', 59.00, 566.40), ('pro', 99.00, 950.40)"
        ") AS v(id, month, year) WHERE plans.id = v.id"
    )

    op.create_table(
        "plan_addons",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("ai_messages", sa.Integer(), nullable=False),
        sa.Column("price_usd", sa.Numeric(8, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.execute(
        "INSERT INTO plan_addons (id, name, ai_messages, price_usd) "
        "VALUES ('extra_500_msgs', '500 mensajes extra', 500, 8.00)"
    )

    op.create_table(
        "subscriptions",
        _id(),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("plan_id", sa.Text(), sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("billing_cycle", sa.Text(), nullable=False),
        sa.Column("price_locked_usd", MONEY, nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pending_plan_id", sa.Text(), sa.ForeignKey("plans.id"), nullable=True),
        sa.Column("pending_cycle", sa.Text(), nullable=True),
        sa.Column("provider_ref", sa.Text(), nullable=True),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reminders_sent",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        *_timestamps(),
        sa.CheckConstraint(
            _in("status", ("trialing", "active", "past_due", "paused", "canceled")),
            name=op.f("ck_subscriptions_subscriptions_status"),
        ),
        sa.CheckConstraint(
            _in("billing_cycle", ("monthly", "annual")),
            name=op.f("ck_subscriptions_subscriptions_billing_cycle"),
        ),
    )

    kinds = ("subscription", "renewal", "upgrade", "addon")
    op.create_table(
        "charges",
        _id(),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("subscription_id", UUID, sa.ForeignKey("subscriptions.id"), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("plan_id", sa.Text(), sa.ForeignKey("plans.id"), nullable=True),
        sa.Column("addon_id", sa.Text(), sa.ForeignKey("plan_addons.id"), nullable=True),
        sa.Column("billing_cycle", sa.Text(), nullable=True),
        sa.Column("list_amount_usd", MONEY, nullable=False),
        sa.Column("discount_usd", MONEY, nullable=False, server_default="0"),
        sa.Column("credit_usd", MONEY, nullable=False, server_default="0"),
        sa.Column("amount_usd", MONEY, nullable=False),
        sa.Column("coupon_code", sa.Text(), nullable=True),
        sa.Column("reference", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(_in("kind", kinds), name=op.f("ck_charges_charges_kind")),
        sa.CheckConstraint(
            _in("status", ("pending", "paid", "canceled", "expired")),
            name=op.f("ck_charges_charges_status"),
        ),
        sa.CheckConstraint("amount_usd >= 0", name=op.f("ck_charges_charges_amount_not_negative")),
    )

    op.create_table(
        "payments",
        _id(),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("subscription_id", UUID, sa.ForeignKey("subscriptions.id"), nullable=True),
        sa.Column("charge_id", UUID, sa.ForeignKey("charges.id"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_payment_id", sa.Text(), nullable=False, unique=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("amount_usd", MONEY, nullable=False),
        sa.Column("amount_local", MONEY, nullable=True),
        sa.Column("currency_local", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("receipt_number", sa.Text(), nullable=False, unique=True),
        sa.Column("registered_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(_in("kind", kinds), name=op.f("ck_payments_payments_kind")),
        sa.CheckConstraint(
            _in("status", ("approved", "failed", "refunded")),
            name=op.f("ck_payments_payments_status"),
        ),
        sa.CheckConstraint("amount_usd > 0", name=op.f("ck_payments_payments_amount_positive")),
    )
    op.execute("CREATE SEQUENCE receipt_number_seq START 1")

    op.create_table(
        "usage_periods",
        _id(),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ai_messages_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_messages_limit", sa.Integer(), nullable=False),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("tenant_id", "period_start"),
    )
    # The current period of every business, from the trial counter (RF-004).
    op.execute(
        "INSERT INTO usage_periods (tenant_id, period_start, period_end, ai_messages_used, "
        "ai_messages_limit) SELECT t.id, t.created_at, "
        "COALESCE(t.trial_ends_at, t.created_at + interval '1 month'), t.ai_messages_used, "
        "p.max_ai_messages FROM tenants t JOIN plans p ON p.id = t.plan_id"
    )

    op.create_table(
        "coupons",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("percent_off", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "percent_off BETWEEN 1 AND 100", name=op.f("ck_coupons_coupons_percent_off")
        ),
    )
    op.create_table(
        "coupon_redemptions",
        sa.Column("coupon_code", sa.Text(), sa.ForeignKey("coupons.code"), primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column(
            "redeemed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "billing_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID, nullable=False, index=True),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("amount_usd", MONEY, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute(
        "CREATE FUNCTION billing_ledger_append_only() RETURNS trigger AS $$ BEGIN "
        "RAISE EXCEPTION 'billing_ledger is append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER billing_ledger_append_only BEFORE UPDATE OR DELETE ON billing_ledger "
        "FOR EACH ROW EXECUTE FUNCTION billing_ledger_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER billing_ledger_append_only ON billing_ledger")
    op.execute("DROP FUNCTION billing_ledger_append_only()")
    op.drop_table("billing_ledger")
    op.drop_table("coupon_redemptions")
    op.drop_table("coupons")
    op.drop_table("usage_periods")
    op.execute("DROP SEQUENCE receipt_number_seq")
    op.drop_table("payments")
    op.drop_table("charges")
    op.drop_table("subscriptions")
    op.drop_table("plan_addons")
    op.execute("UPDATE plans SET price_usd_month = NULL")
    op.drop_column("plans", "price_usd_year")
