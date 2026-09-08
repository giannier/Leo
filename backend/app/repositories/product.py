"""Product catalog repository."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select

from app.models.business import Product
from app.repositories.base import BaseRepository


class ProductRepository(BaseRepository[Product]):
    """Catalog access for one tenant.

    Products are soft-deleted, so every listing here excludes `deleted_at`
    rows. Callers that genuinely need deleted rows must say so explicitly.
    """

    model = Product

    def active_names(self, *, limit: int) -> list[str]:
        """Just the names of live products - what the agent may say aloud.

        A column read rather than `list_active()` because the caller is the
        per-reply catalogue guard and runs on the customer's latency: it needs
        the strings, not hydrated rows with prices, stock and descriptions.

        Built from `_tenant_predicate` for the same reason every other read
        here is - the Constitution requires the tenant filter at this layer -
        but it cannot go through `_scoped()`, which selects whole entities.
        The filter is applied before any other criterion, exactly as there.
        """
        statement = (
            select(Product.name)
            .where(self._tenant_predicate)
            .where(Product.deleted_at.is_(None), Product.is_active.is_(True))
            .limit(limit)
        )
        return list(self._session.execute(statement).scalars().all())

    def list_active(self, *, limit: int | None = None) -> Sequence[Product]:
        """Live, sellable products - what the agent is allowed to quote."""
        return self.list(
            Product.deleted_at.is_(None),
            Product.is_active.is_(True),
            limit=limit,
        )

    def list_including_deleted(self) -> Sequence[Product]:
        return self.list()

    def soft_delete(self, entity_id: uuid.UUID) -> bool:
        """Mark a product deleted without removing its row.

        Orders and conversations reference products historically; a hard
        delete would rewrite the past.
        """
        product = self.get(entity_id)
        if product is None or product.deleted_at is not None:
            return False
        product.deleted_at = datetime.now(UTC)
        self._session.flush()
        return True
