"""Campaigns, templates, opt-ins, tags and Meta's rates (Feature 011, TASK-1101)

* `message_templates`, `contact_optins`, `optin_imports`, `contact_tags`, `campaigns`,
  `campaign_recipients`, `meta_rates` (see `app/models/campaigns.py`).
* `messages.msg_type` gains `template`; `conversations.origin_campaign_id`.
* `plans.max_campaigns_month` and `max_recipients_per_campaign` (C-108).
* `agent_configs.optin_preguntar` (C-102) and `seguimiento_plantilla_id` (C-110).
* `whatsapp_numbers.limite_diario` (C-106).
* Meta's published rates for Peru (C-107), to verify before the pilot.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041"
down_revision: str | Sequence[str] | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()

TYPES_BEFORE = (
    "text",
    "image",
    "document",
    "audio",
    "sticker",
    "location",
    "contact",
    "unsupported",
    "video",
    "interactive",
    "event",
)
TYPES_AFTER = (*TYPES_BEFORE, "template")

# (plan, campaigns a month, people per campaign), C-108.
PLAN_LIMITS = (("trial", 1, 100), ("emprende", 2, 500), ("crece", 8, 2000), ("pro", 20, 10000))

# Meta's rates for Peru, per delivered template (C-107).
RATES = (
    ("PE", "marketing", Decimal("0.07030")),
    ("PE", "utility", Decimal("0.02000")),
)


def _types(values: Sequence[str]) -> str:
    return "msg_type IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _id() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _stamps() -> list[sa.Column]:
    return [
        _created(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "message_templates",
        _id(),
        _tenant(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False, server_default="es"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("footer", sa.Text(), nullable=True),
        sa.Column(
            "header_media_asset_id",
            UUID,
            sa.ForeignKey("media_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("variables", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("provider_template_id", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("library_key", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.UniqueConstraint(
            "tenant_id", "name", "language", name="uq_message_templates_tenant_id_name_language"
        ),
        sa.CheckConstraint(
            _in("category", ("utility", "marketing")),
            name=op.f("ck_message_templates_message_templates_category"),
        ),
        sa.CheckConstraint(
            _in("status", ("draft", "pending", "approved", "rejected", "paused", "disabled")),
            name=op.f("ck_message_templates_message_templates_status"),
        ),
        sa.CheckConstraint(
            "name ~ '^[a-z0-9_]{1,60}$'", name=op.f("ck_message_templates_message_templates_name")
        ),
        sa.CheckConstraint(
            "char_length(body) BETWEEN 1 AND 1024",
            name=op.f("ck_message_templates_message_templates_body"),
        ),
    )

    op.create_table(
        "contact_optins",
        _id(),
        _tenant(),
        sa.Column(
            "contact_id", UUID, sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB, nullable=False, server_default="{}"),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.UniqueConstraint(
            "tenant_id", "contact_id", name="uq_contact_optins_tenant_id_contact_id"
        ),
        sa.CheckConstraint(
            _in("source", ("conversation", "import", "form")),
            name=op.f("ck_contact_optins_contact_optins_source"),
        ),
        sa.CheckConstraint(
            _in("status", ("asked", "granted", "declined", "revoked")),
            name=op.f("ck_contact_optins_contact_optins_status"),
        ),
    )

    op.create_table(
        "optin_imports",
        _id(),
        _tenant(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("declaration", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("imported", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
        _created(),
    )
    op.create_index(
        "ix_optin_imports_tenant_id_created_at", "optin_imports", ["tenant_id", "created_at"]
    )

    op.create_table(
        "contact_tags",
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column(
            "contact_id", UUID, sa.ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("tag", sa.Text(), primary_key=True),
        _created(),
        sa.CheckConstraint(
            "char_length(tag) BETWEEN 1 AND 30", name=op.f("ck_contact_tags_contact_tags_tag")
        ),
    )
    op.create_index("ix_contact_tags_tenant_id_tag", "contact_tags", ["tenant_id", "tag"])

    op.create_table(
        "campaigns",
        _id(),
        _tenant(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("template_id", UUID, sa.ForeignKey("message_templates.id"), nullable=False),
        sa.Column("segment", JSONB, nullable=False, server_default="{}"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_from", sa.Text(), nullable=False, server_default="09:00"),
        sa.Column("window_to", sa.Text(), nullable=False, server_default="20:00"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("pause_reason", sa.Text(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column(
            "created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.CheckConstraint(
            _in("status", ("draft", "scheduled", "sending", "paused", "completed", "canceled")),
            name=op.f("ck_campaigns_campaigns_status"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 80", name=op.f("ck_campaigns_campaigns_name")
        ),
    )
    op.create_index("ix_campaigns_status", "campaigns", ["status"])

    op.create_table(
        "campaign_recipients",
        sa.Column(
            "campaign_id",
            UUID,
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "contact_id", UUID, sa.ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True
        ),
        _tenant(),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column(
            "message_id", UUID, sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "conversation_id",
            UUID,
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.CheckConstraint(
            _in(
                "status",
                ("pending", "sent", "delivered", "read", "replied", "failed", "skipped"),
            ),
            name=op.f("ck_campaign_recipients_campaign_recipients_status"),
        ),
    )
    op.create_index(
        "ix_campaign_recipients_tenant_id_sent_at", "campaign_recipients", ["tenant_id", "sent_at"]
    )
    op.create_index("ix_campaign_recipients_contact_id", "campaign_recipients", ["contact_id"])
    op.create_index("ix_campaign_recipients_message_id", "campaign_recipients", ["message_id"])

    rates = op.create_table(
        "meta_rates",
        sa.Column("country", sa.Text(), primary_key=True),
        sa.Column("category", sa.Text(), primary_key=True),
        sa.Column("valid_from", sa.Date(), primary_key=True),
        sa.Column("rate_usd", sa.Numeric(8, 5), nullable=False),
        sa.CheckConstraint(
            _in("category", ("utility", "marketing")),
            name=op.f("ck_meta_rates_meta_rates_category"),
        ),
    )
    op.bulk_insert(
        rates,
        [
            {"country": c, "category": k, "valid_from": date(2025, 7, 1), "rate_usd": r}
            for c, k, r in RATES
        ],
    )

    op.drop_constraint(op.f("ck_messages_messages_msg_type"), "messages", type_="check")
    op.create_check_constraint("messages_msg_type", "messages", _types(TYPES_AFTER))
    op.add_column(
        "conversations",
        sa.Column(
            "origin_campaign_id",
            UUID,
            sa.ForeignKey(
                "campaigns.id",
                ondelete="SET NULL",
                name=op.f("fk_conversations_origin_campaign_id_campaigns"),
            ),
            nullable=True,
        ),
    )

    op.add_column(
        "plans",
        sa.Column("max_campaigns_month", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "plans",
        sa.Column("max_recipients_per_campaign", sa.Integer(), nullable=False, server_default="0"),
    )
    for plan, campaigns, people in PLAN_LIMITS:
        op.execute(
            sa.text(
                "UPDATE plans SET max_campaigns_month = :c, max_recipients_per_campaign = :p "
                "WHERE id = :id"
            ).bindparams(c=campaigns, p=people, id=plan)
        )

    op.add_column(
        "agent_configs",
        sa.Column("optin_preguntar", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "agent_configs",
        sa.Column(
            "seguimiento_plantilla_id",
            UUID,
            sa.ForeignKey(
                "message_templates.id",
                ondelete="SET NULL",
                name=op.f("fk_agent_configs_seguimiento_plantilla_id_message_templates"),
            ),
            nullable=True,
        ),
    )
    op.add_column("whatsapp_numbers", sa.Column("limite_diario", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("whatsapp_numbers", "limite_diario")
    op.drop_column("agent_configs", "seguimiento_plantilla_id")
    op.drop_column("agent_configs", "optin_preguntar")
    op.drop_column("plans", "max_recipients_per_campaign")
    op.drop_column("plans", "max_campaigns_month")
    op.drop_column("conversations", "origin_campaign_id")
    op.execute("DELETE FROM messages WHERE msg_type = 'template'")
    op.drop_constraint(op.f("ck_messages_messages_msg_type"), "messages", type_="check")
    op.create_check_constraint("messages_msg_type", "messages", _types(TYPES_BEFORE))
    op.drop_table("meta_rates")
    op.drop_table("campaign_recipients")
    op.drop_table("campaigns")
    op.drop_table("contact_tags")
    op.drop_table("optin_imports")
    op.drop_table("contact_optins")
    op.drop_table("message_templates")
