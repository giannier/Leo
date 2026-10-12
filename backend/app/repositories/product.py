"""Product catalog repository."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select

from app.models.business import Product
from app.repositories.base import BaseRepository


def _contains(text: str) -> str:
    """A LIKE pattern that matches `text` anywhere, taking its wildcards literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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

    def search_active(
        self,
        query: str = "",
        *,
        categoria: str | None = None,
        solo_paquetes: bool = False,
        limit: int,
    ) -> Sequence[Product]:
        """Live products matching a customer's words, featured ones first.

        `query` matches the name and `categoria` the category, both without
        regard to case and with `%` and `_` taken literally: they come from the
        model, which got them from a customer. `solo_paquetes` keeps only the
        products sold as a package of N selections.
        """
        statement = self._scoped().where(Product.deleted_at.is_(None), Product.is_active.is_(True))
        if query:
            statement = statement.where(Product.name.ilike(_contains(query), escape="\\"))
        if categoria:
            statement = statement.where(Product.category.ilike(_contains(categoria), escape="\\"))
        if solo_paquetes:
            statement = statement.where(Product.bundle_size.is_not(None))
        statement = statement.order_by(Product.destacado.desc(), Product.name).limit(limit)
        return self._session.execute(statement).scalars().all()

    def active_categories(self) -> list[str]:
        """The distinct categories of live products, in alphabetical order.

        Categories are free text on the product (there is no category table),
        so this is what the catalogue actually contains.
        """
        statement = (
            select(Product.category)
            .where(self._tenant_predicate)
            .where(
                Product.deleted_at.is_(None),
                Product.is_active.is_(True),
                Product.category.is_not(None),
                Product.category != "",
            )
            .distinct()
            .order_by(Product.category)
        )
        return [category for category in self._session.execute(statement).scalars() if category]

    def get_active(self, product_id: uuid.UUID) -> Product | None:
        """One live product of this tenant, or None (another tenant's, deleted or off)."""
        product = self.get(product_id)
        if product is None or product.deleted_at is not None or not product.is_active:
            return None
        return product

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
