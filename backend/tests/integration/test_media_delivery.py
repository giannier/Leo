"""Uploading library files to the provider, reusing the id, and the link fallback
(Feature 004, TASK-419, clarification C-17).

The provider is simulated and counts its uploads, so each scenario asserts on
what the customer-facing path actually costs: the first send uploads, the second
reuses, an expired id uploads again, and a failed upload still sends something.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.crypto import encrypt_secret
from app.models.media import MediaAsset
from app.models.tenant import Tenant
from app.models.whatsapp import WhatsAppNumber
from app.providers.storage.base import ObjectNotFoundError
from app.providers.storage.mock import MockStorageProvider
from app.providers.whatsapp.base import (
    NumberCredentials,
    UploadedMedia,
    WhatsAppAuthError,
    WhatsAppTransientError,
)
from app.repositories.media import MediaAssetRepository
from app.services.media_delivery import (
    LINK_LIFETIME_SECONDS,
    MediaDelivery,
    MediaUnavailable,
    refresh_provider_ids,
)
from app.workers.media import refresh_media_provider_ids

SENDER = "+51912345678"
CREDENTIALS = NumberCredentials(
    provider="ycloud",
    values={"waba_id": "102290129340398", "phone_number_id": "1122334455", "api_key": "yc_key_123"},
)


class SimulatedProvider:
    """Only the upload side of `WhatsAppProvider`; it counts and can be told to fail."""

    def __init__(self, *, failing: Exception | None = None) -> None:
        self.failing = failing
        self.uploads: list[dict[str, Any]] = []

    async def upload_media(
        self,
        credentials: NumberCredentials,
        *,
        sender: str,
        data: bytes,
        mime: str,
        filename: str,
    ) -> UploadedMedia:
        if self.failing is not None:
            raise self.failing
        self.uploads.append({"sender": sender, "data": data, "mime": mime, "filename": filename})
        return UploadedMedia(
            media_id=f"media-{len(self.uploads)}",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )


@pytest.fixture
def created_assets(db: Session) -> Iterator[list[uuid.UUID]]:
    """The assets a test committed, removed afterwards: the refresh job reads
    across tenants, so rows left behind would colour the next run."""
    ids: list[uuid.UUID] = []
    yield ids
    db.rollback()
    db.execute(delete(MediaAsset).where(MediaAsset.id.in_(ids)))
    db.commit()


def _tenant(db: Session, *, number: bool = True) -> Tenant:
    tenant = Tenant(name=f"Academia {uuid.uuid4().hex[:8]}")
    db.add(tenant)
    db.flush()
    if number:
        db.add(
            WhatsAppNumber(
                tenant_id=tenant.id,
                phone_e164=f"+5191{uuid.uuid4().int % 10**7:07d}",
                display_name="Academia",
                provider="ycloud",
                status="connected",
                credentials_enc=encrypt_secret(json.dumps(CREDENTIALS.values)),
            )
        )
        db.flush()
    return tenant


def _asset(
    db: Session,
    tenant: Tenant,
    storage: MockStorageProvider,
    created: list[uuid.UUID],
    *,
    clave: str = "flyer",
    tipo: str = "imagen",
    media_id: str | None = None,
    expires_in: timedelta | None = None,
    with_file: bool = True,
) -> MediaAsset:
    key = f"media/{uuid.uuid4()}.bin"
    if with_file:
        storage.put(key, b"file-bytes", content_type="image/png")
    asset = MediaAssetRepository(db, tenant.id).add(
        clave=clave,
        tipo=tipo,
        titulo="Flyer",
        cuando_usar="Cuando pidan el flyer",
        storage_key=key,
        filename=f"{clave}.png",
        mime_type="image/png",
        size_bytes=10,
        provider_media_id=media_id,
        provider_media_expires_at=datetime.now(UTC) + expires_in if expires_in else None,
    )
    created.append(asset.id)
    return asset


def _delivery(
    db: Session, tenant: Tenant, provider: SimulatedProvider, storage: MockStorageProvider
) -> MediaDelivery:
    return MediaDelivery(
        db,
        tenant.id,
        provider=provider,  # type: ignore[arg-type]
        storage=storage,
        credentials=CREDENTIALS,
        sender=SENDER,
    )


class TestSendingALibraryFile:
    async def test_the_first_time_uploads_and_remembers_the_id(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        tenant, storage, provider = _tenant(db), MockStorageProvider(), SimulatedProvider()
        asset = _asset(db, tenant, storage, created_assets)

        reference = await _delivery(db, tenant, provider, storage).reference_for(asset)

        assert (reference.kind, reference.media_id, reference.link) == ("image", "media-1", None)
        assert reference.filename == "flyer.png"
        assert provider.uploads == [
            {"sender": SENDER, "data": b"file-bytes", "mime": "image/png", "filename": "flyer.png"}
        ]
        assert asset.provider_media_id == "media-1"
        assert asset.provider_media_expires_at is not None
        assert asset.provider_media_expires_at > datetime.now(UTC) + timedelta(days=29)

    async def test_the_second_time_reuses_the_stored_id(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        tenant, storage, provider = _tenant(db), MockStorageProvider(), SimulatedProvider()
        asset = _asset(db, tenant, storage, created_assets)
        delivery = _delivery(db, tenant, provider, storage)

        first = await delivery.reference_for(asset)
        second = await delivery.reference_for(asset)

        assert first.media_id == second.media_id == "media-1"
        assert len(provider.uploads) == 1

    async def test_an_expired_id_is_uploaded_again(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        tenant, storage, provider = _tenant(db), MockStorageProvider(), SimulatedProvider()
        asset = _asset(
            db, tenant, storage, created_assets, media_id="old", expires_in=-timedelta(days=1)
        )

        reference = await _delivery(db, tenant, provider, storage).reference_for(asset)

        assert reference.media_id == "media-1"
        assert asset.provider_media_id == "media-1"

    async def test_an_id_about_to_lapse_is_not_trusted(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        """Accepted now and gone by the time WhatsApp fetches it."""
        tenant, storage, provider = _tenant(db), MockStorageProvider(), SimulatedProvider()
        asset = _asset(
            db, tenant, storage, created_assets, media_id="old", expires_in=timedelta(minutes=20)
        )

        reference = await _delivery(db, tenant, provider, storage).reference_for(asset)

        assert reference.media_id == "media-1"

    async def test_an_id_without_an_expiry_is_not_trusted(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        tenant, storage, provider = _tenant(db), MockStorageProvider(), SimulatedProvider()
        asset = _asset(db, tenant, storage, created_assets, media_id="mystery")

        reference = await _delivery(db, tenant, provider, storage).reference_for(asset)

        assert reference.media_id == "media-1"

    @pytest.mark.parametrize(
        "failure",
        [WhatsAppTransientError("down"), WhatsAppAuthError("revoked")],
    )
    async def test_a_failed_upload_falls_back_to_a_one_hour_link(
        self, db: Session, created_assets: list[uuid.UUID], failure: Exception
    ) -> None:
        tenant, storage = _tenant(db), MockStorageProvider()
        provider = SimulatedProvider(failing=failure)
        asset = _asset(db, tenant, storage, created_assets, tipo="documento")

        reference = await _delivery(db, tenant, provider, storage).reference_for(asset)

        assert reference.kind == "document"
        assert reference.media_id is None
        assert reference.link is not None
        assert f"expires_in={LINK_LIFETIME_SECONDS}" in reference.link
        assert LINK_LIFETIME_SECONDS == 3600
        # Nothing was remembered: the next send tries the upload again.
        assert asset.provider_media_id is None

    async def test_a_file_missing_from_storage_cannot_be_sent_at_all(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        tenant, storage, provider = _tenant(db), MockStorageProvider(), SimulatedProvider()
        asset = _asset(db, tenant, storage, created_assets, with_file=False)

        with pytest.raises(MediaUnavailable):
            await _delivery(db, tenant, provider, storage).reference_for(asset)

    async def test_a_link_that_cannot_be_minted_is_unavailable(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        class NoLinks(MockStorageProvider):
            def presigned_url(self, key: str, *, expires_in: int = 900) -> str:
                raise ObjectNotFoundError(key)

        tenant, storage = _tenant(db), NoLinks()
        provider = SimulatedProvider(failing=WhatsAppTransientError("down"))
        asset = _asset(db, tenant, storage, created_assets)

        with pytest.raises(MediaUnavailable):
            await _delivery(db, tenant, provider, storage).reference_for(asset)

    @pytest.mark.parametrize(
        ("tipo", "kind"),
        [("imagen", "image"), ("documento", "document"), ("video", "video"), ("audio", "audio")],
    )
    async def test_the_kind_follows_the_library_type(
        self, db: Session, created_assets: list[uuid.UUID], tipo: str, kind: str
    ) -> None:
        tenant, storage, provider = _tenant(db), MockStorageProvider(), SimulatedProvider()
        asset = _asset(db, tenant, storage, created_assets, tipo=tipo, clave=f"f_{tipo}")

        reference = await _delivery(db, tenant, provider, storage).reference_for(asset)

        assert reference.kind == kind


class TestTheRefreshJob:
    async def test_it_uploads_again_only_what_lapses_soon(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        tenant, storage, provider = _tenant(db), MockStorageProvider(), SimulatedProvider()
        soon = _asset(
            db,
            tenant,
            storage,
            created_assets,
            clave="pronto",
            media_id="a",
            expires_in=timedelta(hours=20),
        )
        later = _asset(
            db,
            tenant,
            storage,
            created_assets,
            clave="luego",
            media_id="b",
            expires_in=timedelta(days=20),
        )
        never_uploaded = _asset(db, tenant, storage, created_assets, clave="nunca")
        db.commit()

        outcome = await refresh_provider_ids(db, provider=provider, storage=storage)  # type: ignore[arg-type]

        assert (outcome.considered, outcome.refreshed, outcome.failed) == (1, 1, 0)
        assert soon.provider_media_id == "media-1"
        assert later.provider_media_id == "b"
        assert never_uploaded.provider_media_id is None

    async def test_one_failing_upload_does_not_stop_the_others(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        tenant, storage = _tenant(db), MockStorageProvider()
        provider = SimulatedProvider()
        good = _asset(
            db,
            tenant,
            storage,
            created_assets,
            clave="bueno",
            media_id="a",
            expires_in=timedelta(hours=5),
        )
        broken = _asset(
            db,
            tenant,
            storage,
            created_assets,
            clave="roto",
            media_id="b",
            expires_in=timedelta(hours=5),
            with_file=False,
        )
        db.commit()

        outcome = await refresh_provider_ids(db, provider=provider, storage=storage)  # type: ignore[arg-type]

        assert (outcome.considered, outcome.refreshed, outcome.failed) == (2, 1, 1)
        assert good.provider_media_id == "media-1"
        assert broken.provider_media_id == "b"

    async def test_a_provider_refusal_keeps_the_old_id_and_counts_as_failed(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        tenant, storage = _tenant(db), MockStorageProvider()
        provider = SimulatedProvider(failing=WhatsAppTransientError("down"))
        asset = _asset(
            db, tenant, storage, created_assets, media_id="a", expires_in=timedelta(hours=5)
        )
        db.commit()

        outcome = await refresh_provider_ids(db, provider=provider, storage=storage)  # type: ignore[arg-type]

        assert (outcome.refreshed, outcome.failed) == (0, 1)
        assert asset.provider_media_id == "a"

    async def test_a_tenant_without_a_number_is_skipped_not_fatal(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        storage, provider = MockStorageProvider(), SimulatedProvider()
        orphan = _tenant(db, number=False)
        with_number = _tenant(db)
        stuck = _asset(
            db, orphan, storage, created_assets, media_id="a", expires_in=timedelta(hours=5)
        )
        fine = _asset(
            db, with_number, storage, created_assets, media_id="b", expires_in=timedelta(hours=5)
        )
        db.commit()

        outcome = await refresh_provider_ids(db, provider=provider, storage=storage)  # type: ignore[arg-type]

        assert (outcome.considered, outcome.refreshed, outcome.failed) == (2, 1, 1)
        assert stuck.provider_media_id == "a"
        assert fine.provider_media_id == "media-1"

    async def test_each_tenant_is_uploaded_with_its_own_number(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        storage, provider = MockStorageProvider(), SimulatedProvider()
        first, second = _tenant(db), _tenant(db)
        _asset(db, first, storage, created_assets, media_id="a", expires_in=timedelta(hours=5))
        _asset(db, second, storage, created_assets, media_id="b", expires_in=timedelta(hours=5))
        db.commit()

        await refresh_provider_ids(db, provider=provider, storage=storage)  # type: ignore[arg-type]

        senders = {upload["sender"] for upload in provider.uploads}
        numbers = {
            number.phone_e164
            for number in db.query(WhatsAppNumber).filter(
                WhatsAppNumber.tenant_id.in_([first.id, second.id])
            )
        }
        assert senders == numbers
        assert len(senders) == 2

    async def test_the_arq_entry_point_runs_it_with_the_shared_resources(
        self, db: Session, created_assets: list[uuid.UUID]
    ) -> None:
        tenant, storage, provider = _tenant(db), MockStorageProvider(), SimulatedProvider()
        _asset(db, tenant, storage, created_assets, media_id="a", expires_in=timedelta(hours=5))
        db.commit()

        class _Factory:
            def __call__(self) -> Any:
                return _Scope()

        class _Scope:
            def __enter__(self) -> Session:
                return db

            def __exit__(self, *exc: object) -> None:
                return None

        result = await refresh_media_provider_ids(
            {"session_factory": _Factory(), "whatsapp": provider, "storage": storage}
        )

        assert result == "considered=1 refreshed=1 failed=0"
