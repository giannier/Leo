"""Dashboard: daily metrics, model costs, cost per trace, dashboard settings (Feature 010, TASK-1001)

* `metrics_daily`: one row per business per local day, recomputable (TASK-1002).
* `model_costs`: USD per 1 000 tokens in and out, from a date on, kept by an admin. No price is seeded:
  the platform's own prices are entered in the panel, and a trace without one has no cost.
* `agent_traces.cost_usd`: computed when the trace is written.
* `dashboard_settings`: minutes a person would spend per conversation (default 4, HU-03).

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


INTEGER = sa.Integer()


def _count(name: str, kind: sa.types.TypeEngine = INTEGER) -> sa.Column:
    return sa.Column(name, kind, nullable=False, server_default="0")


def upgrade() -> None:
    op.create_table(
        "metrics_daily",
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        _count("conversations"),
        _count("conversations_agent_only"),
        _count("conversations_with_person"),
        _count("messages_in"),
        _count("messages_out_agent"),
        _count("messages_out_person"),
        _count("first_responses"),
        _count("first_response_seconds", sa.BigInteger()),
        _count("handovers"),
        sa.Column("handover_reasons", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _count("orders_confirmed"),
        sa.Column("sales", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _count("tokens_in", sa.BigInteger()),
        _count("tokens_out", sa.BigInteger()),
        sa.Column("cost_usd", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "model_costs",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_per_1k_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("output_per_1k_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_model_costs_model_valid_from", "model_costs", ["model", "valid_from"])
    op.add_column("agent_traces", sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True))
    op.create_table(
        "dashboard_settings",
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("minutes_per_conversation", sa.Integer(), nullable=False, server_default="4"),
    )


def downgrade() -> None:
    op.drop_table("dashboard_settings")
    op.drop_column("agent_traces", "cost_usd")
    op.drop_index("ix_model_costs_model_valid_from", table_name="model_costs")
    op.drop_table("model_costs")
    op.drop_table("metrics_daily")
