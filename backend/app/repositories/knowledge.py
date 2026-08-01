"""RAG knowledge repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update

from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.repositories.base import BaseRepository, TenantScopeError

# Statuses a source may be claimed from. `failed` is included so a retry
# after a transient provider outage is an ordinary re-enqueue rather than a
# manual database edit.
CLAIMABLE_STATUSES = ("pending", "failed")

STATUS_PENDING = "pending"
STATUS_INDEXING = "indexing"
STATUS_INDEXED = "indexed"
STATUS_FAILED = "failed"


class KnowledgeSourceRepository(BaseRepository[KnowledgeSource]):
    """Uploaded documents and snippets for one tenant."""

    model = KnowledgeSource

    def list_indexed(self) -> Sequence[KnowledgeSource]:
        """Sources actually available to the agent right now."""
        return self.list(
            KnowledgeSource.deleted_at.is_(None),
            KnowledgeSource.status == STATUS_INDEXED,
        )

    def list_active(self) -> Sequence[KnowledgeSource]:
        """Everything the tenant still owns, newest first.

        Includes `pending`, `indexing` and `failed`: the panel has to show a
        document that is still being processed or that could not be read,
        otherwise a failed upload is invisible and looks like a lost file.
        """
        statement = (
            self._scoped()
            .where(KnowledgeSource.deleted_at.is_(None))
            .order_by(KnowledgeSource.created_at.desc(), KnowledgeSource.id)
        )
        return self._session.execute(statement).scalars().all()

    def total_chars(self, *, exclude_source_id: uuid.UUID | None = None) -> int:
        """Characters indexed so far, for the plan's max_rag_chars budget.

        Deleted sources do not count: the tenant freed that quota.

        `exclude_source_id` is what the indexing worker uses when it finally
        learns how big a PDF was. Without it the source being sized would be
        counted twice - once as its stored `char_count`, once as the freshly
        extracted text - and a tenant near the limit would be refused for
        space they are not actually using.
        """
        statement = (
            select(func.coalesce(func.sum(KnowledgeSource.char_count), 0))
            .where(KnowledgeSource.tenant_id == self.tenant_id)
            .where(KnowledgeSource.deleted_at.is_(None))
        )
        if exclude_source_id is not None:
            statement = statement.where(KnowledgeSource.id != exclude_source_id)
        return self._session.execute(statement).scalar_one()

    def active_file_count(self) -> int:
        """Live files, for the plan's max_knowledge_files budget."""
        return self.count(KnowledgeSource.deleted_at.is_(None))

    # --------------------------------------------------------- state machine

    def claim_for_indexing(self, source_id: uuid.UUID) -> bool:
        """Move `pending|failed` -> `indexing`, atomically.

        A conditional UPDATE rather than read-then-write: two workers handed
        the same job (an ARQ retry racing the original) would both read
        `pending` and both index the document, doubling the tenant's chunks
        and their embedding cost. Here the database decides, and exactly one
        UPDATE matches a row.

        The tenant predicate is on the UPDATE itself, not inherited from
        `_scoped()`, because this statement never runs a SELECT.
        """
        statement = (
            update(KnowledgeSource)
            .where(self._tenant_predicate)
            .where(KnowledgeSource.id == source_id)
            .where(KnowledgeSource.deleted_at.is_(None))
            .where(KnowledgeSource.status.in_(CLAIMABLE_STATUSES))
            .values(status=STATUS_INDEXING, error_msg=None)
        )
        # `Session.execute` is typed as returning a generic `Result`; a DML
        # statement always yields a `CursorResult`, which is the one that
        # carries `rowcount`.
        result = cast("CursorResult[Any]", self._session.execute(statement))
        self._session.flush()
        return bool(result.rowcount)

    def set_char_count(self, source_id: uuid.UUID, char_count: int) -> bool:
        """Record the real size of a source once it is known."""
        source = self.get(source_id)
        if source is None:
            return False
        source.char_count = char_count
        self._session.flush()
        return True

    def mark_indexed(self, source_id: uuid.UUID) -> bool:
        """Indexing finished. Clears any error from a previous attempt."""
        source = self.get(source_id)
        if source is None:
            return False
        source.status = STATUS_INDEXED
        source.error_msg = None
        self._session.flush()
        return True

    def mark_failed(self, source_id: uuid.UUID, reason: str) -> bool:
        """Indexing failed, with the reason the tenant will be shown.

        The reason is always recorded. A `failed` row with an empty
        `error_msg` is a dead end for whoever has to explain it.
        """
        source = self.get(source_id)
        if source is None:
            return False
        source.status = STATUS_FAILED
        source.error_msg = reason
        self._session.flush()
        return True

    def soft_delete(self, source_id: uuid.UUID) -> bool:
        """Retire a source without removing its row.

        Soft, because `char_count` history and the audit trail of what the
        agent was once told are worth keeping. The chunks are a different
        matter and must be hard-deleted by the caller - an orphaned embedding
        keeps answering questions from a document the tenant deleted.
        """
        source = self.get(source_id)
        if source is None or source.deleted_at is not None:
            return False
        source.deleted_at = datetime.now(UTC)
        self._session.flush()
        return True


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

    def list_for_source(self, source_id: uuid.UUID) -> Sequence[KnowledgeChunk]:
        """Chunks of one source belonging to this tenant, oldest first."""
        statement = (
            self._scoped()
            .where(KnowledgeChunk.source_id == source_id)
            .order_by(KnowledgeChunk.created_at, KnowledgeChunk.id)
        )
        return self._session.execute(statement).scalars().all()

    def count_for_source(self, source_id: uuid.UUID) -> int:
        return self.count(KnowledgeChunk.source_id == source_id)

    def delete_for_source(self, source_id: uuid.UUID) -> int:
        """Drop every chunk of one source belonging to this tenant."""
        chunks = self.list(KnowledgeChunk.source_id == source_id)
        for chunk in chunks:
            self._session.delete(chunk)
        self._session.flush()
        return len(chunks)

    def replace_for_source(
        self,
        source_id: uuid.UUID,
        chunks: Sequence[tuple[str, Sequence[float]]],
        *,
        embedding_model: str | None = None,
    ) -> int:
        """Swap a source's chunks for a freshly embedded set.

        Refuses a source that belongs to another tenant, mirroring
        `MessageRepository.append`: `source_id` arrives as a bare UUID from a
        job payload, so ownership has to be proved here rather than assumed.
        Re-indexing goes through this method so that the old vectors can
        never survive alongside the new ones.
        """
        sources = KnowledgeSourceRepository(self._session, self.tenant_id)
        if not sources.exists(source_id):
            raise TenantScopeError(
                f"Knowledge source {source_id!s} does not belong to tenant {self.tenant_id!s}"
            )

        self.delete_for_source(source_id)
        for content, embedding in chunks:
            self.add(
                source_id=source_id,
                content=content,
                embedding=list(embedding),
                embedding_model=embedding_model,
            )
        return len(chunks)
