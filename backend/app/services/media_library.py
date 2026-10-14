"""The business's media library: upload, replace, describe (Feature 004, TASK-436).

A file is judged by its **content**, not by its name or the type the browser
claims: an `.exe` renamed to `.jpg` is refused because its first bytes are not
a JPEG's. The limits are WhatsApp's (`data-model.md` §0017): image JPG/PNG up to
5 MB, video MP4 and audio up to 16 MB, document PDF up to 100 MB.

Files go to the private bucket under the tenant's prefix. Replacing a file
forgets the provider's media id, so the next send uploads the new one.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.media import MediaAsset
from app.providers.storage.base import StorageError, StorageProvider
from app.repositories.media import MediaAssetRepository

logger = logging.getLogger(__name__)

MB = 1024 * 1024
LIMITS = {"imagen": 5 * MB, "video": 16 * MB, "audio": 16 * MB, "documento": 100 * MB}
MAX_UPLOAD = max(LIMITS.values())


class MediaRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Sniffed:
    tipo: str
    mime_type: str
    extension: str


def sniff(data: bytes) -> Sniffed | None:
    """What the bytes are, among the types WhatsApp sends. None: not one of them."""
    head = data[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return Sniffed("imagen", "image/jpeg", "jpg")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Sniffed("imagen", "image/png", "png")
    if head.startswith(b"%PDF-"):
        return Sniffed("documento", "application/pdf", "pdf")
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B "):
            return Sniffed("audio", "audio/mp4", "m4a")
        return Sniffed("video", "video/mp4", "mp4")
    if head.startswith(b"OggS"):
        return Sniffed("audio", "audio/ogg", "ogg")
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return Sniffed("audio", "audio/mpeg", "mp3")
    if head.startswith(b"#!AMR"):
        return Sniffed("audio", "audio/amr", "amr")
    if head[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return Sniffed("audio", "audio/aac", "aac")
    return None


def check(data: bytes) -> Sniffed:
    """The sniffed type, or `MediaRejected` with the reason."""
    if not data:
        raise MediaRejected("empty_file", "El archivo está vacío.")
    found = sniff(data)
    if found is None:
        raise MediaRejected(
            "unsupported_file",
            "Solo imágenes JPG o PNG, videos MP4, audios y documentos PDF.",
        )
    if len(data) > LIMITS[found.tipo]:
        raise MediaRejected(
            "file_too_large",
            f"Un archivo de tipo {found.tipo} admite hasta {LIMITS[found.tipo] // MB} MB.",
        )
    return found


class MediaLibrary:
    def __init__(self, session: Session, tenant_id: uuid.UUID, storage: StorageProvider) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._storage = storage
        self._repo = MediaAssetRepository(session, tenant_id)

    def _store(self, data: bytes, found: Sniffed) -> str:
        key = f"tenants/{self._tenant_id}/media/{uuid.uuid4()}.{found.extension}"
        self._storage.put(key, data, content_type=found.mime_type)
        return key

    def upload(self, data: bytes, filename: str, **fields: object) -> MediaAsset:
        found = check(data)
        if self._repo.by_clave(str(fields["clave"])) is not None:
            raise MediaRejected("clave_taken", "Ya tienes un archivo con esa clave.")
        key = self._store(data, found)
        return self._repo.add(
            **fields,
            tipo=found.tipo,
            storage_key=key,
            filename=filename[:200] or f"archivo.{found.extension}",
            mime_type=found.mime_type,
            size_bytes=len(data),
        )

    def replace_file(self, asset: MediaAsset, data: bytes, filename: str) -> MediaAsset:
        """A new file under the same key. The type may not change: a QR stays an image."""
        found = check(data)
        if found.tipo != asset.tipo:
            raise MediaRejected(
                "type_changed", f"Este recurso es {asset.tipo}; sube un archivo del mismo tipo."
            )
        old_key = asset.storage_key
        asset.storage_key = self._store(data, found)
        asset.filename = filename[:200] or asset.filename
        asset.mime_type = found.mime_type
        asset.size_bytes = len(data)
        self._repo.clear_provider_media(asset)
        self._session.flush()
        self._discard(old_key)
        return asset

    def delete(self, asset: MediaAsset) -> None:
        key = asset.storage_key
        self._session.delete(asset)
        self._session.flush()
        self._discard(key)

    def _discard(self, key: str) -> None:
        try:
            self._storage.delete(key)
        except StorageError:
            logger.warning("could not delete a replaced media object")
