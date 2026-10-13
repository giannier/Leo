"""Product catalog repository."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.models.business import Product, ProductCategory
from app.repositories.base import BaseRepository


def _contains(text: str) -> str:
    """A LIKE pattern that matches `text` anywhere, taking its wildcards literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class ProductCategoryRepository(BaseRepository[ProductCategory]):
    """The categories of one tenant's catalogue (Milestone B, decision 13)."""

    model = ProductCategory

    def by_name(self, nombre: str) -> ProductCategory | None:
        statement = self._scoped().where(
            func.lower(ProductCategory.nombre) == " ".join(nombre.split()).lower()
        )
        return self._session.execute(statement).scalar_one_or_none()

    def ensure(self, nombre: str) -> ProductCategory:
        """The category with this name (ignoring case and extra spaces); created if new."""
        clean = " ".join(nombre.split())
        existing = self.by_name(clean)
        if existing is not None:
            return existing
        return self.add(nombre=clean)


class ProductRepository(BaseRepository[Product]):
    """Catalog access for one tenant.

    Products are soft-deleted, so every listing here excludes `deleted_at`
    rows. Callers that genuinely need deleted rows must say so explicitly.
    """

    model = Product

    def add(self, **values: Any) -> Product:
        """Create a product, linked to its category row (created if new)."""
        self._link_category(values)
        return super().add(**values)

    def update(self, entity_id: uuid.UUID, **values: Any) -> Product | None:
        """Update a product; a new category text moves it to that category row."""
        if "category" in values:
            self._link_category(values)
        return super().update(entity_id, **values)

    def _link_category(self, values: dict[str, Any]) -> None:
        raw = values.get("category")
        text = " ".join(str(raw).split()) if raw is not None else ""
        if not text:
            values["category"] = None
            values["category_id"] = None
            return
        category = ProductCategoryRepository(self._session, self._tenant_id).ensure(text)
        values["category"] = category.nombre
        values["category_id"] = category.id

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
        """The active categories that have live products, by `orden` then name.

        Read from `product_categories` (Milestone B, decision 13): a category the
        owner switched off is not offered, even if products still carry it.
        """
        statement = (
            select(ProductCategory.nombre)
            .join(Product, Product.category_id == ProductCategory.id)
            .where(ProductCategory.tenant_id == self._tenant_id)
            .where(self._tenant_predicate)
            .where(
                ProductCategory.activa.is_(True),
                Product.deleted_at.is_(None),
                Product.is_active.is_(True),
            )
            .group_by(ProductCategory.id, ProductCategory.nombre, ProductCategory.orden)
            .order_by(ProductCategory.orden, func.lower(ProductCategory.nombre))
        )
        return list(self._session.execute(statement).scalars())

    def get_active(self, product_id: uuid.UUID) -> Product | None:
        """One live product of this tenant, or None (another tenant's, deleted or off)."""
        product = self.get(product_id)
        if product is None or product.deleted_at is not None or not product.is_active:
            return None
        return product

    def by_ids(self, product_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, Product]:
        """The products of this tenant among `product_ids`, by id, in one query.

        Deleted and deactivated ones are included: this serves the orders that
        already hold a line for the product, and what was sold is read as it is,
        not as the catalogue shows it today. Ids of other tenants are absent.
        """
        if not product_ids:
            return {}
        statement = self._scoped().where(Product.id.in_(list(product_ids)))
        return {product.id: product for product in self._session.execute(statement).scalars()}

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
