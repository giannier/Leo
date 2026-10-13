"""product_categories: a simple category table (name, order, active)

Feature 004 (Motor de ventas), Milestone B, decision 13: "crear ahora una tabla simple
de categorías (nombre, orden, activa), mientras no hay datos reales". Until now a
category was free text on the product and the option list was built from the
distinct texts, with no order of its own and no way to hide one.

* `product_categories`: `nombre` (unique per tenant, ignoring case), `orden` and
  `activa`.
* `products.category_id` points at it (`SET NULL` if a category is deleted).
  `products.category` stays as the display text the API and the import already use;
  every write through `ProductRepository` links the two.
* Backfill: one category per distinct text of each tenant (case-insensitive, the
  first spelling wins), and every product linked to it.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_categories",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("orden", sa.Integer(), server_default="0", nullable=False),
        sa.Column("activa", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(nombre)) BETWEEN 1 AND 80",
            name=op.f("ck_product_categories_nombre_length"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_product_categories_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_categories")),
    )
    op.create_index(
        "uq_product_categories_tenant_id_nombre",
        "product_categories",
        ["tenant_id", sa.text("lower(nombre)")],
        unique=True,
    )
    op.add_column("products", sa.Column("category_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_products_category_id_product_categories"),
        "products",
        "product_categories",
        ["category_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_products_category_id"), "products", ["category_id"])

    op.execute(
        """
        INSERT INTO product_categories (tenant_id, nombre)
        SELECT DISTINCT ON (tenant_id, lower(btrim(category))) tenant_id, btrim(category)
        FROM products
        WHERE category IS NOT NULL AND btrim(category) <> ''
        ORDER BY tenant_id, lower(btrim(category)), created_at
        """
    )
    op.execute(
        """
        UPDATE products p SET category_id = c.id
        FROM product_categories c
        WHERE c.tenant_id = p.tenant_id AND lower(c.nombre) = lower(btrim(p.category))
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_products_category_id"), table_name="products")
    op.drop_constraint(
        op.f("fk_products_category_id_product_categories"), "products", type_="foreignkey"
    )
    op.drop_column("products", "category_id")
    op.drop_index("uq_product_categories_tenant_id_nombre", table_name="product_categories")
    op.drop_table("product_categories")
