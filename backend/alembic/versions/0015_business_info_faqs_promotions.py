"""business_info, faqs and promotions

Feature 004 (Motor de ventas), TASK-403.

Three tables the sales engine reads on every turn, plus the column that will
point a payment method at its QR image.

`payment_methods_business.media_asset_id` is created here without a foreign key
because `media_assets` does not exist yet; migration 0017 adds the constraint.

Adaptations to `data-model.md` (see `conciliacion.md`, "Adaptaciones"):

* No `ON DELETE CASCADE` on `tenant_id` (the repository convention).
* Index and CHECK names follow `NAMING_CONVENTION`.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SECTIONS = ("locales", "entregas", "tipos_cliente", "devoluciones", "contacto")


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(f"'{value}'" for value in values)})"


def upgrade() -> None:
    op.create_table(
        "business_info",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in_list("section", SECTIONS), name=op.f("ck_business_info_section")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_business_info_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("tenant_id", "section", name=op.f("pk_business_info")),
    )

    op.create_table(
        "faqs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("pregunta", sa.Text(), nullable=False),
        sa.Column("respuesta", sa.Text(), nullable=False),
        sa.Column("orden", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("knowledge_source_id", sa.UUID(), nullable=True),
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
            "char_length(pregunta) BETWEEN 3 AND 300", name=op.f("ck_faqs_pregunta_length")
        ),
        sa.CheckConstraint(
            "char_length(respuesta) BETWEEN 1 AND 2000", name=op.f("ck_faqs_respuesta_length")
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_source_id"],
            ["knowledge_sources.id"],
            name=op.f("fk_faqs_knowledge_source_id_knowledge_sources"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_faqs_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_faqs")),
    )
    op.create_index(
        "ix_faqs_tenant_id_is_active_orden", "faqs", ["tenant_id", "is_active", "orden"]
    )

    op.create_table(
        "promotions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("uso", sa.Text(), server_default="automatica", nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("valor", sa.Numeric(12, 2), nullable=True),
        sa.Column("n", sa.Integer(), nullable=True),
        sa.Column("m", sa.Integer(), nullable=True),
        sa.Column("aplica_a", sa.Text(), server_default="todo", nullable=False),
        sa.Column("product_ids", sa.ARRAY(sa.UUID()), server_default="{}", nullable=False),
        sa.Column("categorias", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("monto_minimo", sa.Numeric(12, 2), nullable=True),
        sa.Column("vigencia_horas", sa.Integer(), nullable=True),
        sa.Column("para_seguimiento", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("inicia_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("termina_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
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
            _in_list("uso", ("automatica", "cierre")), name=op.f("ck_promotions_uso")
        ),
        sa.CheckConstraint(
            _in_list("tipo", ("porcentaje", "monto_fijo", "nxm", "precio_cantidad")),
            name=op.f("ck_promotions_tipo"),
        ),
        sa.CheckConstraint(
            _in_list("aplica_a", ("todo", "productos", "categorias")),
            name=op.f("ck_promotions_aplica_a"),
        ),
        # A CHECK that evaluates to NULL passes, so each rule states its NOT
        # NULL requirements explicitly instead of trusting the comparison.
        sa.CheckConstraint(
            "tipo <> 'nxm' OR (n IS NOT NULL AND m IS NOT NULL AND n > m AND m >= 1)",
            name=op.f("ck_promotions_nxm"),
        ),
        sa.CheckConstraint(
            "tipo <> 'porcentaje' OR (valor IS NOT NULL AND valor > 0 AND valor <= 100)",
            name=op.f("ck_promotions_porcentaje"),
        ),
        sa.CheckConstraint(
            "tipo <> 'monto_fijo' OR (valor IS NOT NULL AND valor > 0)",
            name=op.f("ck_promotions_monto"),
        ),
        sa.CheckConstraint(
            "tipo <> 'precio_cantidad' OR "
            "(valor IS NOT NULL AND valor > 0 AND m IS NOT NULL AND m >= 2)",
            name=op.f("ck_promotions_precio_cantidad"),
        ),
        sa.CheckConstraint(
            "uso <> 'cierre' OR (vigencia_horas IS NOT NULL AND vigencia_horas BETWEEN 1 AND 168)",
            name=op.f("ck_promotions_cierre"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_promotions_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotions")),
    )
    op.create_index("ix_promotions_tenant_id_is_active", "promotions", ["tenant_id", "is_active"])

    op.add_column("payment_methods_business", sa.Column("media_asset_id", sa.UUID(), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_methods_business", "media_asset_id")
    op.drop_index("ix_promotions_tenant_id_is_active", table_name="promotions")
    op.drop_table("promotions")
    op.drop_index("ix_faqs_tenant_id_is_active_orden", table_name="faqs")
    op.drop_table("faqs")
    op.drop_table("business_info")
