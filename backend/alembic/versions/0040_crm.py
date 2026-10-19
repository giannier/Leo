"""Mini-CRM of leads: stages, leads, their history, lost reasons (Feature 012, TASK-1201)

The default stages and lost reasons are created per business the first time the CRM reads them
(`CrmRepository.ensure_defaults`), so no tenant needs a backfill here.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040"
down_revision: str | Sequence[str] | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _id() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "pipeline_stages",
        _id(),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("semantic", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        *_stamps(),
        sa.UniqueConstraint("tenant_id", "key", name="uq_pipeline_stages_tenant_id_key"),
        sa.CheckConstraint(
            "semantic IS NULL OR semantic IN ('nuevo','interesado','por_pagar','ganado','perdido')",
            name=op.f("ck_pipeline_stages_pipeline_stages_semantic"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(label)) BETWEEN 1 AND 40",
            name=op.f("ck_pipeline_stages_pipeline_stages_label"),
        ),
    )

    op.create_table(
        "leads",
        _id(),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("contact_id", UUID, sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("conversation_id", UUID, sa.ForeignKey("conversations.id"), nullable=True),
        sa.Column("stage_id", UUID, sa.ForeignKey("pipeline_stages.id"), nullable=False),
        sa.Column("assigned_to", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("amount_estimated", sa.Numeric(12, 2), nullable=True),
        sa.Column("amount_real", sa.Numeric(12, 2), nullable=True),
        sa.Column("moneda", sa.CHAR(3), nullable=True),
        sa.Column("product_interest", sa.Text(), nullable=True),
        sa.Column("origen", sa.Text(), nullable=True),
        sa.Column("lost_reason", sa.Text(), nullable=True),
        sa.Column("order_id", UUID, sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("previous_lead_id", UUID, sa.ForeignKey("leads.id"), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("moved_by_person", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "stage_entered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.CheckConstraint(
            "status IN ('active', 'won', 'lost', 'discarded')", name=op.f("ck_leads_leads_status")
        ),
        sa.CheckConstraint(
            "(amount_estimated IS NULL OR amount_estimated >= 0) "
            "AND (amount_real IS NULL OR amount_real >= 0)",
            name=op.f("ck_leads_leads_amounts_not_negative"),
        ),
    )
    op.create_index(
        "uq_leads_active_per_contact",
        "leads",
        ["tenant_id", "contact_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_leads_tenant_id_stage_id", "leads", ["tenant_id", "stage_id"])
    op.create_index(
        "ix_leads_tenant_id_last_activity_at", "leads", ["tenant_id", "last_activity_at"]
    )

    op.create_table(
        "lead_events",
        _id(),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("lead_id", UUID, sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("actor_user_id", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("from_stage", sa.Text(), nullable=True),
        sa.Column("to_stage", sa.Text(), nullable=True),
        sa.Column("cause", sa.Text(), nullable=False),
        sa.Column(
            "detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "actor IN ('agent', 'user', 'system')", name=op.f("ck_lead_events_lead_events_actor")
        ),
    )
    op.create_index("ix_lead_events_lead_id_created_at", "lead_events", ["lead_id", "created_at"])
    # Append-only (RF-003): the history is never rewritten.
    op.execute(
        """
        CREATE FUNCTION lead_events_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'lead_events is append-only';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER lead_events_no_update BEFORE UPDATE ON lead_events
          FOR EACH ROW EXECUTE FUNCTION lead_events_append_only();
        """
    )

    op.create_table(
        "lost_reasons",
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "char_length(btrim(label)) BETWEEN 1 AND 60",
            name=op.f("ck_lost_reasons_lost_reasons_label"),
        ),
    )


def downgrade() -> None:
    op.drop_table("lost_reasons")
    op.execute("DROP TRIGGER IF EXISTS lead_events_no_update ON lead_events")
    op.execute("DROP FUNCTION IF EXISTS lead_events_append_only()")
    op.drop_table("lead_events")
    op.drop_table("leads")
    op.drop_table("pipeline_stages")
