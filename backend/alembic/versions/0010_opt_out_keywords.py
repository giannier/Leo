"""agent_configs.opt_out_keywords: what a customer writes to be left alone

HU-03. Somebody who says STOP must not be answered again, and the word they
actually use is local: "BAJA" from a Peruvian customer, "STOP" from anyone who
has used an English service. Both are the default; a tenant can add their own.

Not nullable, with the defaults as the server default, because "no keywords
configured" and "this business honours no opt-out" must never be the same
state. The second one is a compliance problem, and a NULL would make it
indistinguishable from a row nobody has edited.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_configs",
        sa.Column(
            "opt_out_keywords",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default='["stop", "baja"]',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_configs", "opt_out_keywords")
