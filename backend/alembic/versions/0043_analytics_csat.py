"""Satisfaction surveys, Leo's NPS, A/B campaigns and their settings (Feature 013, TASK-1301)

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043"
down_revision: str | Sequence[str] | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "csat_surveys",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "conversation_id",
            UUID,
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id", UUID, sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("lead_id", UUID, sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "operator_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "message_id", UUID, sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="sent"),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('sent', 'answered', 'expired')",
            name=op.f("ck_csat_surveys_csat_surveys_status"),
        ),
        sa.CheckConstraint(
            "trigger IN ('ganado', 'cerrada')", name=op.f("ck_csat_surveys_csat_surveys_trigger")
        ),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 1 AND 5",
            name=op.f("ck_csat_surveys_csat_surveys_score"),
        ),
    )
    op.create_index("ix_csat_surveys_tenant_id_sent_at", "csat_surveys", ["tenant_id", "sent_at"])
    op.create_index("ix_csat_surveys_contact_id_sent_at", "csat_surveys", ["contact_id", "sent_at"])

    op.create_table(
        "nps_responses",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cycle", sa.Date(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("dismissed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "cycle", name="uq_nps_responses_tenant_id_user_id_cycle"
        ),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 10",
            name=op.f("ck_nps_responses_nps_responses_score"),
        ),
    )

    op.add_column(
        "campaigns",
        sa.Column(
            "template_b_id",
            UUID,
            sa.ForeignKey(
                "message_templates.id", name=op.f("fk_campaigns_template_b_id_message_templates")
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "campaign_recipients",
        sa.Column("variant", sa.Text(), nullable=False, server_default="A"),
    )
    op.create_check_constraint(
        "campaign_recipients_variant", "campaign_recipients", "variant IN ('A', 'B')"
    )
    op.add_column(
        "dashboard_settings",
        sa.Column("csat_activo", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "dashboard_settings",
        sa.Column("reporte_mensual", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_column("dashboard_settings", "reporte_mensual")
    op.drop_column("dashboard_settings", "csat_activo")
    op.drop_constraint(
        op.f("ck_campaign_recipients_campaign_recipients_variant"),
        "campaign_recipients",
        type_="check",
    )
    op.drop_column("campaign_recipients", "variant")
    op.drop_column("campaigns", "template_b_id")
    op.drop_table("nps_responses")
    op.drop_table("csat_surveys")
