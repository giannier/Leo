"""The period a price covers: once, per month or per year.

Revision ID: 0050
Revises: 0049

A plan or a hosting is sold "S/ 110 al año" or "S/ 40 al mes"; without the period the assistant said
"S/ 110.00" and the customer had to guess. Existing products stay "once" (`unico`). The period is how a
price is shown and spoken: orders and totals still add the amount as it is.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | Sequence[str] | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products", sa.Column("periodo", sa.Text(), nullable=False, server_default="unico")
    )
    op.create_check_constraint(
        "ck_products_periodo", "products", "periodo IN ('unico', 'mes', 'ano')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_products_periodo", "products", type_="check")
    op.drop_column("products", "periodo")
