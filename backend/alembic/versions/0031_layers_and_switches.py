"""Layers: rule switches, catalogue news, reviewer permission, rubro knowledge (017, TASK-461)

* `tenant_rule_switches`: the template and rubro rules a business switched off.
* `tenant_catalog_seen`: the version whose news the reviewer already saw.
* `tenant_users.puede_revisar` (C-09). The owner always counts as a reviewer
  in code, so no backfill is needed.
* `catalog_embeddings`: vectors of rubro FAQs and objections, so
  `consultar_conocimiento` finds them. Platform data, apart from the frozen
  version rows; a few hundred rows filtered by version, so no vector index.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_rule_switches",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("rule_key", sa.Text(), nullable=False),
        sa.Column("apagada", sa.Boolean(), nullable=False),
        sa.Column("cambiado_por", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('proceso', 'rubro')", name=op.f("ck_tenant_rule_switches_kind")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_rule_switches_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["cambiado_por"],
            ["users.id"],
            name=op.f("fk_tenant_rule_switches_cambiado_por_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "kind", "rule_key", name=op.f("pk_tenant_rule_switches")
        ),
    )
    op.create_table(
        "tenant_catalog_seen",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("version_vista", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('proceso', 'rubro')", name=op.f("ck_tenant_catalog_seen_kind")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_catalog_seen_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("tenant_id", "kind", "key", name=op.f("pk_tenant_catalog_seen")),
    )
    op.add_column(
        "tenant_users",
        sa.Column("puede_revisar", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "catalog_embeddings",
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("item_kind", sa.Text(), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "item_kind IN ('faq', 'objecion')", name=op.f("ck_catalog_embeddings_item_kind")
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["catalog_versions.id"],
            name=op.f("fk_catalog_embeddings_version_id_catalog_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_catalog_embeddings")),
    )
    op.create_index(op.f("ix_catalog_embeddings_version_id"), "catalog_embeddings", ["version_id"])


def downgrade() -> None:
    op.drop_table("catalog_embeddings")
    op.drop_column("tenant_users", "puede_revisar")
    op.drop_table("tenant_catalog_seen")
    op.drop_table("tenant_rule_switches")
