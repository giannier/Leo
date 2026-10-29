"""A product's photos (Feature 018, HU-02).

Photos are media assets linked to the product (`media_assets.product_id`), so `buscar_productos` and
`ver_producto` already offer them to the model. The business only uploads the picture: the key, the
title and the "when to send it" are written here from the product's name. At most ten, in the
order the business sets; the first is the main one.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.models.business import Product
from app.models.media import MediaAsset
from app.providers.storage.base import StorageProvider
from app.repositories.media import MediaAssetRepository
from app.services.media_library import MediaLibrary, MediaRejected

MAX_PHOTOS = 10
_CLAVE_ROOM = 40 - len("foto__99")


def _slug(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name.casefold())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")[:_CLAVE_ROOM].strip("_") or "producto"


def _title(name: str) -> str:
    return f"Foto de {name}"[:120]


def _when(name: str) -> str:
    return f"Cuando quiera ver {name} o pida una foto"[:300]


def _free_clave(repo: MediaAssetRepository, name: str) -> str:
    base = f"foto_{_slug(name)}"
    for n in range(1, 100):
        clave = f"{base}_{n}"
        if repo.by_clave(clave) is None:
            return clave
    return f"foto_{uuid.uuid4().hex[:12]}"


def follow_rename(session: Session, tenant_id: uuid.UUID, product: Product, old_name: str) -> int:
    """A renamed product's photos take the new name, so the assistant stops reading the old one.

    Giannier, 2026-10-29: «Agua de Mesa de 500 ml» became «… 625 ml» and its photo still offered
    «Cuando quiera ver Agua de Mesa de 500 ml». Only what Leo wrote from the name changes (key,
    title and «when to send it»); a text the business wrote itself is left as it is. Returns how
    many photos changed.
    """
    old, new = " ".join(old_name.split()), " ".join(product.name.split())
    if old == new:
        return 0
    repo = MediaAssetRepository(session, tenant_id)
    prefix = f"foto_{_slug(old)}_"
    changed = 0
    for photo in repo.list(MediaAsset.product_id == product.id, MediaAsset.tipo == "imagen"):
        before = (photo.clave, photo.titulo, photo.cuando_usar)
        if photo.titulo == _title(old):
            photo.titulo = _title(new)
        if photo.cuando_usar == _when(old):
            photo.cuando_usar = _when(new)
        named_after_it = photo.clave.startswith(prefix) and photo.clave[len(prefix) :].isdigit()
        if named_after_it and _slug(new) != _slug(old):
            photo.clave = _free_clave(repo, new)
        if (photo.clave, photo.titulo, photo.cuando_usar) != before:
            changed += 1
            session.flush()
    return changed


class ProductPhotos:
    def __init__(self, session: Session, tenant_id: uuid.UUID, storage: StorageProvider) -> None:
        self._session = session
        self._repo = MediaAssetRepository(session, tenant_id)
        self._library = MediaLibrary(session, tenant_id, storage)

    def of(self, product: Product) -> list[MediaAsset]:
        """Every photo of the product, active or not, in order."""
        rows = self._repo.list(MediaAsset.product_id == product.id, MediaAsset.tipo == "imagen")
        return sorted(rows, key=lambda a: (a.orden, a.created_at, a.clave))

    def add(self, product: Product, data: bytes, filename: str) -> MediaAsset:
        photos = self.of(product)
        if len(photos) >= MAX_PHOTOS:
            raise MediaRejected(
                "too_many_photos", f"Un producto puede tener hasta {MAX_PHOTOS} fotos."
            )
        name = " ".join(product.name.split())
        asset = self._library.upload(
            data,
            filename,
            clave=_free_clave(self._repo, name),
            titulo=_title(name),
            cuando_usar=_when(name),
            product_id=product.id,
            orden=(photos[-1].orden + 1) if photos else 0,
        )
        if asset.tipo != "imagen":
            self._library.delete(asset)
            raise MediaRejected("not_an_image", "Sube una foto (JPG, PNG o WEBP).")
        return asset

    def reorder(self, product: Product, ids: Sequence[uuid.UUID]) -> list[MediaAsset]:
        """The photos in the order given; any left out keep their place after them."""
        photos = self.of(product)
        by_id = {p.id: p for p in photos}
        ordered = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
        ordered += [p for p in photos if p.id not in set(ids)]
        for position, photo in enumerate(ordered):
            photo.orden = position
        self._session.flush()
        return ordered

    def remove(self, product: Product, asset_id: uuid.UUID) -> bool:
        photo = next((p for p in self.of(product) if p.id == asset_id), None)
        if photo is None:
            return False
        self._library.delete(photo)
        return True

    def main_photos(self, products: Sequence[Product]) -> dict[uuid.UUID, MediaAsset]:
        """The first active photo of each product, in one query."""
        grouped = self._repo.for_products([p.id for p in products])
        return {
            pid: first
            for pid, assets in grouped.items()
            if (first := next((a for a in assets if a.tipo == "imagen"), None)) is not None
        }
