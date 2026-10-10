"""conversation_state and conversation_stage_events

Feature 004 (Motor de ventas), TASK-402.

`conversation_state` is one row per conversation: the stage it is in, the
funnel column that stage maps to, and the facts that only exist at conversation
level. `conversation_stage_events` is the append-only history of every change.

Adaptations to `data-model.md` (recorded in `conciliacion.md`, "Adaptaciones"):

* No `ON DELETE CASCADE`. Nothing else in this schema cascades from `tenants`
  or `conversations`, and a cascade here would let one stray DELETE erase the
  history that explains a sale.
* Index names follow the repository convention (`ix_<table>_<columns>`).
* `conversation_stage_events.created_at` defaults to `clock_timestamp()`, as
  `messages` does: one turn can cross several stages inside one transaction and
  `now()` would stamp them identically.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNNEL_CODES = (
    "NUEVO",
    "ATENDIDO",
    "NEGOCIACION",
    "CUENTAS_ENVIADAS",
    "EN_VERIFICACION",
    "GANADO",
    "DERIVADO",
    "PERDIDO",
)


def upgrade() -> None:
    funnel = ", ".join(f"'{code}'" for code in FUNNEL_CODES)
    op.create_table(
        "conversation_state",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("template_key", sa.Text(), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("stage_key", sa.Text(), nullable=False),
        sa.Column("funnel_code", sa.Text(), nullable=False),
        sa.Column(
            "facts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("is_sandbox", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "stage_entered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"funnel_code IN ({funnel})",
            name=op.f("ck_conversation_state_funnel_code"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_state_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_conversation_state_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("conversation_id", name=op.f("pk_conversation_state")),
    )
    op.create_index(
        "ix_conversation_state_tenant_id_stage_key",
        "conversation_state",
        ["tenant_id", "stage_key"],
    )
    op.create_index(
        "ix_conversation_state_tenant_id_funnel_code",
        "conversation_state",
        ["tenant_id", "funnel_code"],
    )

    op.create_table(
        "conversation_stage_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("from_stage", sa.Text(), nullable=True),
        sa.Column("to_stage", sa.Text(), nullable=False),
        sa.Column("from_funnel", sa.Text(), nullable=True),
        sa.Column("to_funnel", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("facts_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_stage_events_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_conversation_stage_events_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_stage_events")),
    )
    op.create_index(
        "ix_conversation_stage_events_conversation_id_created_at",
        "conversation_stage_events",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_conversation_stage_events_tenant_id_created_at",
        "conversation_stage_events",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_stage_events_tenant_id_created_at",
        table_name="conversation_stage_events",
    )
    op.drop_index(
        "ix_conversation_stage_events_conversation_id_created_at",
        table_name="conversation_stage_events",
    )
    op.drop_table("conversation_stage_events")
    op.drop_index("ix_conversation_state_tenant_id_funnel_code", table_name="conversation_state")
    op.drop_index("ix_conversation_state_tenant_id_stage_key", table_name="conversation_state")
    op.drop_table("conversation_state")
