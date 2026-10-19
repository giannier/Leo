"""Experiencia simple: product tags, photo order, simulator feedback (Feature 018, TASK-1801)

* `products.tags`: up to 10 labels the business writes ("oferta", "SIAF"); the agent also finds a
  product by them and the catalogue filters by them.
* `media_assets.orden`: the order of a product's photos; the first is the main one.
* `sandbox_feedback`: a thumbs up or down on one reply of the simulator, with the reason (HU-06).

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_products_products_tags_count"), "products", "cardinality(tags) <= 10"
    )
    op.create_index("ix_products_tags", "products", ["tags"], postgresql_using="gin")

    op.add_column(
        "media_assets",
        sa.Column("orden", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "sandbox_feedback",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "conversation_id",
            UUID,
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id", UUID, sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "rating IN (-1, 1)", name=op.f("ck_sandbox_feedback_sandbox_feedback_rating")
        ),
        sa.CheckConstraint(
            "char_length(motivo) <= 500",
            name=op.f("ck_sandbox_feedback_sandbox_feedback_motivo_length"),
        ),
        sa.UniqueConstraint("message_id", name="uq_sandbox_feedback_message_id"),
    )
    op.create_index(
        "ix_sandbox_feedback_tenant_created", "sandbox_feedback", ["tenant_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_sandbox_feedback_tenant_created", table_name="sandbox_feedback")
    op.drop_table("sandbox_feedback")
    op.drop_column("media_assets", "orden")
    op.drop_index("ix_products_tags", table_name="products")
    op.drop_constraint(op.f("ck_products_products_tags_count"), "products", type_="check")
    op.drop_column("products", "tags")
