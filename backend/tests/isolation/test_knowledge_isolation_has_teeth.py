"""Proof that the TASK-012 isolation cases would catch a broken filter.

`test_isolation_suite_has_teeth.py` sabotages `BaseRepository._scoped()`,
which covers every read that starts there. Three knowledge paths do not:

* `claim_for_indexing` is an atomic UPDATE, so it carries `_tenant_predicate`
  on the statement rather than inheriting `_scoped()`.
* `total_chars` is an aggregate built with its own WHERE.
* `replace_for_source` guards ownership with an explicit check before it
  writes, the way `MessageRepository.append` does.

A `_scoped()` sabotage cannot reach any of them, so each is broken here
deliberately and the resulting leak is asserted. If someone removes one of
those guards, `test_knowledge_isolation.py` fails; if someone "fixes" the
sabotage below, these meta-tests fail. Either way the damage is visible.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeSource
from app.repositories import KnowledgeChunkRepository, KnowledgeSourceRepository
from app.repositories.base import TenantScopeError
from app.repositories.knowledge import CLAIMABLE_STATUSES, STATUS_INDEXING
from tests.isolation.conftest import EMBEDDING_DIM, TenantFixture


class _UnscopedClaimRepository(KnowledgeSourceRepository):
    """Sabotaged: the claim UPDATE forgot its tenant predicate."""

    def claim_for_indexing(self, source_id: uuid.UUID) -> bool:
        statement = (
            update(KnowledgeSource)
            .where(KnowledgeSource.id == source_id)
            .where(KnowledgeSource.status.in_(CLAIMABLE_STATUSES))
            .values(status=STATUS_INDEXING, error_msg=None)
        )
        result = cast("CursorResult[Any]", self._session.execute(statement))
        self._session.flush()
        return bool(result.rowcount)


class _UnscopedTotalCharsRepository(KnowledgeSourceRepository):
    """Sabotaged: the plan budget is summed across every tenant."""

    def total_chars(self, *, exclude_source_id: uuid.UUID | None = None) -> int:
        statement = select(func.coalesce(func.sum(KnowledgeSource.char_count), 0)).where(
            KnowledgeSource.deleted_at.is_(None)
        )
        return int(self._session.execute(statement).scalar_one())


class _UncheckedChunkRepository(KnowledgeChunkRepository):
    """Sabotaged: chunks are written without proving who owns the source."""

    def replace_for_source(self, source_id: uuid.UUID, chunks: Any) -> int:
        self.delete_for_source(source_id)
        for content, embedding in chunks:
            self.add(source_id=source_id, content=content, embedding=list(embedding))
        return len(list(chunks))


def _make_claimable(db: Session, source_id: uuid.UUID) -> None:
    source = db.get(KnowledgeSource, source_id)
    assert source is not None
    source.status = "pending"
    db.flush()


class TestTheSuiteDetectsAnUnscopedClaim:
    def test_a_broken_claim_would_seize_a_foreign_source(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _make_claimable(db, tenant_b.source_id)
        broken = _UnscopedClaimRepository(db, tenant_a.tenant_id)

        assert broken.claim_for_indexing(tenant_b.source_id) is True, (
            "sabotage no longer leaks; the meta-test is stale"
        )

    def test_the_real_claim_refuses_the_identical_call(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        _make_claimable(db, tenant_b.source_id)
        real = KnowledgeSourceRepository(db, tenant_a.tenant_id)

        assert real.claim_for_indexing(tenant_b.source_id) is False
        victim = db.get(KnowledgeSource, tenant_b.source_id)
        assert victim is not None
        assert victim.status == "pending"


class TestTheSuiteDetectsAnUnscopedBudget:
    def test_a_broken_total_would_bill_one_tenant_for_anothers_documents(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The consequence is a tenant refused for quota they never spent."""
        db.get(KnowledgeSource, tenant_a.source_id).char_count = 100  # type: ignore[union-attr]
        db.get(KnowledgeSource, tenant_b.source_id).char_count = 900  # type: ignore[union-attr]
        db.flush()

        broken = _UnscopedTotalCharsRepository(db, tenant_a.tenant_id)
        real = KnowledgeSourceRepository(db, tenant_a.tenant_id)

        assert broken.total_chars() >= 1000
        assert real.total_chars() == 100


class TestTheSuiteDetectsAnUncheckedChunkWrite:
    def test_a_broken_write_would_poison_a_foreign_knowledge_base(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        broken = _UncheckedChunkRepository(db, tenant_a.tenant_id)
        broken.replace_for_source(tenant_b.source_id, [("Inyectado", [0.5] * EMBEDDING_DIM)])

        planted = db.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.source_id == tenant_b.source_id)
        ).scalars()
        assert any(chunk.content == "Inyectado" for chunk in planted)

    def test_the_real_write_refuses_the_identical_call(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        real = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        with pytest.raises(TenantScopeError):
            real.replace_for_source(tenant_b.source_id, [("Inyectado", [0.5] * EMBEDDING_DIM)])


def test_sabotaged_repositories_are_never_exported() -> None:
    """They must stay confined to this module, unusable by application code."""
    import app.repositories as repositories

    for name in (
        "_UnscopedClaimRepository",
        "_UnscopedTotalCharsRepository",
        "_UncheckedChunkRepository",
    ):
        assert not hasattr(repositories, name)


@pytest.mark.parametrize(
    "repository",
    [_UnscopedClaimRepository, _UnscopedTotalCharsRepository, _UncheckedChunkRepository],
)
def test_sabotage_is_confined_to_the_test_package(repository: type) -> None:
    assert repository.__module__.startswith("tests.")
