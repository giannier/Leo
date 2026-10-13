"""tenants.es_prueba: accounts created by the end-to-end suite

TASK-455. Every Playwright run registers new businesses (`e2e-...@example.com`)
and they used to pile up in the platform panel next to the real ones. After the
suite runs, `scripts/mark_e2e_accounts.py` marks them, and the panel hides them
unless asked.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants", sa.Column("es_prueba", sa.Boolean(), server_default=sa.false(), nullable=False)
    )


def downgrade() -> None:
    op.drop_column("tenants", "es_prueba")
