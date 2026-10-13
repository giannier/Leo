"""Fetching the photo a customer sent (Feature 004, TASK-429).

The webhook only carries a reference to an image. The turn that needs it (a payment
receipt, step 4 of the pipeline) runs later, after the debounce window, and fetches
it here: from private storage when the simulator already put it there, from the
provider (with the tenant's own credential) when it came through WhatsApp.

It never raises: an image that cannot be had is `None`, and the pipeline tells the
model to ask the customer to send it again.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.orders.proofs import InboundImage, ReceivedImage
from app.orders.vision import IMAGE_TYPES, MAX_IMAGE_BYTES
from app.providers.storage.base import StorageError, StorageProvider
from app.providers.whatsapp.base import WhatsAppError, WhatsAppProvider
from app.repositories.whatsapp import WhatsAppNumberRepository
from app.services.connection import read_credentials

logger = logging.getLogger(__name__)


class InboundImageReader:
    """`ImageReader` over private storage and the WhatsApp provider."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        *,
        storage: StorageProvider | None,
        provider: WhatsAppProvider | None = None,
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._storage = storage
        self._provider = provider

    async def read(self, image: InboundImage) -> ReceivedImage | None:
        if image.stored is not None:
            return self._from_storage(*image.stored)
        if image.media is not None and image.media.link:
            return await self._from_provider(image.media.link, image.media.mime_type)
        return None

    def _from_storage(self, key: str, mime_type: str) -> ReceivedImage | None:
        if self._storage is None or mime_type not in IMAGE_TYPES:
            return None
        try:
            data = self._storage.get(key)
        except StorageError:
            logger.warning(
                "a stored inbound image is gone", extra={"tenant_id": str(self._tenant_id)}
            )
            return None
        return ReceivedImage(data=data, mime_type=mime_type)

    async def _from_provider(self, link: str, declared: str | None) -> ReceivedImage | None:
        if self._provider is None:
            return None
        numbers = WhatsAppNumberRepository(self._session, self._tenant_id).list_connected()
        credentials = read_credentials(numbers[0]) if numbers else None
        if credentials is None:
            return None
        try:
            media = await self._provider.download_media(
                credentials, link=link, max_bytes=MAX_IMAGE_BYTES
            )
        except WhatsAppError as exc:
            logger.warning(
                "an inbound image could not be downloaded",
                extra={"tenant_id": str(self._tenant_id), "reason": type(exc).__name__},
            )
            return None
        mime = media.mime_type if media.mime_type in IMAGE_TYPES else (declared or "")
        if mime not in IMAGE_TYPES or not media.data:
            return None
        return ReceivedImage(data=media.data, mime_type=mime)
