"""Multi-tenant isolation suite.

Mandated by the Constitution and never skipped or disabled. Each test drives
a real repository against two real tenants and asserts that tenant A cannot
observe or modify anything belonging to tenant B.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models import Product
from app.repositories import (
    ConversationRepository,
    KnowledgeChunkRepository,
    KnowledgeSourceRepository,
    MessageRepository,
    ProductRepository,
    TenantScopeError,
)
from tests.isolation.conftest import EMBEDDING_DIM, TenantFixture


class TestReadsCannotCrossTenants:
    def test_get_returns_none_for_another_tenants_product(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = ProductRepository(db, tenant_a.tenant_id)
        assert repo.get(tenant_b.product_id) is None

    def test_get_does_not_distinguish_foreign_from_missing(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Both must be None. A different error would leak that the id exists."""
        repo = ProductRepository(db, tenant_a.tenant_id)
        assert repo.get(tenant_b.product_id) == repo.get(uuid.uuid4())

    def test_list_excludes_other_tenants_rows(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = ProductRepository(db, tenant_a.tenant_id)
        ids = {product.id for product in repo.list()}
        assert tenant_a.product_id in ids
        assert tenant_b.product_id not in ids

    def test_count_only_counts_own_rows(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        assert ProductRepository(db, tenant_a.tenant_id).count() == 1

    def test_messages_of_a_foreign_conversation_are_invisible(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Knowing the conversation id must not be enough to read it."""
        repo = MessageRepository(db, tenant_a.tenant_id)
        assert repo.list_for_conversation(tenant_b.conversation_id) == []

    def test_conversation_listing_is_scoped(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = ConversationRepository(db, tenant_a.tenant_id)
        ids = {conversation.id for conversation in repo.list_open()}
        assert ids == {tenant_a.conversation_id}

    def test_knowledge_totals_ignore_other_tenants(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = KnowledgeSourceRepository(db, tenant_a.tenant_id)
        assert repo.active_file_count() == 1


class TestVectorSearchIsScoped:
    def test_similarity_search_never_returns_a_foreign_chunk(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The nearest chunk overall belongs to B; A must still not see it.

        The query vector points at B's embedding, so an unscoped search would
        rank B's chunk first. This is the test that would catch a tenant
        filter applied after ordering instead of before.
        """
        repo = KnowledgeChunkRepository(db, tenant_a.tenant_id)
        results = repo.search([-1.0] * EMBEDDING_DIM, limit=10)
        returned = {chunk.id for chunk in results}
        assert tenant_b.chunk_id not in returned
        assert returned == {tenant_a.chunk_id}


class TestWritesCannotCrossTenants:
    def test_update_of_a_foreign_row_is_a_no_op(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = ProductRepository(db, tenant_a.tenant_id)
        assert repo.update(tenant_b.product_id, name="Hijacked") is None

        victim = db.get(Product, tenant_b.product_id)
        assert victim is not None
        assert victim.name == "Product B"

    def test_delete_of_a_foreign_row_is_refused(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = ProductRepository(db, tenant_a.tenant_id)
        assert repo.delete(tenant_b.product_id) is False
        assert db.get(Product, tenant_b.product_id) is not None

    def test_soft_delete_of_a_foreign_row_is_refused(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = ProductRepository(db, tenant_a.tenant_id)
        assert repo.soft_delete(tenant_b.product_id) is False

        victim = db.get(Product, tenant_b.product_id)
        assert victim is not None
        assert victim.deleted_at is None

    def test_delete_all_leaves_the_other_tenant_untouched(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """The most destructive operation on the repository."""
        removed = ProductRepository(db, tenant_a.tenant_id).delete_all()
        assert removed == 1
        assert db.get(Product, tenant_b.product_id) is not None

    def test_add_cannot_be_told_to_write_for_another_tenant(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = ProductRepository(db, tenant_a.tenant_id)
        with pytest.raises(TenantScopeError):
            repo.add(name="Planted", price=Decimal("1.00"), tenant_id=tenant_b.tenant_id)

    def test_add_stamps_the_bound_tenant(self, db: Session, tenant_a: TenantFixture) -> None:
        repo = ProductRepository(db, tenant_a.tenant_id)
        product = repo.add(name="Legit", price=Decimal("5.00"))
        assert product.tenant_id == tenant_a.tenant_id

    def test_tenant_id_cannot_be_reassigned(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        """Moving a row between tenants is never a repository operation."""
        repo = ProductRepository(db, tenant_a.tenant_id)
        with pytest.raises(TenantScopeError):
            repo.update(tenant_a.product_id, tenant_id=tenant_b.tenant_id)

    def test_appending_to_a_foreign_conversation_is_refused(
        self, db: Session, tenant_a: TenantFixture, tenant_b: TenantFixture
    ) -> None:
        repo = MessageRepository(db, tenant_a.tenant_id)
        with pytest.raises(TenantScopeError):
            repo.append(
                conversation_id=tenant_b.conversation_id,
                direction="outbound",
                author="agent",
                content="Injected",
            )


class TestRepositoryConstruction:
    def test_a_repository_for_a_non_tenant_model_is_rejected(self) -> None:
        """`users` has no tenant_id, so it must not get a BaseRepository."""
        from app.models import User
        from app.repositories.base import BaseRepository

        with pytest.raises(TypeError, match="tenant_id"):

            class UserRepository(BaseRepository[User]):  # type: ignore[type-var]
                model = User
