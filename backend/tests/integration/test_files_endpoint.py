"""`GET /api/v1/files/<token>`: the one public door to files held in Postgres.

Anybody can knock, so the tests are mostly about the knock that should not work:
every way of being refused has to look the same from outside, and nothing a
stranger sends may turn into a 500. Then the behaviour a browser and WhatsApp rely
on: the right type and length, a 304 for a repeat visit, ranges for audio and video.

Runs against the real database inside the `db` fixture's transaction.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.api.deps import get_storage_provider
from app.models.storage import CHUNK_BYTES, StoredObjectChunk
from app.providers.storage import (
    MockStorageProvider,
    PostgresStorageProvider,
    StorageError,
    links,
)
from tests.integration.test_postgres_storage import _bytes, _key, _tenant

SECRET = links.derive_secret("files-endpoint-test-secret-key-long-enough")
BASE = "http://files.test"


def _provider(db: Session, cls: type[PostgresStorageProvider] = PostgresStorageProvider) -> Any:
    return cls(
        lambda: Session(bind=db.get_bind(), join_transaction_mode="create_savepoint"),
        max_object_bytes=200 * 1024 * 1024,
        link_secret=SECRET,
        public_base_url=BASE,
    )


@pytest.fixture
def store(app: FastAPI, db: Session) -> PostgresStorageProvider:
    provider = _provider(db)
    app.dependency_overrides[get_storage_provider] = lambda: provider
    return provider


def _path(store: PostgresStorageProvider, key: str, expires_in: int = 600) -> str:
    return urlparse(store.presigned_url(key, expires_in=expires_in)).path


class TestServingAFile:
    async def test_it_sends_the_file_with_its_type_and_length(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session
    ) -> None:
        tenant = _tenant(db)
        key, data = _key(tenant), _bytes(5000)
        store.put(key, data, content_type="image/png")

        response = await api.get(_path(store, key))

        assert response.status_code == 200
        assert response.content == data
        assert response.headers["content-type"] == "image/png"
        assert response.headers["content-length"] == "5000"
        assert response.headers["etag"] == f'"{hashlib.sha256(data).hexdigest()}"'
        assert response.headers["accept-ranges"] == "bytes"

    async def test_a_file_spanning_several_chunks_arrives_whole(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session
    ) -> None:
        tenant = _tenant(db)
        key, data = _key(tenant), _bytes(2 * CHUNK_BYTES + 77)
        store.put(key, data, content_type="video/mp4")

        response = await api.get(_path(store, key))

        assert response.content == data

    async def test_a_head_request_describes_it_without_sending_it(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session
    ) -> None:
        tenant = _tenant(db)
        key = _key(tenant)
        store.put(key, _bytes(3000), content_type="image/png")

        response = await api.head(_path(store, key))

        assert response.status_code == 200
        assert response.content == b""
        assert response.headers["content-length"] == "3000"
        assert response.headers["content-type"] == "image/png"

    async def test_it_carries_the_app_wide_security_headers(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session
    ) -> None:
        """They come from `SecurityHeadersMiddleware`, not from this route: the
        route adds none of its own, so it cannot weaken them."""
        tenant = _tenant(db)
        key = _key(tenant)
        store.put(key, b"x" * 10, content_type="image/png")

        headers = (await api.get(_path(store, key))).headers

        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["cross-origin-resource-policy"] == "same-site"
        assert "default-src 'none'" in headers["content-security-policy"]
        assert headers["content-disposition"] == "inline"

    async def test_a_pdf_is_shown_inline_and_this_route_adds_no_sandbox_to_it(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session
    ) -> None:
        """A `sandbox` CSP stops Chrome's PDF viewer from rendering, so the one
        route that serves PDFs must not add it on top of the app-wide policy."""
        tenant = _tenant(db)
        key = _key(tenant, "knowledge", "manual.pdf")
        store.put(key, b"%PDF-1.4", content_type="application/pdf")

        headers = (await api.get(_path(store, key))).headers

        assert headers["content-disposition"] == "inline"
        assert "sandbox" not in headers["content-security-policy"]

    async def test_a_type_that_is_not_media_is_a_download_never_a_page(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session
    ) -> None:
        tenant = _tenant(db)
        key = _key(tenant)
        store.put(key, b"<script>alert(1)</script>", content_type="text/html")

        headers = (await api.get(_path(store, key))).headers

        assert headers["content-disposition"].startswith("attachment")
        assert headers["x-content-type-options"] == "nosniff"


class TestCaching:
    async def test_a_receipt_is_not_kept_by_the_browser(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session
    ) -> None:
        tenant = _tenant(db)
        key = _key(tenant, "proofs")
        store.put(key, b"x" * 10, content_type="image/jpeg")

        response = await api.get(_path(store, key))

        assert response.headers["cache-control"] == "private, no-store"

    async def test_a_photo_may_be_reused_until_its_link_would_have_stopped(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session
    ) -> None:
        tenant = _tenant(db)
        key = _key(tenant, "media")
        store.put(key, b"x" * 10, content_type="image/png")

        response = await api.get(_path(store, key, expires_in=600))

        control = response.headers["cache-control"]
        assert control.startswith("private, max-age=")
        assert 0 < int(control.rsplit("=", 1)[1]) <= 600

    @pytest.mark.parametrize(
        "header",
        ['"{etag}"', 'W/"{etag}"', '"nope", "{etag}"', "*"],
        ids=["strong", "weak", "in-a-list", "star"],
    )
    async def test_a_repeat_visit_is_a_304_with_no_body(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session, header: str
    ) -> None:
        tenant = _tenant(db)
        key, data = _key(tenant), _bytes(2000)
        store.put(key, data, content_type="image/png")
        etag = hashlib.sha256(data).hexdigest()

        response = await api.get(
            _path(store, key), headers={"If-None-Match": header.replace("{etag}", etag)}
        )

        assert response.status_code == 304
        assert response.content == b""
        assert response.headers["etag"] == f'"{etag}"'

    async def test_a_different_etag_gets_the_file(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session
    ) -> None:
        tenant = _tenant(db)
        key = _key(tenant)
        store.put(key, b"hello", content_type="image/png")

        response = await api.get(_path(store, key), headers={"If-None-Match": '"stale"'})

        assert response.status_code == 200
        assert response.content == b"hello"


class TestRanges:
    @pytest.fixture
    def video(self, store: PostgresStorageProvider, db: Session) -> tuple[str, bytes]:
        tenant = _tenant(db)
        key, data = _key(tenant), _bytes(2 * CHUNK_BYTES + 500)
        store.put(key, data, content_type="video/mp4")
        return _path(store, key), data

    async def test_the_first_bytes(self, api: AsyncClient, video: tuple[str, bytes]) -> None:
        path, data = video

        response = await api.get(path, headers={"Range": "bytes=0-99"})

        assert response.status_code == 206
        assert response.content == data[:100]
        assert response.headers["content-range"] == f"bytes 0-99/{len(data)}"
        assert response.headers["content-length"] == "100"

    async def test_a_range_across_a_chunk_boundary(
        self, api: AsyncClient, video: tuple[str, bytes]
    ) -> None:
        path, data = video
        start, end = CHUNK_BYTES - 10, CHUNK_BYTES + 10

        response = await api.get(path, headers={"Range": f"bytes={start}-{end}"})

        assert response.status_code == 206
        assert response.content == data[start : end + 1]

    async def test_a_range_spanning_three_chunks(
        self, api: AsyncClient, video: tuple[str, bytes]
    ) -> None:
        path, data = video

        response = await api.get(path, headers={"Range": "bytes=5-"})

        assert response.status_code == 206
        assert response.content == data[5:]

    async def test_the_last_bytes(self, api: AsyncClient, video: tuple[str, bytes]) -> None:
        """Players ask for the end of an MP4 first, to find the index."""
        path, data = video

        response = await api.get(path, headers={"Range": "bytes=-300"})

        assert response.status_code == 206
        assert response.content == data[-300:]

    async def test_an_end_past_the_file_is_clamped(
        self, api: AsyncClient, video: tuple[str, bytes]
    ) -> None:
        path, data = video

        response = await api.get(path, headers={"Range": f"bytes={len(data) - 4}-999999999"})

        assert response.content == data[-4:]
        assert (
            response.headers["content-range"]
            == f"bytes {len(data) - 4}-{len(data) - 1}/{len(data)}"
        )

    async def test_a_start_past_the_file_is_unsatisfiable(
        self, api: AsyncClient, video: tuple[str, bytes]
    ) -> None:
        path, data = video

        response = await api.get(path, headers={"Range": f"bytes={len(data)}-"})

        assert response.status_code == 416
        assert response.headers["content-range"] == f"bytes */{len(data)}"

    @pytest.mark.parametrize(
        "header",
        [
            "bytes=0-1,5-9",
            "bytes=abc-",
            "items=0-5",
            "bytes=9-3",
            "bytes=-",
            f"bytes={'9' * 5000}-",
        ],
        ids=["several", "garbage", "wrong-unit", "backwards", "empty", "huge-number"],
    )
    async def test_a_range_it_cannot_honour_gets_the_whole_file_and_never_an_error(
        self, api: AsyncClient, video: tuple[str, bytes], header: str
    ) -> None:
        path, data = video

        response = await api.get(path, headers={"Range": header})

        assert response.status_code == 200
        assert response.content == data

    async def test_a_range_is_ignored_when_the_validator_no_longer_matches(
        self, api: AsyncClient, video: tuple[str, bytes]
    ) -> None:
        """`If-Range` says "only if it is still the version I have"."""
        path, data = video

        response = await api.get(path, headers={"Range": "bytes=0-9", "If-Range": '"stale"'})

        assert response.status_code == 200
        assert response.content == data


class TestEveryRefusalLooksTheSame:
    async def _refusals(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session
    ) -> dict[str, tuple[int, bytes]]:
        tenant = _tenant(db)
        live, deleted = _key(tenant), _key(tenant)
        store.put(live, b"secret", content_type="image/png")
        store.put(deleted, b"gone", content_type="image/png")
        deleted_path = _path(store, deleted)
        store.delete(deleted)

        good = _path(store, live)
        token = good.rsplit("/", 1)[1]
        expired = links.sign(live, 600, secret=SECRET, now=time.time() - 10_000)[0]
        elsewhere = links.sign(live, 600, secret=links.derive_secret("another-key" * 4))[0]
        malformed_key = links.sign("../../etc/passwd", 600, secret=SECRET)[0]
        prefix = "/api/v1/files/"
        cases = {
            "deleted": deleted_path,
            "expired": f"{prefix}{expired}",
            "wrong-signing-key": f"{prefix}{elsewhere}",
            "signed-but-not-a-key": f"{prefix}{malformed_key}",
            "tampered": f"{prefix}{token[:-1]}{'A' if token[-1] != 'A' else 'B'}",
            "garbage": f"{prefix}not-a-token",
            "non-ascii": f"{prefix}v1.%C3%A9.%C3%A9",
            "huge": f"{prefix}{'A' * 4000}",
        }
        return {
            name: (r.status_code, r.content)
            for name, r in [(n, await api.get(p)) for n, p in cases.items()]
        }

    async def test_they_are_all_the_same_404(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session
    ) -> None:
        results = await self._refusals(api, store, db)

        assert {status for status, _ in results.values()} == {404}
        assert len({body for _, body in results.values()}) == 1

    async def test_the_s3_store_has_no_links_to_serve(
        self, api: AsyncClient, app: FastAPI, store: PostgresStorageProvider, db: Session
    ) -> None:
        tenant = _tenant(db)
        key = _key(tenant)
        store.put(key, b"x", content_type="image/png")
        path = _path(store, key)
        app.dependency_overrides[get_storage_provider] = MockStorageProvider

        assert (await api.get(path)).status_code == 404


class TestWhenStorageFails:
    async def test_the_catalogue_being_unreachable_is_a_503_not_a_500(
        self, api: AsyncClient, app: FastAPI, db: Session
    ) -> None:
        class Broken(PostgresStorageProvider):
            def stat(self, key: str) -> Any:
                raise StorageError("the database went away")

        broken = _provider(db, Broken)
        app.dependency_overrides[get_storage_provider] = lambda: broken
        key = _key(_tenant(db))

        response = await api.get(_path(broken, key))

        assert response.status_code == 503
        assert response.json()["error"] == "storage_unavailable"

    async def test_a_damaged_file_aborts_the_download_instead_of_ending_cleanly(
        self, api: AsyncClient, store: PostgresStorageProvider, db: Session
    ) -> None:
        """The headers are out by then, so all that is left to say "this is not the
        file" is a connection that does not complete."""
        tenant = _tenant(db)
        key = _key(tenant)
        store.put(key, _bytes(CHUNK_BYTES + 10), content_type="image/png")
        db.execute(
            StoredObjectChunk.__table__.update()
            .where(StoredObjectChunk.key == key, StoredObjectChunk.seq == 0)
            .values(data=b"\x00" * CHUNK_BYTES)
        )

        with pytest.raises(StorageError, match="corrupt"):
            await api.get(_path(store, key))
