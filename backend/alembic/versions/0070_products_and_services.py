"""Products and services: what an item is, «solo informar», and where a package's choices come from.

Revision ID: 0070
Revises: 0069

Feature 020 (the universal process, Giannier 2026-10-28): only products and services are sold, and each
item of the catalogue says how it closes, instead of a template per business.

* `products.tipo`: `producto` or `servicio` (D1). A bookable item is a service; everything else starts as a
  product and the owner corrects it.
* `modo_venta` admits `informar` (D7): what a person puts together (a programme, a project) is not
  ordered or quoted by the assistant, it is explained and handed to an advisor.
* `products.bundle_categoria` (D2): a package whose choices come only from one category («paquete de 3
  herramientas»). Empty: any item of the business, as before.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0070"
down_revision: str | Sequence[str] | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products", sa.Column("tipo", sa.Text(), server_default="producto", nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_products_tipo"), "products", "tipo IN ('producto', 'servicio')"
    )
    op.execute("UPDATE products SET tipo = 'servicio' WHERE agendable")

    op.drop_constraint(op.f("ck_products_modo_venta"), "products", type_="check")
    op.create_check_constraint(
        op.f("ck_products_modo_venta"),
        "products",
        "modo_venta IN ('precio_fijo', 'cotizacion', 'informar')",
    )

    op.add_column("products", sa.Column("bundle_categoria", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_products_bundle_categoria"),
        "products",
        "bundle_categoria IS NULL OR "
        "(bundle_size IS NOT NULL AND char_length(btrim(bundle_categoria)) BETWEEN 1 AND 80)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_products_bundle_categoria"), "products", type_="check")
    op.drop_column("products", "bundle_categoria")

    # An item that only informs goes back to being quoted: the nearest thing the old check allows.
    op.execute("UPDATE products SET modo_venta = 'cotizacion' WHERE modo_venta = 'informar'")
    op.drop_constraint(op.f("ck_products_modo_venta"), "products", type_="check")
    op.create_check_constraint(
        op.f("ck_products_modo_venta"), "products", "modo_venta IN ('precio_fijo', 'cotizacion')"
    )

    op.drop_constraint(op.f("ck_products_tipo"), "products", type_="check")
    op.drop_column("products", "tipo")
