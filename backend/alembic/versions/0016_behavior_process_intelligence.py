"""sales behaviour, process options and intelligence levels per plan

Feature 004 (Motor de ventas), TASK-404.

* `agent_configs` gets the behaviour the tenant tunes (pace, detail, follow-up,
  handover pause), the process template it runs, that template's options and
  its edited system messages, and the model tier it chose.
* `plans.allowed_intelligence` says which tiers a plan may choose.
* `process_stage_overrides` keeps the stage instructions a tenant edited.

Every column has a default, so existing tenants land on `productos`,
`equilibrado`, `moderado` and `economico` without a backfill.

Adaptations to `data-model.md` (see `conciliacion.md`, "Adaptaciones"):

* No `ON DELETE CASCADE` on `tenant_id` (the repository convention).
* CHECK names follow `NAMING_CONVENTION`.
* `plans.allowed_intelligence` also gets a CHECK (at least one level, only known
  levels): an empty list would leave a plan with no model to answer with.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (name, type, server default); order is the order of the columns in the table.
AGENT_CONFIG_COLUMNS: tuple[tuple[str, sa.types.TypeEngine, str], ...] = (
    ("ritmo", sa.Text(), "equilibrado"),
    ("detalle", sa.Text(), "moderado"),
    ("seguimiento_activo", sa.Boolean(), "false"),
    ("seguimiento_horas", sa.Integer(), "4"),
    ("seguimiento_max", sa.Integer(), "2"),
    ("nivel_inteligencia", sa.Text(), "economico"),
    ("derivacion_pausa_horas", sa.Integer(), "6"),
    ("process_template_key", sa.Text(), "productos"),
    ("process_options", postgresql.JSONB(astext_type=sa.Text()), "{}"),
    ("system_messages", postgresql.JSONB(astext_type=sa.Text()), "{}"),
)

# (full constraint name, expression)
AGENT_CONFIG_CHECKS = (
    ("ck_agent_configs_ritmo", "ritmo IN ('directo', 'equilibrado', 'paso_a_paso')"),
    ("ck_agent_configs_detalle", "detalle IN ('breve', 'moderado', 'extenso')"),
    ("ck_agent_configs_seguimiento_horas", "seguimiento_horas BETWEEN 1 AND 20"),
    ("ck_agent_configs_seguimiento_max", "seguimiento_max BETWEEN 1 AND 3"),
    (
        "ck_agent_configs_nivel_inteligencia",
        "nivel_inteligencia IN ('economico', 'medio', 'mejor')",
    ),
    ("ck_agent_configs_derivacion_pausa_horas", "derivacion_pausa_horas BETWEEN 1 AND 72"),
)


def upgrade() -> None:
    for name, column_type, default in AGENT_CONFIG_COLUMNS:
        op.add_column(
            "agent_configs",
            sa.Column(name, column_type, server_default=default, nullable=False),
        )
    for name, expression in AGENT_CONFIG_CHECKS:
        op.create_check_constraint(op.f(name), "agent_configs", expression)

    op.add_column(
        "plans",
        sa.Column(
            "allowed_intelligence",
            sa.ARRAY(sa.Text()),
            server_default="{economico}",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_plans_allowed_intelligence"),
        "plans",
        "cardinality(allowed_intelligence) >= 1 "
        "AND allowed_intelligence <@ ARRAY['economico', 'medio', 'mejor']::text[]",
    )

    op.create_table(
        "process_stage_overrides",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("template_key", sa.Text(), nullable=False),
        sa.Column("stage_key", sa.Text(), nullable=False),
        sa.Column("instrucciones", sa.Text(), nullable=True),
        sa.Column("instrucciones_seguimiento", sa.Text(), nullable=True),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(instrucciones) <= 4000",
            name=op.f("ck_process_stage_overrides_instrucciones_length"),
        ),
        sa.CheckConstraint(
            "char_length(instrucciones_seguimiento) <= 1000",
            name=op.f("ck_process_stage_overrides_instrucciones_seguimiento_length"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_process_stage_overrides_tenant_id_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_process_stage_overrides_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "template_key", "stage_key", name=op.f("pk_process_stage_overrides")
        ),
    )


def downgrade() -> None:
    op.drop_table("process_stage_overrides")
    op.drop_constraint(op.f("ck_plans_allowed_intelligence"), "plans", type_="check")
    op.drop_column("plans", "allowed_intelligence")
    for name, _ in AGENT_CONFIG_CHECKS:
        op.drop_constraint(op.f(name), "agent_configs", type_="check")
    for name, _, _ in reversed(AGENT_CONFIG_COLUMNS):
        op.drop_column("agent_configs", name)
