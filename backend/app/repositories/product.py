"""Product catalog repository."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from app.models.business import Product
from app.repositories.base import BaseRepository


class ProductRepository(BaseRepository[Product]):
    """Catalog access for one tenant.

    Products are soft-deleted, so every listing here excludes `deleted_at`
    rows. Callers that genuinely need deleted rows must say so explicitly.
    """

    model = Product

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
