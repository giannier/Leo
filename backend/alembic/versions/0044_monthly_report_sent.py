"""The month whose report was last emailed, so it goes once (Feature 013, C-108)

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | Sequence[str] | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("dashboard_settings", sa.Column("reporte_ultimo_mes", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("dashboard_settings", "reporte_ultimo_mes")
