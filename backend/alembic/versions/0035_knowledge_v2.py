"""Knowledge v2: web sources, priority, ingestion report and chunk hashes (Feature 006, TASK-601)

* `knowledge_sources.type` gains `web` (a crawl of the business's own site).
* `source_url` and `crawled_at`: where a `web` source was read from and when.
* `priority` (0-100, higher wins): which source the agent trusts when two disagree
  (006 C-003: the business's own FAQ and policies over its website by default).
* `report`: what ingestion could not do, for the panel (OCR pages that could not
  be read, pages a crawl skipped). JSON because its shape differs per type.
* `knowledge_chunks.content_hash`: the normalised text's SHA-256. Unique per source,
  so a document that repeats a block embeds it once; across sources the search
  keeps one copy (the higher-priority one). Old rows stay NULL until the
  maintenance command re-chunks them.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = ("faq", "policy", "pdf", "text")
NEW_TYPES = (*OLD_TYPES, "web")


def _types(values: Sequence[str]) -> str:
    return "type IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_knowledge_sources_knowledge_sources_type"), "knowledge_sources", type_="check"
    )
    op.create_check_constraint("knowledge_sources_type", "knowledge_sources", _types(NEW_TYPES))
    op.add_column("knowledge_sources", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column(
        "knowledge_sources", sa.Column("crawled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "knowledge_sources",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
    )
    op.create_check_constraint(
        "knowledge_sources_priority", "knowledge_sources", "priority BETWEEN 0 AND 100"
    )
    op.add_column(
        "knowledge_sources",
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("knowledge_chunks", sa.Column("content_hash", sa.Text(), nullable=True))
    op.create_index(
        "uq_knowledge_chunks_source_hash",
        "knowledge_chunks",
        ["source_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("content_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_knowledge_chunks_source_hash", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "content_hash")
    op.drop_column("knowledge_sources", "report")
    op.drop_constraint(
        op.f("ck_knowledge_sources_knowledge_sources_priority"), "knowledge_sources", type_="check"
    )
    op.drop_column("knowledge_sources", "priority")
    op.drop_column("knowledge_sources", "crawled_at")
    op.drop_column("knowledge_sources", "source_url")
    op.execute(
        "DELETE FROM knowledge_chunks WHERE source_id IN "
        "(SELECT id FROM knowledge_sources WHERE type = 'web')"
    )
    op.execute("DELETE FROM knowledge_sources WHERE type = 'web'")
    op.drop_constraint(
        op.f("ck_knowledge_sources_knowledge_sources_type"), "knowledge_sources", type_="check"
    )
    op.create_check_constraint("knowledge_sources_type", "knowledge_sources", _types(OLD_TYPES))
