"""Electronic receipts through the business's own provider account (019, phase G).

Revision ID: 0062
Revises: 0061

* `invoicing_settings`: the route and token of the business's account with its provider (the token
  encrypted), the series and the next number of each, and how the tax is treated. Off until the
  owner turns it on.
* One electronic receipt standing per order (a partial unique index): a double click cannot issue two.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: str | Sequence[str] | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invoicing_settings",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("proveedor", sa.Text(), server_default="nubefact", nullable=False),
        sa.Column("ruta", sa.Text(), nullable=True),
        sa.Column("token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("serie_boleta", sa.Text(), server_default="B001", nullable=False),
        sa.Column("serie_factura", sa.Text(), server_default="F001", nullable=False),
        sa.Column("siguiente_boleta", sa.Integer(), server_default="1", nullable=False),
        sa.Column("siguiente_factura", sa.Integer(), server_default="1", nullable=False),
        sa.Column("tratamiento_igv", sa.Text(), server_default="gravado", nullable=False),
        sa.Column(
            "igv_pct", sa.Numeric(precision=5, scale=2), server_default="18.00", nullable=False
        ),
        sa.Column("activo", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "proveedor IN ('nubefact')", name=op.f("ck_invoicing_settings_proveedor")
        ),
        sa.CheckConstraint(
            "serie_boleta ~ '^B[A-Z0-9]{3}$'",
            name=op.f("ck_invoicing_settings_serie_boleta_format"),
        ),
        sa.CheckConstraint(
            "serie_factura ~ '^F[A-Z0-9]{3}$'",
            name=op.f("ck_invoicing_settings_serie_factura_format"),
        ),
        sa.CheckConstraint(
            "tratamiento_igv IN ('gravado', 'exonerado', 'inafecto')",
            name=op.f("ck_invoicing_settings_tratamiento_igv"),
        ),
        sa.CheckConstraint(
            "igv_pct >= 0 AND igv_pct <= 30", name=op.f("ck_invoicing_settings_igv_range")
        ),
        sa.CheckConstraint(
            "siguiente_boleta >= 1 AND siguiente_factura >= 1",
            name=op.f("ck_invoicing_settings_next_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_invoicing_settings_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_invoicing_settings")),
    )
    op.create_index(
        "uq_order_documents_one_electronic",
        "order_documents",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text(
            "origen = 'electronico' AND estado = 'emitido' AND tipo IN ('boleta', 'factura')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_order_documents_one_electronic",
        table_name="order_documents",
        postgresql_where=sa.text(
            "origen = 'electronico' AND estado = 'emitido' AND tipo IN ('boleta', 'factura')"
        ),
    )
    op.drop_table("invoicing_settings")
