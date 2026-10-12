"""products fit for sale

Feature 004 (Motor de ventas), TASK-406.

`products` gets what the sales engine needs to put a product in an order: how it
is sold (fixed price or quote), whether it is digital (no delivery address), the
bundle size that turns it into a package, whether a package accepts free-text
selections, and whether the assistant pushes it first.

Every column has a default, so existing products stay fixed-price, physical,
single and not highlighted without a backfill.

Adaptations to `data-model.md` (see `conciliacion.md`, "Adaptaciones"):

* `category` is not added: `products.category` already exists (migration 0001).
* CHECK names follow `NAMING_CONVENTION`.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("modo_venta", sa.Text(), server_default="precio_fijo", nullable=False),
    )
    op.add_column(
        "products", sa.Column("es_digital", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column("products", sa.Column("bundle_size", sa.Integer(), nullable=True))
    op.add_column(
        "products",
        sa.Column("bundle_permite_libres", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "products", sa.Column("destacado", sa.Boolean(), server_default="false", nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_products_modo_venta"),
        "products",
        "modo_venta IN ('precio_fijo', 'cotizacion')",
    )
    op.create_check_constraint(
        op.f("ck_products_bundle_size_range"),
        "products",
        "bundle_size IS NULL OR bundle_size BETWEEN 2 AND 50",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_products_bundle_size_range"), "products", type_="check")
    op.drop_constraint(op.f("ck_products_modo_venta"), "products", type_="check")
    op.drop_column("products", "destacado")
    op.drop_column("products", "bundle_permite_libres")
    op.drop_column("products", "bundle_size")
    op.drop_column("products", "es_digital")
    op.drop_column("products", "modo_venta")
