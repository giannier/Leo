"""knowledge_chunks.embedding_model: which model produced each vector

Vectors from two different embedding models are not comparable, even at the
same width - and nothing about a mixed table looks wrong. Search keeps
returning results; they are simply the wrong passages. Retrieval degrades in
silence and the agent starts answering from nothing.

Switching `LLM_PROFILE` invalidates every vector written under the old one.
Without this column there is no way to tell which rows those are, so there is
no way to repair them either. `scripts/reindex_knowledge.py` reads it.

Nullable, and left null for existing rows: they genuinely cannot say what
produced them, and "unknown" has to count as stale. Backfilling a guess would
be worse than the gap - it would mark broken vectors as current.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding_model", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_chunks", "embedding_model")
