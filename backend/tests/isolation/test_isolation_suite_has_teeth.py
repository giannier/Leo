"""Meta-test: proof that the isolation suite would catch a broken filter.

A green isolation suite is worthless on its own - a suite whose assertions
never fail is also green. TASK-004's verification asks for the test of the
test, so these cases deliberately break the tenant filter and assert that the
properties `test_tenant_isolation.py` relies on are then violated.

If someone weakens `BaseRepository._scoped()`, the isolation suite fails. If
someone instead "fixes" the sabotaged repositories below, these meta-tests
fail. Either way the damage is visible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, Product
from app.repositories import KnowledgeChunkRepository, ProductRepository
from tests.isolation.conftest import EMBEDDING_DIM, TenantFixture

if TYPE_CHECKING:
    pass


class _UnscopedProductRepository(ProductRepository):
    """Sabotaged on purpose: the tenant filter is gone.

    This is exactly the regression the isolation suite exists to catch - a
    `select(Model)` written without the tenant predicate.
    """

    def _scoped(self) -> Select[tuple[Product]]:
        return select(Product)


class _LateFilterChunkRepository(KnowledgeChunkRepository):
    """Sabotaged more subtly: filtered, but only after ranking.

    This one still returns the tenant's own rows, so a naive test would pass.
    It is wrong because the ORDER BY and LIMIT see every tenant's chunks, so
    a foreign chunk can push the tenant's real answer out of the result set.
    """

    def search(
        self, embedding: list[float] | tuple[float, ...], *, limit: int = 5
    ) -> list[KnowledgeChunk]:
        ranked = (
            select(KnowledgeChunk)
            .order_by(KnowledgeChunk.embedding.cosine_distance(list(embedding)))
            .limit(limit)
        )
        rows = self._session.execute(ranked).scalars().all()
        return [chunk for chunk in rows if chunk.tenant_id == self.tenant_id]


class TestTheSuiteDetectsUnfilteredReads:
    def test_a_broken_get_would_expose_a_foreign_row(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        broken = _UnscopedProductRepository(db, tenant_a.tenant_id)
        leaked = broken.get(tenant_b.product_id)
        assert leaked is not None, "sabotage no longer leaks; the meta-test is stale"

        # The real implementation, on the identical call, does not.
        assert ProductRepository(db, tenant_a.tenant_id).get(tenant_b.product_id) is None

    def test_a_broken_list_would_expose_foreign_rows(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        broken = _UnscopedProductRepository(db, tenant_a.tenant_id)
        assert tenant_b.product_id in {product.id for product in broken.list()}

        real = ProductRepository(db, tenant_a.tenant_id)
        assert tenant_b.product_id not in {product.id for product in real.list()}

    def test_a_broken_count_would_include_foreign_rows(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        # `count()` builds its own statement rather than reusing `_scoped()`,
        # so it needs its own proof that the filter is really there. This one
        # is safe as an absolute number: it is scoped, so nothing another
        # tenant does can move it.
        assert ProductRepository(db, tenant_a.tenant_id).count() == 1

        # The unscoped one is asserted as a *property*, not as a count. It
        # used to say `== 2`, meaning "the two fixture products and nothing
        # else in the entire database" - which held only while the database
        # contained nothing else, and broke the moment anybody ran the
        # end-to-end suite or clicked around the panel by hand. The property
        # that actually matters is that the missing filter lets tenant B's row
        # through, and that is true whatever else is stored.
        unscoped = {
            product.id for product in _UnscopedProductRepository(db, tenant_a.tenant_id).list()
        }
        assert tenant_a.product_id in unscoped
        assert tenant_b.product_id in unscoped, "sabotage no longer leaks; the meta-test is stale"


class TestTheSuiteDetectsUnfilteredWrites:
    def test_a_broken_delete_would_destroy_a_foreign_row(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        broken = _UnscopedProductRepository(db, tenant_a.tenant_id)
        assert broken.delete(tenant_b.product_id) is True
        assert db.get(Product, tenant_b.product_id) is None

    def test_a_broken_update_would_modify_a_foreign_row(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        broken = _UnscopedProductRepository(db, tenant_a.tenant_id)
        assert broken.update(tenant_b.product_id, name="Hijacked") is not None


class TestTheSuiteDetectsLateFiltering:
    def test_filtering_after_ranking_loses_the_tenants_own_answer(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The subtle bug: correct rows out, but the right answer missing.

        The query vector points at B's embedding. Ranking across all tenants
        first and filtering afterwards fills the limit with B's chunk and then
        discards it, leaving A with nothing - while a filter applied before
        the ORDER BY returns A's chunk.
        """
        query = [-1.0] * EMBEDDING_DIM

        late = _LateFilterChunkRepository(db, tenant_a.tenant_id)
        assert late.search(query, limit=1) == []

        correct = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        found = correct.search(query, limit=1)
        assert [chunk.id for chunk in found] == [tenant_a.chunk_id]


class TestTheFixtureItselfIsSound:
    def test_the_two_tenants_are_actually_distinct(
        self, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Guards against the fixture quietly returning the same tenant twice,
        which would make every isolation assertion pass for the wrong reason."""
        assert tenant_a.tenant_id != tenant_b.tenant_id
        assert tenant_a.product_id != tenant_b.product_id
        assert tenant_a.chunk_id != tenant_b.chunk_id

    def test_both_tenants_rows_really_exist(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """An empty database would also satisfy every isolation assertion."""
        assert db.get(Product, tenant_a.product_id) is not None
        assert db.get(Product, tenant_b.product_id) is not None


def test_sabotaged_repositories_are_never_exported() -> None:
    """They must stay confined to this module, unusable by application code."""
    import app.repositories as repositories

    for name in ("_UnscopedProductRepository", "_LateFilterChunkRepository"):
        assert not hasattr(repositories, name)


@pytest.mark.parametrize("repository", [_UnscopedProductRepository, _LateFilterChunkRepository])
def test_sabotage_is_confined_to_the_test_package(repository: type) -> None:
    assert repository.__module__.startswith("tests.")
