"""Getting a library file to WhatsApp: upload once, reuse, fall back to a link.

C-17. WhatsApp downloads a file from a public link or uses a `media_id` the
provider issued for an earlier upload, and the object store is private (MinIO
in development is not reachable from the internet at all). So the first time a
file is sent it is uploaded to the provider; the id and the moment the provider
forgets the file are kept on the asset; later sends reuse the id; and a day
before it lapses a job uploads it again.

If the upload cannot be done - the provider is down, the credential was
revoked - the send does not fail on that account: it falls back to a link that
expires in one hour, which is what the object store can offer on its own.
Better a file that may or may not arrive than a customer who asked for the
price list and heard nothing.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.media import MediaAsset
from app.providers.storage.base import StorageError, StorageProvider
from app.providers.whatsapp.base import (
    MediaKind,
    MediaRef,
    NumberCredentials,
    WhatsAppError,
    WhatsAppProvider,
)
from app.repositories.media import MediaAssetRepository
from app.repositories.whatsapp import WhatsAppNumberRepository
from app.services.connection import read_credentials

logger = logging.getLogger(__name__)

# How long the fallback link lives (C-17).
LINK_LIFETIME_SECONDS = 3600

# A stored id that lapses within this window is not trusted at send time: the
# message would be accepted and the file gone by the time it is fetched.
SEND_MARGIN = timedelta(hours=1)

# `media.refresh_provider_ids` re-uploads what lapses within this horizon, so
# a daily run always gets a second chance before the id is actually lost.
REFRESH_HORIZON = timedelta(hours=48)

MEDIA_KINDS: dict[str, MediaKind] = {
    "imagen": "image",
    "documento": "document",
    "video": "video",
    "audio": "audio",
}


class MediaUnavailable(RuntimeError):
    """The file cannot be sent at all: the object store no longer has it."""


@dataclass(frozen=True)
class RefreshOutcome:
    """What one run of the refresh job did."""

    considered: int = 0
    refreshed: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return f"considered={self.considered} refreshed={self.refreshed} failed={self.failed}"


class MediaDelivery:
    """Resolves library assets into something the provider can send."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        provider: WhatsAppProvider,
        storage: StorageProvider,
        credentials: NumberCredentials,
        sender: str,
    ) -> None:
        self._tenant_id = tenant_id
        self._assets = MediaAssetRepository(session, tenant_id)
        self._provider = provider
        self._storage = storage
        self._credentials = credentials
        self._sender = sender

    async def reference_for(self, asset: MediaAsset, *, now: datetime | None = None) -> MediaRef:
        """How to send this asset: a stored id if it is still good, a fresh
        upload if not, a one-hour link if the upload fails."""
        at = now or datetime.now(UTC)
        if self._is_usable(asset, at):
            return self._by_id(asset)

        if await self.upload(asset, now=at):
            return self._by_id(asset)

        # The upload failed (already logged). Hand over a link instead.
        return self._by_link(asset)

    async def upload(self, asset: MediaAsset, *, now: datetime | None = None) -> bool:
        """Upload the file and remember the id. False if the provider refused.

        An asset whose file has disappeared from the object store raises
        `MediaUnavailable`: there is nothing to upload and nothing to link.
        """
        data = await self._read(asset)
        try:
            uploaded = await self._provider.upload_media(
                self._credentials,
                sender=self._sender,
                data=data,
                mime=asset.mime_type,
                filename=asset.filename,
            )
        except WhatsAppError as exc:
            logger.warning(
                "media upload failed",
                extra={
                    "tenant_id": str(self._tenant_id),
                    "asset_id": str(asset.id),
                    "reason": str(exc),
                },
            )
            return False

        self._assets.store_provider_media(
            asset, media_id=uploaded.media_id, expires_at=uploaded.expires_at
        )
        return True

    @staticmethod
    def _is_usable(asset: MediaAsset, now: datetime) -> bool:
        if not asset.provider_media_id or asset.provider_media_expires_at is None:
            # An id with no known expiry cannot be trusted either.
            return False
        return asset.provider_media_expires_at - SEND_MARGIN > now

    async def _read(self, asset: MediaAsset) -> bytes:
        try:
            return await asyncio.to_thread(self._storage.get, asset.storage_key)
        except StorageError as exc:
            raise MediaUnavailable(
                f"asset {asset.id} has no file in storage ({asset.storage_key})"
            ) from exc

    @staticmethod
    def _by_id(asset: MediaAsset) -> MediaRef:
        return MediaRef(
            kind=MEDIA_KINDS[asset.tipo],
            media_id=asset.provider_media_id,
            filename=asset.filename,
        )

    def _by_link(self, asset: MediaAsset) -> MediaRef:
        try:
            link = self._storage.presigned_url(asset.storage_key, expires_in=LINK_LIFETIME_SECONDS)
        except StorageError as exc:
            raise MediaUnavailable(f"asset {asset.id} has no file in storage") from exc
        return MediaRef(kind=MEDIA_KINDS[asset.tipo], link=link, filename=asset.filename)


def tenants_with_expiring_media(session: Session, cutoff: datetime) -> list[uuid.UUID]:
    """Which businesses have uploaded files that lapse before `cutoff`.

    Platform-wide, like the trial jobs: the one query that has to look across
    tenants to find out who has work. Everything after it re-scopes to a single
    tenant before touching a row.
    """
    statement = (
        select(MediaAsset.tenant_id)
        .where(
            MediaAsset.is_active.is_(True),
            MediaAsset.provider_media_id.is_not(None),
            MediaAsset.provider_media_expires_at < cutoff,
        )
        .distinct()
    )
    return list(session.execute(statement).scalars().all())


async def refresh_provider_ids(
    session: Session,
    *,
    provider: WhatsAppProvider,
    storage: StorageProvider,
    now: datetime | None = None,
    horizon: timedelta = REFRESH_HORIZON,
) -> RefreshOutcome:
    """Upload again every file whose provider copy lapses within `horizon`.

    Each asset is committed on its own: one broken file, or one tenant whose
    number was disconnected, must not stop the others, and a rollback halfway
    through would waste the uploads already done.
    """
    at = now or datetime.now(UTC)
    cutoff = at + horizon
    considered = refreshed = failed = 0

    for tenant_id in tenants_with_expiring_media(session, cutoff):
        numbers = WhatsAppNumberRepository(session, tenant_id).list_connected()
        credentials = read_credentials(numbers[0]) if numbers else None
        assets = MediaAssetRepository(session, tenant_id).expiring_before(cutoff)
        if credentials is None:
            # Nothing to upload with. The assets stay as they are; the send
            # path will upload (or link) when a number exists again.
            considered += len(assets)
            failed += len(assets)
            logger.warning("no usable number to refresh media", extra={"tenant_id": str(tenant_id)})
            continue

        delivery = MediaDelivery(
            session,
            tenant_id,
            provider=provider,
            storage=storage,
            credentials=credentials,
            sender=numbers[0].phone_e164,
        )
        for asset in assets:
            considered += 1
            try:
                ok = await delivery.upload(asset, now=at)
                session.commit()
            except Exception:  # noqa: BLE001 - one bad asset must not stop the run
                session.rollback()
                logger.exception("could not refresh a media id", extra={"asset_id": str(asset.id)})
                failed += 1
                continue
            if ok:
                refreshed += 1
            else:
                failed += 1
    return RefreshOutcome(considered=considered, refreshed=refreshed, failed=failed)
