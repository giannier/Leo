"""Pilot mode: only a share of the customers go to the assistant (feature 019, phase C).

Revision ID: 0058

Revises: 0057

A business that is afraid of putting an agent in front of everybody on the first day can start with a
share of them (10 %, 25 %, 50 %) and let its team answer the rest, then raise it. The share is decided per
person, never per message, so a customer is always answered by the same side. 100 % (the default) changes
nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058"
down_revision: str | Sequence[str] | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_configs",
        sa.Column("piloto_pct", sa.Integer(), nullable=False, server_default="100"),
    )
    op.create_check_constraint(
        op.f("ck_agent_configs_agent_configs_piloto_pct"),
        "agent_configs",
        "piloto_pct BETWEEN 0 AND 100",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_configs_agent_configs_piloto_pct"), "agent_configs", type_="check"
    )
    op.drop_column("agent_configs", "piloto_pct")
