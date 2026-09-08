"""agent_traces: record what the catalogue post-guard did

The catalogue said "Leche" and the agent offered "Leche pausterizada". The
guard that now catches that lives in `app/agent/catalog_guard.py`; this column
is where it says so, so a tenant looking at a reply can tell "the model
renamed a product and was corrected" from "the model answered normally".

Nullable and null on an ordinary turn, which is nearly all of them: a non-null
value is itself the signal, and a default of `{}` on every row would bury the
handful worth reading.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_traces",
        sa.Column("catalog_guard", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_traces", "catalog_guard")
