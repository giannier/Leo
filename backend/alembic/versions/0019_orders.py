"""orders, items, payment proofs and per-tenant sequences

Feature 004 (Motor de ventas), TASK-407.

* `tenant_sequences` numbers each tenant's orders from 1.
* `orders` is the purchase being assembled with a customer; `order_items` its
  lines (name and unit price copied when added); `payment_proofs` the receipts
  customers send, as the vision step read them.

Adaptations to `data-model.md` (see `conciliacion.md`, "Adaptaciones"):

* No `ON DELETE CASCADE` on `tenant_id`, `conversation_id` or `order_id` of
  `payment_proofs`: deleting a conversation or an order must not erase the sales
  history or the receipts. The one cascade is `order_items.order_id` (an item has
  no life without its order). The `ON DELETE SET NULL` of the data model stay.
* Index, CHECK, UNIQUE and FK names follow `NAMING_CONVENTION`.
* `payment_proofs.created_at` defaults to `clock_timestamp()`: "the latest proof"
  decides whether a payment is rejected, and `now()` ties inside a transaction.
* Three CHECKs the data model does not list: money columns of `orders` and
  `order_items` are not negative, and `payment_proofs.confianza` is between 0 and 1
  (`numeric(3,2)` alone would accept up to 9.99).

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_sequences",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("value", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_sequences_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("tenant_id", "name", name=op.f("pk_tenant_sequences")),
    )
    op.create_table(
        "orders",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("estado", sa.Text(), server_default="borrador", nullable=False),
        sa.Column("modalidad_pago", sa.Text(), server_default="previo", nullable=False),
        sa.Column("moneda", sa.CHAR(length=3), nullable=False),
        sa.Column(
            "subtotal", sa.Numeric(precision=12, scale=2), server_default="0", nullable=False
        ),
        sa.Column(
            "descuento", sa.Numeric(precision=12, scale=2), server_default="0", nullable=False
        ),
        sa.Column("envio", sa.Numeric(precision=12, scale=2), server_default="0", nullable=False),
        sa.Column("total", sa.Numeric(precision=12, scale=2), server_default="0", nullable=False),
        sa.Column("promotion_id", sa.UUID(), nullable=True),
        sa.Column("promotion_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tipo_entrega", sa.Text(), nullable=True),
        sa.Column("zona_envio", sa.Text(), nullable=True),
        sa.Column("datos_cliente", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("motivo_rechazo", sa.Text(), nullable=True),
        sa.Column("rechazo_vigente", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("siguiente_paso", sa.Text(), nullable=True),
        sa.Column("is_sandbox", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("confirmed_by", sa.UUID(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "estado IN ('borrador', 'por_confirmar', 'confirmado', 'en_curso', 'completado', 'descartado')",
            name=op.f("ck_orders_estado"),
        ),
        sa.CheckConstraint(
            "modalidad_pago IN ('previo', 'contra_entrega')", name=op.f("ck_orders_modalidad_pago")
        ),
        sa.CheckConstraint(
            "tipo_entrega IN ('delivery', 'recojo')", name=op.f("ck_orders_tipo_entrega")
        ),
        sa.CheckConstraint(
            "subtotal >= 0 AND descuento >= 0 AND envio >= 0 AND total >= 0",
            name=op.f("ck_orders_amounts_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"],
            ["users.id"],
            name=op.f("fk_orders_confirmed_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], name=op.f("fk_orders_contact_id_contacts")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_orders_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["promotion_id"],
            ["promotions.id"],
            name=op.f("fk_orders_promotion_id_promotions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_orders_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("tenant_id", "numero", name="uq_orders_tenant_id_numero"),
    )
    op.create_index(
        "ix_orders_conversation_id_created_at",
        "orders",
        ["conversation_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_orders_tenant_id_is_sandbox_estado_created_at",
        "orders",
        ["tenant_id", "is_sandbox", "estado", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "order_items",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("precio_unitario", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("cantidad", sa.Integer(), nullable=False),
        sa.Column("bundle_size", sa.Integer(), nullable=True),
        sa.Column(
            "bundle_selections",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("subtotal", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cantidad BETWEEN 1 AND 999", name=op.f("ck_order_items_cantidad_range")
        ),
        sa.CheckConstraint(
            "precio_unitario >= 0 AND subtotal >= 0",
            name=op.f("ck_order_items_amounts_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_items_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_order_items_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_order_items_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"], unique=False)
    op.create_table(
        "payment_proofs",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("estado", sa.Text(), server_default="por_verificar", nullable=False),
        sa.Column("monto", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("moneda", sa.CHAR(length=3), nullable=True),
        sa.Column("fecha_operacion", sa.Date(), nullable=True),
        sa.Column("codigo_operacion", sa.Text(), nullable=True),
        sa.Column("entidad", sa.Text(), nullable=True),
        sa.Column("destinatario", sa.Text(), nullable=True),
        sa.Column("confianza", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("observaciones", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("extraccion", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("motivo_rechazo", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "estado IN ('por_verificar', 'aprobado', 'rechazado')",
            name=op.f("ck_payment_proofs_estado"),
        ),
        sa.CheckConstraint(
            "confianza BETWEEN 0 AND 1", name=op.f("ck_payment_proofs_confianza_range")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_payment_proofs_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_payment_proofs_message_id_messages"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_payment_proofs_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name=op.f("fk_payment_proofs_reviewed_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_payment_proofs_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_proofs")),
    )
    op.create_index(
        "ix_payment_proofs_order_id_created_at",
        "payment_proofs",
        ["order_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_payment_proofs_tenant_id_codigo_operacion",
        "payment_proofs",
        ["tenant_id", "codigo_operacion"],
        unique=False,
        postgresql_where=sa.text("codigo_operacion IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payment_proofs_tenant_id_codigo_operacion",
        table_name="payment_proofs",
        postgresql_where=sa.text("codigo_operacion IS NOT NULL"),
    )
    op.drop_index("ix_payment_proofs_order_id_created_at", table_name="payment_proofs")
    op.drop_table("payment_proofs")
    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")
    op.drop_index("ix_orders_tenant_id_is_sandbox_estado_created_at", table_name="orders")
    op.drop_index("ix_orders_conversation_id_created_at", table_name="orders")
    op.drop_table("orders")
    op.drop_table("tenant_sequences")
