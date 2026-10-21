"""Quoting for real (feature 019, phase B).

Revision ID: 0054
Revises: 0053

* `products.datos_a_pedir`: what the agent asks the customer before handing a quote over, one item per service
  ("para cuántas personas", "para cuándo"), up to six.
* `leads.quote_*`: the quote a customer asked for lives on the lead (one active lead per contact), not in a table of
  its own: the status (`solicitada` when the agent hands it over, `enviada` when a person sends it), the structured
  summary and data the agent collected, and when each happened. The amount quoted is the lead's `amount_estimated`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054"
down_revision: str | Sequence[str] | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "datos_a_pedir",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_products_products_datos_a_pedir_count"),
        "products",
        "cardinality(datos_a_pedir) <= 6",
    )
    op.add_column("leads", sa.Column("quote_status", sa.Text(), nullable=True))
    op.add_column("leads", sa.Column("quote_summary", sa.Text(), nullable=True))
    op.add_column(
        "leads",
        sa.Column(
            "quote_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "leads", sa.Column("quote_requested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("leads", sa.Column("quote_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f("ck_leads_leads_quote_status"),
        "leads",
        "quote_status IS NULL OR quote_status IN ('solicitada', 'enviada')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_leads_leads_quote_status"), "leads", type_="check")
    for column in (
        "quote_sent_at",
        "quote_requested_at",
        "quote_data",
        "quote_summary",
        "quote_status",
    ):
        op.drop_column("leads", column)
    op.drop_constraint(op.f("ck_products_products_datos_a_pedir_count"), "products", type_="check")
    op.drop_column("products", "datos_a_pedir")
