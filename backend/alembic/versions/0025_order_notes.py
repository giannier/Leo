"""orders.notas: what the customer asked while the business reviewed the order

Feature 004 (Motor de ventas), Milestone B, decision 8. An order waiting for the
business (`por_confirmar`) cannot be changed. When the customer asks for a change
anyway, the assistant explains why and leaves the request on the order for the
operator, who reads it next to the receipt before deciding.

`notas` is a JSON list of `{en, texto, origen}`, newest last. It never holds more
than the customer's own words about the order.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "notas",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("orders", "notas")
