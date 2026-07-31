"""Multi-tenant isolation for every knowledge path TASK-012 adds.

Mandated by the Constitution and never skipped or disabled. One case per new
repository method plus the retrieval service, each driving the real code
against two real tenants.

The teeth of these cases are proved in `test_isolation_suite_has_teeth.py`:
removing the predicate from `BaseRepository._scoped()` must make them fail.
The two methods that carry their own predicate instead of inheriting
`_scoped()` - `claim_for_indexing`, which is an atomic UPDATE, and
`total_chars`, which is an aggregate - are sabotaged separately there, since
a `_scoped()` change cannot reach them.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeSource
from app.providers.llm import MockLLMProvider
from app.repositories import (
    KnowledgeChunkRepository,
    KnowledgeSourceRepository,
    TenantRepository,
    TenantScopeError,
)
from app.services.knowledge import search_knowledge
from tests.isolation.conftest import EMBEDDING_DIM, TenantFixture


class TestSourceReadsAreScoped:
    def test_list_active_excludes_the_other_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = KnowledgeSourceRepository(db, tenant_a.tenant_id)
        ids = {source.id for source in repo.list_active()}
        assert ids == {tenant_a.source_id}

    def test_list_indexed_excludes_the_other_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = KnowledgeSourceRepository(db, tenant_a.tenant_id)
        ids = {source.id for source in repo.list_indexed()}
        assert tenant_b.source_id not in ids

    def test_total_chars_ignores_the_other_tenants_documents(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The plan budget must be charged to the tenant who spent it."""
        db.get(KnowledgeSource, tenant_a.source_id).char_count = 100  # type: ignore[union-attr]
        db.get(KnowledgeSource, tenant_b.source_id).char_count = 900  # type: ignore[union-attr]
        db.flush()

        assert KnowledgeSourceRepository(db, tenant_a.tenant_id).total_chars() == 100

    def test_excluding_a_foreign_source_cannot_change_the_total(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """`exclude_source_id` narrows within the tenant, never across it."""
        db.get(KnowledgeSource, tenant_a.source_id).char_count = 100  # type: ignore[union-attr]
        db.flush()
        repo = KnowledgeSourceRepository(db, tenant_a.tenant_id)

        assert repo.total_chars(exclude_source_id=tenant_b.source_id) == 100
        assert repo.total_chars(exclude_source_id=tenant_a.source_id) == 0

    def test_active_file_count_only_counts_own_files(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        assert KnowledgeSourceRepository(db, tenant_a.tenant_id).active_file_count() == 1


class TestSourceStateChangesAreScoped:
    def test_a_foreign_source_cannot_be_claimed_for_indexing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """A job payload is a bare UUID; ownership has to be proved here."""
        db.get(KnowledgeSource, tenant_b.source_id).status = "pending"  # type: ignore[union-attr]
        db.flush()

        claimed = KnowledgeSourceRepository(db, tenant_a.tenant_id).claim_for_indexing(
            tenant_b.source_id
        )

        assert claimed is False
        victim = db.get(KnowledgeSource, tenant_b.source_id)
        assert victim is not None
        assert victim.status == "pending"

    def test_a_foreign_source_cannot_be_marked_indexed(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        db.get(KnowledgeSource, tenant_b.source_id).status = "failed"  # type: ignore[union-attr]
        db.flush()

        assert not KnowledgeSourceRepository(db, tenant_a.tenant_id).mark_indexed(
            tenant_b.source_id
        )
        victim = db.get(KnowledgeSource, tenant_b.source_id)
        assert victim is not None
        assert victim.status == "failed"

    def test_a_foreign_source_cannot_be_marked_failed(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        assert not KnowledgeSourceRepository(db, tenant_a.tenant_id).mark_failed(
            tenant_b.source_id, "sabotage"
        )
        victim = db.get(KnowledgeSource, tenant_b.source_id)
        assert victim is not None
        assert victim.error_msg is None
        assert victim.status == "indexed"

    def test_a_foreign_sources_size_cannot_be_rewritten(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Inflating another tenant's `char_count` would exhaust their plan."""
        assert not KnowledgeSourceRepository(db, tenant_a.tenant_id).set_char_count(
            tenant_b.source_id, 999_999
        )
        victim = db.get(KnowledgeSource, tenant_b.source_id)
        assert victim is not None
        assert victim.char_count == 0

    def test_a_foreign_source_cannot_be_soft_deleted(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        assert not KnowledgeSourceRepository(db, tenant_a.tenant_id).soft_delete(tenant_b.source_id)
        victim = db.get(KnowledgeSource, tenant_b.source_id)
        assert victim is not None
        assert victim.deleted_at is None

    def test_none_of_it_is_distinguishable_from_a_missing_row(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """A different answer for "foreign" would leak that the id exists."""
        repo = KnowledgeSourceRepository(db, tenant_a.tenant_id)
        unknown = uuid.uuid4()
        assert repo.soft_delete(tenant_b.source_id) == repo.soft_delete(unknown)
        assert repo.mark_indexed(tenant_b.source_id) == repo.mark_indexed(unknown)
        assert repo.claim_for_indexing(tenant_b.source_id) == repo.claim_for_indexing(unknown)


class TestChunkAccessIsScoped:
    def test_chunks_of_a_foreign_source_are_invisible(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Knowing the source id must not be enough to read its chunks."""
        repo = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        assert repo.list_for_source(tenant_b.source_id) == []
        assert repo.count_for_source(tenant_b.source_id) == 0

    def test_chunks_of_a_foreign_source_cannot_be_deleted(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        assert repo.delete_for_source(tenant_b.source_id) == 0
        assert db.get(KnowledgeChunk, tenant_b.chunk_id) is not None

    def test_writing_chunks_into_a_foreign_source_is_refused(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Poisoning another tenant's knowledge base is the worst case here."""
        repo = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        with pytest.raises(TenantScopeError):
            repo.replace_for_source(tenant_b.source_id, [("Inyectado", [0.5] * EMBEDDING_DIM)])

    def test_a_refused_write_destroys_nothing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """`replace_for_source` deletes before it inserts; it must not get that far."""
        repo = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        with pytest.raises(TenantScopeError):
            repo.replace_for_source(tenant_b.source_id, [])

        assert db.get(KnowledgeChunk, tenant_b.chunk_id) is not None

    def test_replacing_own_chunks_stamps_the_bound_tenant(
        self, db: Session, tenant_a: TenantFixture
    ) -> None:
        repo = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        repo.replace_for_source(tenant_a.source_id, [("Nuevo", [0.1] * EMBEDDING_DIM)])

        chunks = repo.list_for_source(tenant_a.source_id)
        assert [chunk.content for chunk in chunks] == ["Nuevo"]
        assert all(chunk.tenant_id == tenant_a.tenant_id for chunk in chunks)


class TestRetrievalIsScoped:
    async def test_search_never_returns_another_tenants_chunk(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The whole point of RAG isolation: A must not read B's documents."""
        hits = await search_knowledge(db, tenant_a.tenant_id, "secret", k=10, llm=MockLLMProvider())

        assert tenant_b.chunk_id not in {hit.chunk_id for hit in hits}

    async def test_a_tenant_with_no_knowledge_gets_nothing(
        self, db: Session, tenant_b: TenantFixture
    ) -> None:
        """Not "someone else's answer" - nothing."""
        empty_tenant = uuid.uuid4()
        hits = await search_knowledge(db, empty_tenant, "secret", k=10, llm=MockLLMProvider())

        assert hits == []

    async def test_the_tenants_own_chunk_still_wins_when_a_foreign_one_is_nearer(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Late filtering would return an empty list here, not a leak.

        B's chunk is the nearest overall. Ranking first and filtering after
        fills the limit with it and then discards it, so A silently gets
        nothing - the failure this ordering exists to prevent.
        """
        chunk_a = db.get(KnowledgeChunk, tenant_a.chunk_id)
        chunk_b = db.get(KnowledgeChunk, tenant_b.chunk_id)
        assert chunk_a is not None and chunk_b is not None
        query_text = "consulta dirigida"
        # Point the query straight at B's vector.
        chunk_b.embedding = (await MockLLMProvider().embed([query_text], input_type="query"))[0]
        chunk_a.embedding = [0.001] * EMBEDDING_DIM
        db.flush()

        hits = await search_knowledge(
            db, tenant_a.tenant_id, query_text, k=1, llm=MockLLMProvider()
        )

        assert [hit.chunk_id for hit in hits] == [tenant_a.chunk_id]


class TestTheTenantLock:
    def test_it_only_ever_locks_the_tenant_it_was_asked_for(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The budget mutex must not be shared between customers."""
        locked = TenantRepository(db).lock(tenant_a.tenant_id)

        assert locked is not None
        assert locked.id == tenant_a.tenant_id

    def test_locking_an_unknown_tenant_returns_nothing(self, db: Session) -> None:
        assert TenantRepository(db).lock(uuid.uuid4()) is None
