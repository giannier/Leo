"""RAG knowledge repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.repositories.base import BaseRepository


class KnowledgeSourceRepository(BaseRepository[KnowledgeSource]):
    """Uploaded documents and snippets for one tenant."""

    model = KnowledgeSource

    def list_indexed(self) -> Sequence[KnowledgeSource]:
        """Sources actually available to the agent right now."""
        return self.list(
            KnowledgeSource.deleted_at.is_(None),
            KnowledgeSource.status == "indexed",
        )

    def total_chars(self) -> int:
        """Characters indexed so far, for the plan's max_rag_chars budget.

        Deleted sources do not count: the tenant freed that quota.
        """
        statement = (
            select(func.coalesce(func.sum(KnowledgeSource.char_count), 0))
            .where(KnowledgeSource.tenant_id == self.tenant_id)
            .where(KnowledgeSource.deleted_at.is_(None))
        )
        return self._session.execute(statement).scalar_one()

    def active_file_count(self) -> int:
        """Live files, for the plan's max_knowledge_files budget."""
        return self.count(KnowledgeSource.deleted_at.is_(None))


class KnowledgeChunkRepository(BaseRepository[KnowledgeChunk]):
    """Embedded chunks for one tenant."""

    model = KnowledgeChunk

    def search(self, embedding: Sequence[float], *, limit: int = 5) -> Sequence[KnowledgeChunk]:
        """Nearest chunks by cosine distance, within this tenant only.

        The tenant filter is applied before ordering, so another tenant's
        chunk can never win the ranking - the reason this must live in a
        repository rather than in an ad-hoc query at the call site.
        """
        statement = (
            self._scoped()
            .order_by(KnowledgeChunk.embedding.cosine_distance(embedding))
            .limit(limit)
        )
        return self._session.execute(statement).scalars().all()

    def delete_for_source(self, source_id: uuid.UUID) -> int:
        """Drop every chunk of one source belonging to this tenant."""
        chunks = self.list(KnowledgeChunk.source_id == source_id)
        for chunk in chunks:
            self._session.delete(chunk)
        self._session.flush()
        return len(chunks)
