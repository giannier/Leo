"""The tenant's media library (Feature 004)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from app.models.media import MediaAsset
from app.repositories.base import BaseRepository


class MediaAssetRepository(BaseRepository[MediaAsset]):
    """The files one tenant has uploaded for the assistant to send."""

    model = MediaAsset

    def by_clave(self, clave: str) -> MediaAsset | None:
        """The asset with this key, whether or not it is active."""
        statement = self._scoped().where(MediaAsset.clave == clave)
        return self._session.execute(statement).scalar_one_or_none()

    def active(self) -> Sequence[MediaAsset]:
        """Assets the assistant may send, in a stable order."""
        statement = self._scoped().where(MediaAsset.is_active.is_(True)).order_by(MediaAsset.clave)
        return self._session.execute(statement).scalars().all()

    def for_product(self, product_id: uuid.UUID) -> Sequence[MediaAsset]:
        """Active assets linked to one product."""
        statement = (
            self._scoped()
            .where(MediaAsset.product_id == product_id, MediaAsset.is_active.is_(True))
            .order_by(MediaAsset.orden, MediaAsset.clave)
        )
        return self._session.execute(statement).scalars().all()

    def for_products(self, product_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[MediaAsset]]:
        """Active assets linked to any of these products, grouped by product.

        One query for a whole search result instead of one per product.
        """
        if not product_ids:
            return {}
        statement = (
            self._scoped()
            .where(MediaAsset.product_id.in_(product_ids), MediaAsset.is_active.is_(True))
            .order_by(MediaAsset.orden, MediaAsset.clave)
        )
        grouped: dict[uuid.UUID, list[MediaAsset]] = {}
        for asset in self._session.execute(statement).scalars():
            if asset.product_id is not None:
                grouped.setdefault(asset.product_id, []).append(asset)
        return grouped

    def for_promotion(self, promotion_id: uuid.UUID) -> Sequence[MediaAsset]:
        """Active assets linked to one promotion."""
        statement = (
            self._scoped()
            .where(MediaAsset.promotion_id == promotion_id, MediaAsset.is_active.is_(True))
            .order_by(MediaAsset.orden, MediaAsset.clave)
        )
        return self._session.execute(statement).scalars().all()

    def expiring_before(self, cutoff: datetime) -> Sequence[MediaAsset]:
        """Active, already-uploaded assets whose provider copy expires before `cutoff`.

        Feeds the daily `media.refresh_provider_ids` job. Assets never uploaded
        are not listed: they are uploaded when first needed.
        """
        statement = self._scoped().where(
            MediaAsset.is_active.is_(True),
            MediaAsset.provider_media_id.is_not(None),
            MediaAsset.provider_media_expires_at < cutoff,
        )
        return self._session.execute(statement).scalars().all()

    def store_provider_media(
        self, asset: MediaAsset, *, media_id: str, expires_at: datetime | None
    ) -> MediaAsset:
        """Remember the id the provider gave the uploaded file."""
        asset.provider_media_id = media_id
        asset.provider_media_expires_at = expires_at
        self._session.flush()
        return asset

    def clear_provider_media(self, asset: MediaAsset) -> MediaAsset:
        """Forget the uploaded copy, e.g. after the file was replaced."""
        asset.provider_media_id = None
        asset.provider_media_expires_at = None
        self._session.flush()
        return asset
