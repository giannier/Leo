"""The Postgres object store, against a real database.

`MockStorageProvider` proves the port; the S3 adapter's translation is proven
against a stubbed client. What neither can prove is the part that is new here:
that a file survives being cut into chunks and put back together, that nothing is
left behind when it is replaced or deleted, that a damaged file is an error and
not a broken image, and that the limits really limit.

Every test runs inside the `db` fixture's transaction, which is rolled back, so
nothing durable is written. The provider is given sessions on that same
connection (`create_savepoint`), so its own commits stay inside it.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.storage import CHUNK_BYTES, StoredObject, StoredObjectChunk
from app.models.tenant import Tenant
from app.providers.storage import (
    MockStorageProvider,
    ObjectNotFoundError,
    ObjectTooLargeError,
    PostgresStorageProvider,
    QuotaExceededError,
    StorageError,
    links,
)

PNG = "image/png"
MB = 1024 * 1024


def _tenant(db: Session) -> Tenant:
    tenant = Tenant(
        name=f"Tienda {uuid.uuid4().hex[:8]}",
        status="trial",
        plan_id="trial",
        trial_ends_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(tenant)
    db.flush()
    return tenant


def _key(tenant: Tenant, kind: str = "media", name: str | None = None) -> str:
    return f"tenants/{tenant.id}/{kind}/{name or uuid.uuid4().hex}.png"


def _store(
    db: Session,
    *,
    max_object_bytes: int = 200 * MB,
    quota: int = 0,
    link_secret: bytes | None = None,
    public_base_url: str | None = None,
) -> PostgresStorageProvider:
    return PostgresStorageProvider(
        lambda: Session(bind=db.get_bind(), join_transaction_mode="create_savepoint"),
        max_object_bytes=max_object_bytes,
        tenant_quota_bytes=quota,
        link_secret=link_secret,
        public_base_url=public_base_url,
    )


def _bytes(size: int) -> bytes:
    """Deterministic, and not periodic at the chunk size, so a chunk put in the
    wrong place would change the result."""
    pattern = bytes(range(251))
    return (pattern * (size // len(pattern) + 1))[:size]


def _chunk_rows(db: Session, key: str) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(StoredObjectChunk).where(StoredObjectChunk.key == key)
        )
        or 0
    )


class TestRoundTrip:
    def test_a_small_file_comes_back_as_it_went_in(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db)
        key = _key(tenant)

        assert store.put(key, b"hello", content_type=PNG) == key
        assert store.get(key) == b"hello"

    def test_the_catalogue_describes_the_file(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db)
        key, data = _key(tenant), _bytes(1000)
        store.put(key, data, content_type=PNG)

        info = store.stat(key)

        assert info is not None
        assert info.tenant_id == tenant.id
        assert (info.content_type, info.size_bytes, info.chunk_count) == (PNG, 1000, 1)
        assert info.sha256 == hashlib.sha256(data).hexdigest()

    def test_a_large_file_is_cut_into_chunks_and_put_back_together(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db)
        key, data = _key(tenant), _bytes(2 * CHUNK_BYTES + 1234)

        store.put(key, data, content_type="application/pdf")

        assert _chunk_rows(db, key) == 3
        assert store.get(key) == data

    @pytest.mark.parametrize(
        ("size", "chunks"),
        [(CHUNK_BYTES - 1, 1), (CHUNK_BYTES, 1), (CHUNK_BYTES + 1, 2), (2 * CHUNK_BYTES, 2)],
        ids=["just-under", "exactly-one", "just-over", "exactly-two"],
    )
    def test_the_chunk_boundaries_do_not_lose_or_add_a_byte(
        self, db: Session, size: int, chunks: int
    ) -> None:
        tenant, store = _tenant(db), _store(db)
        key, data = _key(tenant), _bytes(size)

        store.put(key, data, content_type=PNG)

        info = store.stat(key)
        assert info is not None and info.chunk_count == chunks
        assert store.get(key) == data

    def test_an_empty_file_is_a_file(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db)
        key = _key(tenant)

        store.put(key, b"", content_type=PNG)

        assert store.get(key) == b""
        assert _chunk_rows(db, key) == 0

    def test_a_stream_never_holds_more_than_one_chunk(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db)
        key, data = _key(tenant), _bytes(2 * CHUNK_BYTES + 5)
        store.put(key, data, content_type=PNG)

        pieces = list(store.stream(key))

        assert [len(piece) for piece in pieces] == [CHUNK_BYTES, CHUNK_BYTES, 5]
        assert b"".join(pieces) == data

    def test_replacing_a_key_leaves_no_old_chunks_behind(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db)
        key = _key(tenant)
        store.put(key, _bytes(3 * CHUNK_BYTES), content_type=PNG)

        store.put(key, b"smaller", content_type=PNG)

        assert store.get(key) == b"smaller"
        assert _chunk_rows(db, key) == 1


class TestMissingAndDeleted:
    def test_reading_a_missing_key_is_a_not_found(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db)

        with pytest.raises(ObjectNotFoundError):
            store.get(_key(tenant))

    def test_a_missing_key_does_not_exist(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db)

        assert store.exists(_key(tenant)) is False
        assert store.stat(_key(tenant)) is None

    def test_deleting_reports_whether_there_was_anything_and_takes_the_chunks(
        self, db: Session
    ) -> None:
        tenant, store = _tenant(db), _store(db)
        key = _key(tenant)
        store.put(key, _bytes(2 * CHUNK_BYTES), content_type=PNG)

        assert store.delete(key) is True
        assert store.delete(key) is False
        assert store.exists(key) is False
        assert _chunk_rows(db, key) == 0

    def test_the_catalogue_answers_without_touching_the_payload(self, db: Session) -> None:
        """If `exists` and `stat` read the chunks, losing them would break both."""
        tenant, store = _tenant(db), _store(db)
        key = _key(tenant)
        store.put(key, _bytes(CHUNK_BYTES + 1), content_type=PNG)
        db.execute(StoredObjectChunk.__table__.delete().where(StoredObjectChunk.key == key))

        assert store.exists(key) is True
        assert store.stat(key) is not None
        with pytest.raises(ObjectNotFoundError):
            store.get(key)


class TestIntegrity:
    def test_a_damaged_chunk_is_an_error_not_a_broken_file(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db)
        key = _key(tenant)
        store.put(key, _bytes(2 * CHUNK_BYTES), content_type=PNG)
        db.execute(
            update(StoredObjectChunk)
            .where(StoredObjectChunk.key == key, StoredObjectChunk.seq == 1)
            .values(data=b"\x00" * CHUNK_BYTES)
        )

        with pytest.raises(StorageError, match="corrupt"):
            store.get(key)

    def test_a_streamed_file_is_checked_when_it_ends(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db)
        key = _key(tenant)
        store.put(key, _bytes(CHUNK_BYTES + 10), content_type=PNG)
        db.execute(
            update(StoredObjectChunk)
            .where(StoredObjectChunk.key == key, StoredObjectChunk.seq == 0)
            .values(data=b"\x01" * CHUNK_BYTES)
        )

        with pytest.raises(StorageError, match="corrupt"):
            list(store.stream(key))


class TestLimits:
    def test_a_file_over_the_object_cap_is_refused_and_writes_nothing(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db, max_object_bytes=10)
        key = _key(tenant)

        with pytest.raises(ObjectTooLargeError):
            store.put(key, b"x" * 11, content_type=PNG)

        assert store.exists(key) is False

    def test_a_file_at_the_cap_is_accepted(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db, max_object_bytes=10)

        store.put(_key(tenant), b"x" * 10, content_type=PNG)

    def test_the_quota_refuses_the_file_that_would_pass_it_and_writes_nothing(
        self, db: Session
    ) -> None:
        tenant, store = _tenant(db), _store(db, quota=100)
        store.put(_key(tenant), b"x" * 60, content_type=PNG)
        second = _key(tenant)

        with pytest.raises(QuotaExceededError):
            store.put(second, b"y" * 41, content_type=PNG)

        assert store.exists(second) is False
        assert store.usage(tenant.id) == 60

    def test_the_quota_is_exact_at_the_limit(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db, quota=100)
        store.put(_key(tenant), b"x" * 60, content_type=PNG)

        store.put(_key(tenant), b"y" * 40, content_type=PNG)

        assert store.usage(tenant.id) == 100

    def test_replacing_a_file_does_not_count_it_twice(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db, quota=100)
        key = _key(tenant)
        store.put(key, b"x" * 90, content_type=PNG)

        store.put(key, b"y" * 95, content_type=PNG)

        assert store.usage(tenant.id) == 95

    def test_one_business_filling_its_quota_does_not_block_another(self, db: Session) -> None:
        full, other, store = _tenant(db), _tenant(db), _store(db, quota=50)
        store.put(_key(full), b"x" * 50, content_type=PNG)

        store.put(_key(other), b"y" * 50, content_type=PNG)

        assert store.usage(full.id) == 50
        assert store.usage(other.id) == 50

    def test_no_quota_means_no_limit(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db, quota=0)

        store.put(_key(tenant), b"x" * (3 * CHUNK_BYTES), content_type=PNG)

        assert store.usage(tenant.id) == 3 * CHUNK_BYTES


class TestKeysAndTypes:
    @pytest.mark.parametrize(
        "template",
        [
            "../etc/passwd",
            "tenants/not-a-uuid/media/a.png",
            "tenants/{id}/media/../a.png",
            "tenants/{id}/Media/a.png",
            "tenants/{id}/media/",
            "tenants/{id}/media/a/b.png",
            "{id}/media/a.png",
            "",
        ],
        ids=[
            "path-escape",
            "bad-tenant",
            "dot-dot",
            "uppercase-kind",
            "no-name",
            "nested",
            "no-prefix",
            "empty",
        ],
    )
    def test_a_key_outside_the_tenant_layout_is_refused(self, db: Session, template: str) -> None:
        tenant, store = _tenant(db), _store(db)
        key = template.replace("{id}", str(tenant.id))

        with pytest.raises(StorageError, match="not a valid storage key"):
            store.put(key, b"x", content_type=PNG)

    @pytest.mark.parametrize(
        "content_type",
        ["", "image", "text/html; charset=utf-8", "image/png\r\nX-Evil: 1", "a b/c"],
        ids=["empty", "no-subtype", "parameters", "header-injection", "space"],
    )
    def test_a_malformed_content_type_is_refused(self, db: Session, content_type: str) -> None:
        tenant, store = _tenant(db), _store(db)

        with pytest.raises(StorageError, match="content type"):
            store.put(_key(tenant), b"x", content_type=content_type)

    def test_a_file_for_a_business_that_does_not_exist_is_refused(self, db: Session) -> None:
        store = _store(db)
        key = f"tenants/{uuid.uuid4()}/media/{uuid.uuid4().hex}.png"

        with pytest.raises(StorageError):
            store.put(key, b"x", content_type=PNG)

    def test_the_owner_comes_from_the_key(self, db: Session) -> None:
        mine, theirs, store = _tenant(db), _tenant(db), _store(db)
        key = _key(mine)
        store.put(key, b"x", content_type=PNG)

        owner = db.scalar(select(StoredObject.tenant_id).where(StoredObject.key == key))

        assert owner == mine.id
        assert owner != theirs.id


SECRET = links.derive_secret("postgres-storage-test-secret-key-long-enough")


class TestLinks:
    def test_a_link_is_built_on_the_public_address(self, db: Session) -> None:
        tenant = _tenant(db)
        store = _store(db, link_secret=SECRET, public_base_url="https://leo.example.pe/")
        key = _key(tenant)

        url = store.presigned_url(key, expires_in=600)

        assert url.startswith("https://leo.example.pe/api/v1/files/v1.")
        assert "//api" not in url

    def test_the_link_names_the_file_it_was_made_for(self, db: Session) -> None:
        tenant = _tenant(db)
        store = _store(db, link_secret=SECRET, public_base_url="http://x")
        key = _key(tenant)
        token = store.presigned_url(key).rsplit("/", 1)[1]

        verified = store.resolve_link(token)

        assert verified is not None and verified.key == key

    def test_a_store_without_a_key_or_address_cannot_make_links(self, db: Session) -> None:
        tenant = _tenant(db)

        for store in (
            _store(db),
            _store(db, link_secret=SECRET),
            _store(db, public_base_url="http://x"),
        ):
            with pytest.raises(StorageError, match="signing key or public URL"):
                store.presigned_url(_key(tenant))

    def test_a_link_is_not_made_for_a_malformed_key_or_an_absurd_lifetime(
        self, db: Session
    ) -> None:
        tenant = _tenant(db)
        store = _store(db, link_secret=SECRET, public_base_url="http://x")

        with pytest.raises(StorageError, match="not a valid storage key"):
            store.presigned_url("../../etc/passwd")
        with pytest.raises(StorageError):
            store.presigned_url(_key(tenant), expires_in=0)

    def test_a_token_is_worthless_to_a_store_with_a_different_key(self, db: Session) -> None:
        tenant = _tenant(db)
        mine = _store(db, link_secret=SECRET, public_base_url="http://x")
        theirs = _store(db, link_secret=links.derive_secret("x" * 40), public_base_url="http://x")
        token = mine.presigned_url(_key(tenant)).rsplit("/", 1)[1]

        assert theirs.resolve_link(token) is None
        assert _store(db).resolve_link(token) is None

    def test_a_genuine_signature_over_a_malformed_key_still_resolves_to_nothing(
        self, db: Session
    ) -> None:
        store = _store(db, link_secret=SECRET, public_base_url="http://x")
        token, _ = links.sign("../../etc/passwd", 600, secret=SECRET)

        assert store.resolve_link(token) is None


class TestRanges:
    @pytest.mark.parametrize(
        ("start", "end"),
        [
            (0, 0),
            (0, 9),
            (CHUNK_BYTES - 1, CHUNK_BYTES - 1),
            (CHUNK_BYTES - 1, CHUNK_BYTES),
            (CHUNK_BYTES, CHUNK_BYTES),
            (CHUNK_BYTES - 5, 2 * CHUNK_BYTES + 5),
            (2 * CHUNK_BYTES, 2 * CHUNK_BYTES + 99),
            (0, 2 * CHUNK_BYTES + 99),
        ],
        ids=[
            "first-byte",
            "first-ten",
            "last-of-chunk-0",
            "across-0-1",
            "first-of-chunk-1",
            "across-three",
            "last-chunk",
            "everything",
        ],
    )
    def test_a_range_returns_exactly_those_bytes(self, db: Session, start: int, end: int) -> None:
        tenant, store = _tenant(db), _store(db)
        key, data = _key(tenant), _bytes(2 * CHUNK_BYTES + 100)
        store.put(key, data, content_type="video/mp4")

        assert b"".join(store.stream_range(key, start, end)) == data[start : end + 1]

    def test_a_range_reads_only_the_chunks_it_overlaps(self, db: Session) -> None:
        """Seeking to the end of a long file must not read the whole of it."""
        tenant, store = _tenant(db), _store(db)
        key, data = _key(tenant), _bytes(3 * CHUNK_BYTES)
        store.put(key, data, content_type="video/mp4")
        # Without chunks 0 and 1 the file cannot be read whole, but its last chunk can.
        db.execute(
            StoredObjectChunk.__table__.delete().where(
                StoredObjectChunk.key == key, StoredObjectChunk.seq < 2
            )
        )

        tail = b"".join(store.stream_range(key, 2 * CHUNK_BYTES + 5, 3 * CHUNK_BYTES - 1))

        assert tail == data[2 * CHUNK_BYTES + 5 :]
        with pytest.raises(ObjectNotFoundError):
            store.get(key)

    @pytest.mark.parametrize(
        ("start", "end"),
        [(-1, 5), (5, 4), (0, 100), (100, 200)],
        ids=["negative", "backwards", "past-end", "start-past-end"],
    )
    def test_a_range_outside_the_file_is_refused(self, db: Session, start: int, end: int) -> None:
        tenant, store = _tenant(db), _store(db)
        key = _key(tenant)
        store.put(key, b"x" * 100, content_type=PNG)

        with pytest.raises(StorageError, match="outside"):
            list(store.stream_range(key, start, end))

    def test_a_range_of_a_missing_file_is_a_not_found(self, db: Session) -> None:
        tenant, store = _tenant(db), _store(db)

        with pytest.raises(ObjectNotFoundError):
            list(store.stream_range(_key(tenant), 0, 1))


class TestMigratingToAndFromPostgres:
    """The way out, against the real store: `scripts.storage_migrate` both ways."""

    def test_postgres_to_another_store_keeps_every_byte(self, db: Session) -> None:
        from scripts.storage_migrate import migrate

        tenant, source, destination = _tenant(db), _store(db), MockStorageProvider()
        big = _key(tenant, "knowledge", "manual.pdf")
        small = _key(tenant)
        source.put(big, _bytes(2 * CHUNK_BYTES + 321), content_type="application/pdf")
        source.put(small, b"photo", content_type=PNG)

        report = migrate(
            [(big, "application/pdf"), (small, PNG)],
            source,
            destination,
            apply=True,
            log=lambda _: None,
        )

        assert (report.copied, report.failed) == (2, [])
        assert destination.get(big) == _bytes(2 * CHUNK_BYTES + 321)
        assert destination.objects[big][1] == "application/pdf"

    def test_another_store_into_postgres_and_the_original_can_then_go(self, db: Session) -> None:
        from scripts.storage_migrate import migrate

        tenant, destination, source = _tenant(db), _store(db), MockStorageProvider()
        key = _key(tenant)
        source.put(key, _bytes(CHUNK_BYTES + 9), content_type=PNG)

        report = migrate(
            [(key, PNG)], source, destination, apply=True, delete_source=True, log=lambda _: None
        )

        assert (report.copied, report.deleted_from_source) == (1, 1)
        assert destination.get(key) == _bytes(CHUNK_BYTES + 9)
        assert source.exists(key) is False
