"""Meta's rates since 1 October 2026: service messages are billed (Feature 011, C-111)

`meta_rates.category` gains `service` (free-form replies inside the 24-hour window, after the
1,000 free a month per number), and Peru's rates in force from 2026-10-01, the highest figure
the sources give so that no estimate understates the cost. To verify with the real invoice.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | Sequence[str] | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OCTOBER = date(2026, 10, 1)
RATES = (
    ("PE", "marketing", Decimal("0.08085")),
    ("PE", "utility", Decimal("0.03450")),
    ("PE", "service", Decimal("0.03000")),
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_meta_rates_meta_rates_category"), "meta_rates", type_="check")
    op.create_check_constraint(
        "meta_rates_category", "meta_rates", "category IN ('utility', 'marketing', 'service')"
    )
    rates = sa.table(
        "meta_rates",
        sa.column("country", sa.Text()),
        sa.column("category", sa.Text()),
        sa.column("valid_from", sa.Date()),
        sa.column("rate_usd", sa.Numeric(8, 5)),
    )
    op.bulk_insert(
        rates,
        [{"country": c, "category": k, "valid_from": OCTOBER, "rate_usd": r} for c, k, r in RATES],
    )


def downgrade() -> None:
    op.execute("DELETE FROM meta_rates WHERE category = 'service' OR valid_from = '2026-10-01'")
    op.drop_constraint(op.f("ck_meta_rates_meta_rates_category"), "meta_rates", type_="check")
    op.create_check_constraint(
        "meta_rates_category", "meta_rates", "category IN ('utility', 'marketing')"
    )
