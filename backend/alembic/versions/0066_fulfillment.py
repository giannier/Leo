"""Orders and deliveries: the stages of the business's work, the pin and the notices (016).

Revision ID: 0066
Revises: 0065

* `orders.etapa` / `etapa_at`: where a confirmed order is in the business's own work while it is
  `en_curso` (`preparando`, `listo`, `en_camino`), and since when. `completado_at` closes the
  clock the comanda and the average delivery time read.
* `orders.ubicacion_entrega`: the pin the customer shared on WhatsApp.
* `order_settings`: which stage tells the customer, one row per business.

The orders that were already `en_curso` start in `preparando`; the ones already `completado` take
the last change of the row as the moment they finished, which is the nearest thing the data kept.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0066"
down_revision: str | Sequence[str] | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("etapa", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("etapa_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("completado_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "orders",
        sa.Column("ubicacion_entrega", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_orders_etapa"), "orders", "etapa IN ('preparando', 'listo', 'en_camino')"
    )
    op.execute(
        "UPDATE orders SET etapa = 'preparando', etapa_at = updated_at WHERE estado = 'en_curso'"
    )
    op.execute("UPDATE orders SET completado_at = updated_at WHERE estado = 'completado'")

    op.create_table(
        "order_settings",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("avisar_listo", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("avisar_en_camino", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_order_settings_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_order_settings")),
    )


def downgrade() -> None:
    op.drop_table("order_settings")
    op.drop_constraint(op.f("ck_orders_etapa"), "orders", type_="check")
    op.drop_column("orders", "ubicacion_entrega")
    op.drop_column("orders", "completado_at")
    op.drop_column("orders", "etapa_at")
    op.drop_column("orders", "etapa")
