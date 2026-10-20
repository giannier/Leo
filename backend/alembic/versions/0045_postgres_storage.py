"""Files in Postgres: the object catalogue and its chunks (TASK-026)

MinIO's official images are no longer published, so the production stack cannot
pull them. Files go to Postgres instead, behind the same `StorageProvider` port.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045"
down_revision: str | Sequence[str] | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "stored_objects",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("size_bytes >= 0", name="size_not_negative"),
        sa.CheckConstraint("chunk_count >= 0", name="chunks_not_negative"),
    )
    op.create_index(op.f("ix_stored_objects_tenant_id"), "stored_objects", ["tenant_id"])

    op.create_table(
        "stored_object_chunks",
        sa.Column(
            "key",
            sa.Text(),
            sa.ForeignKey("stored_objects.key", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
    )

    # Out of line and uncompressed. The default (EXTENDED) tries to compress
    # every value, and what is stored here is JPEG, PNG, PDF, MP4 and audio:
    # already compressed, so that is CPU spent for no saving.
    op.execute("ALTER TABLE stored_object_chunks ALTER COLUMN data SET STORAGE EXTERNAL")

    # Deleting a file frees a lot of space at once. Let autovacuum reclaim it
    # after ~2% of the table is dead, not the 20% default, so the table and its
    # TOAST data do not sit bloated until the next big clean-up.
    op.execute(
        "ALTER TABLE stored_object_chunks SET ("
        "autovacuum_vacuum_scale_factor = 0.02, "
        "toast.autovacuum_vacuum_scale_factor = 0.02)"
    )


def downgrade() -> None:
    op.drop_table("stored_object_chunks")
    op.drop_index(op.f("ix_stored_objects_tenant_id"), table_name="stored_objects")
    op.drop_table("stored_objects")
