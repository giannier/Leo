"""media library

Feature 004 (Motor de ventas), TASK-405.

`media_assets` holds the files the assistant may send (images, documents,
videos, audio), each with a `clave` the tenant picks and a `cuando_usar` that
tells the model when to send it. This migration also adds the foreign key that
migration 0015 left out: `payment_methods_business.media_asset_id` now points at
`media_assets` (`ON DELETE SET NULL`, so removing an image never removes the
payment method).

Adaptations to `data-model.md` (see `conciliacion.md`, "Adaptaciones"):

* No `ON DELETE CASCADE` on `tenant_id` (the repository convention).
* Index, CHECK, UNIQUE and FK names follow `NAMING_CONVENTION`.
* `size_bytes` also gets a CHECK (not negative).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MEDIA_TYPES = ("imagen", "documento", "video", "audio")


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("clave", sa.Text(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("cuando_usar", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("promotion_id", sa.UUID(), nullable=True),
        sa.Column("provider_media_id", sa.Text(), nullable=True),
        sa.Column("provider_media_expires_at", sa.DateTime(timezone=True), nullable=True),
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
            "clave ~ '^[a-z0-9_]{2,40}$'", name=op.f("ck_media_assets_clave_format")
        ),
        sa.CheckConstraint(
            "tipo IN ('imagen', 'documento', 'video', 'audio')",
            name=op.f("ck_media_assets_tipo"),
        ),
        sa.CheckConstraint(
            "char_length(cuando_usar) BETWEEN 5 AND 300",
            name=op.f("ck_media_assets_cuando_usar_length"),
        ),
        sa.CheckConstraint(
            "char_length(caption) <= 1024", name=op.f("ck_media_assets_caption_length")
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_media_assets_size_non_negative")),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_media_assets_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["promotion_id"],
            ["promotions.id"],
            name=op.f("fk_media_assets_promotion_id_promotions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_media_assets_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_assets")),
        sa.UniqueConstraint("tenant_id", "clave", name=op.f("uq_media_assets_tenant_id_clave")),
    )
    op.create_index(
        "ix_media_assets_product",
        "media_assets",
        ["tenant_id", "product_id"],
        postgresql_where=sa.text("product_id IS NOT NULL"),
    )

    op.create_foreign_key(
        op.f("fk_payment_methods_business_media_asset_id_media_assets"),
        "payment_methods_business",
        "media_assets",
        ["media_asset_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_payment_methods_business_media_asset_id_media_assets"),
        "payment_methods_business",
        type_="foreignkey",
    )
    op.drop_index("ix_media_assets_product", table_name="media_assets")
    op.drop_table("media_assets")
