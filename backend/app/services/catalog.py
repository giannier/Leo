"""Product catalogue: CRUD and the two-step import (TASK-011).

The import is deliberately two calls rather than one. A customer uploading
five hundred products has no way to know what our parser made of their
spreadsheet until they see it, and a single call that silently creates
everything is unrecoverable - there is no undo for four hundred rows.

So the upload only reports, and nothing is written until a second call
confirms. The parsed result waits in Redis rather than in the database,
because an abandoned preview is the common case: the customer looks at the
error list, fixes their file and uploads again. A table would accumulate that
litter and need its own cleanup job; a key with a one-hour expiry cleans
itself (contracts/api.md).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.orm import Session

from app.models.business import Product
from app.repositories.product import ProductRepository
from app.services.csv_import import InvalidRow, parse_catalogue

PREVIEW_TTL_SECONDS = 3600
PREVIEW_KEY = "catalog:import:{tenant_id}:{preview_id}"


class PreviewNotFound(LookupError):
    """No preview with that id for this tenant, or it has expired."""


@dataclass(frozen=True)
class ImportPreview:
    preview_id: uuid.UUID
    valid_rows: int
    invalid_rows: list[InvalidRow]


class CatalogService:
    """Products for one tenant."""

    def __init__(
        self, session: Session, tenant_id: uuid.UUID, *, redis: Redis | None = None
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._products = ProductRepository(session, tenant_id)
        self._redis = redis

    # ------------------------------------------------------------------ CRUD

    def list_products(
        self,
        *,
        search: str | None = None,
        category: str | None = None,
        is_active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Product], int]:
        """A page of products, plus the total that matched.

        The total is what lets a client render "showing 50 of 312" and know
        whether another page exists, which a bare list cannot express.
        """
        criteria: list[Any] = [Product.deleted_at.is_(None)]
        if search:
            criteria.append(Product.name.ilike(f"%{search}%"))
        if category:
            criteria.append(Product.category == category)
        if is_active is not None:
            criteria.append(Product.is_active.is_(is_active))

        total = self._products.count(*criteria)
        rows = self._products.list(*criteria, limit=limit, offset=offset)
        return list(rows), total

    def get(self, product_id: uuid.UUID) -> Product | None:
        product = self._products.get(product_id)
        if product is None or product.deleted_at is not None:
            # A soft-deleted product is gone as far as the API is concerned.
            return None
        return product

    def create(self, **values: Any) -> Product:
        product = self._products.add(**values)
        self._session.commit()
        return product

    def update(self, product_id: uuid.UUID, **values: Any) -> Product | None:
        if self.get(product_id) is None:
            return None
        product = self._products.update(product_id, **values)
        self._session.commit()
        return product

    def soft_delete(self, product_id: uuid.UUID) -> bool:
        removed = self._products.soft_delete(product_id)
        if removed:
            self._session.commit()
        return removed

    # ---------------------------------------------------------------- import

    async def stage_import(self, raw: bytes) -> ImportPreview:
        """Parse an upload and hold the result for confirmation.

        Nothing is written to the catalogue here. Raises whatever
        `parse_catalogue` raises when the file cannot be read at all, which is
        a different failure from a file whose rows are individually wrong.
        """
        parsed = parse_catalogue(raw)
        preview_id = uuid.uuid4()

        if self._redis is not None:
            payload = json.dumps(
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "rows": parsed.valid,
                    "invalid": [{"row": row.row, "reason": row.reason} for row in parsed.invalid],
                }
            )
            await self._redis.set(
                PREVIEW_KEY.format(tenant_id=self._tenant_id, preview_id=preview_id),
                payload,
                ex=PREVIEW_TTL_SECONDS,
            )

        return ImportPreview(
            preview_id=preview_id,
            valid_rows=parsed.valid_rows,
            invalid_rows=parsed.invalid,
        )

    async def confirm_import(self, preview_id: uuid.UUID) -> int:
        """Create the products a preview accepted. Returns how many.

        The key is namespaced by tenant, so one business cannot confirm
        another's upload even holding its id - and the delete happens before
        the insert loop, so a double click cannot import twice.
        """
        if self._redis is None:  # pragma: no cover - wired in every real caller
            raise PreviewNotFound(str(preview_id))

        key = PREVIEW_KEY.format(tenant_id=self._tenant_id, preview_id=preview_id)
        payload = await self._redis.get(key)
        if payload is None:
            raise PreviewNotFound(str(preview_id))
        await self._redis.delete(key)

        rows = json.loads(payload)["rows"]
        for row in rows:
            self._products.add(
                name=row["name"],
                description=row["description"],
                # Back to Decimal: it travelled through JSON as a string
                # precisely so it would not become a float on the way.
                price=Decimal(row["price"]),
                stock=row["stock"],
                category=row["category"],
            )
        self._session.commit()
        return len(rows)
