"""What the agent does: capabilities (feature 019).

Revision ID: 0051
Revises: 0050

A business turns on what its agent does (attend, sell, quote; schedule and capture leads come later) instead of
choosing a process template by name. The template and the objective are derived from the capabilities, unless the
owner picked a template by hand (`plantilla_manual`). Each rubro suggests the capabilities that usually fit.
Existing businesses keep behaving as they did: they start with attend and sell.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051"
down_revision: str | Sequence[str] | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALL = "ARRAY['atender','vender','cotizar','agendar','captar']::text[]"
SUGGESTED = {
    "restaurante": ["atender", "vender"],
    "retail": ["atender", "vender"],
    "instituto": ["atender", "vender", "cotizar"],
    "salud": ["atender", "cotizar"],
    "servicios": ["atender", "vender", "cotizar"],
    "otros": ["atender", "vender"],
}


def upgrade() -> None:
    op.add_column(
        "agent_configs",
        sa.Column(
            "capacidades",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY['atender','vender']::text[]"),
        ),
    )
    op.create_check_constraint(
        "ck_agent_configs_capacidades",
        "agent_configs",
        f"capacidades <@ {ALL} AND 'atender' = ANY(capacidades)",
    )
    op.add_column(
        "agent_configs",
        sa.Column("plantilla_manual", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "rubros",
        sa.Column(
            "capacidades_sugeridas",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY['atender','vender']::text[]"),
        ),
    )
    bind = op.get_bind()
    for key, capacities in SUGGESTED.items():
        bind.execute(
            sa.text("UPDATE rubros SET capacidades_sugeridas = :c WHERE key = :k"),
            {"c": capacities, "k": key},
        )


def downgrade() -> None:
    op.drop_column("rubros", "capacidades_sugeridas")
    op.drop_column("agent_configs", "plantilla_manual")
    op.drop_constraint("ck_agent_configs_capacidades", "agent_configs", type_="check")
    op.drop_column("agent_configs", "capacidades")
