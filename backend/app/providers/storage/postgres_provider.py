"""Object storage in Postgres: the same port as S3, with no extra service to run.

Files are written and read in chunks (`app.models.storage`), each call in its
own short transaction. That is the same contract the S3 adapter has - a `put`
is durable when it returns, a caller that fails afterwards cleans up after
itself - so nothing above `providers/` changes.

What keeps a database from being a bad place for files:

- **Chunks, not one big value.** A file is stored as rows of at most
  `CHUNK_BYTES`; a read holds one chunk, never the whole file, while it streams.
- **The catalogue is separate from the bytes.** `exists`, `stat` and quota read
  `stored_objects` only and never touch the payload.
- **A cap per object and an optional quota per business**, so growth is a number
  somebody chose, not an accident.
- **Checked on the way out.** The SHA-256 written with the file is verified when
  it is read back, so a truncated or mixed-up file is an error, not a silent
  broken image.
- **The key is the boundary.** Only `tenants/<uuid>/<kind>/<name>` keys are
  accepted, and the owner is read from the key, so a malformed key can neither
  escape its prefix nor be attributed to nobody.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.storage import CHUNK_BYTES, StoredObject, StoredObjectChunk
from app.providers.storage import links
from app.providers.storage.base import (
    ObjectNotFoundError,
    ObjectTooLargeError,
    QuotaExceededError,
    StorageError,
)

_KEY = re.compile(r"^tenants/(?P<tenant>[0-9a-fA-F-]{36})/[a-z_]+/[A-Za-z0-9._-]+$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$", re.IGNORECASE)


@dataclass(frozen=True)
class ObjectInfo:
    """What the catalogue knows about a file. Enough to answer an HTTP request
    (type, length, ETag) without reading a single byte of it."""

    key: str
    tenant_id: uuid.UUID
    content_type: str
    size_bytes: int
    sha256: str
    chunk_count: int


def tenant_of(key: str) -> uuid.UUID:
    """The business a key belongs to, or `StorageError` when it is not a valid key."""
    match = _KEY.fullmatch(key)
    if match is None:
        raise StorageError(f"not a valid storage key: {key!r}")
    try:
        return uuid.UUID(match.group("tenant"))
    except ValueError as exc:
        raise StorageError(f"not a valid storage key: {key!r}") from exc


class PostgresStorageProvider:
    """Objects in `stored_objects` / `stored_object_chunks`."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        max_object_bytes: int,
        tenant_quota_bytes: int = 0,
        link_secret: bytes | None = None,
        public_base_url: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._max_object_bytes = max_object_bytes
        # 0 means "no quota": the limit is opt-in, so turning this adapter on
        # does not start refusing files anybody could upload yesterday.
        self._tenant_quota_bytes = tenant_quota_bytes
        # Without both, the store still reads and writes; it just cannot mint
        # links, and `presigned_url` says so instead of returning a dead URL.
        self._link_secret = link_secret
        self._public_base_url = public_base_url.rstrip("/") if public_base_url else None

    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        tenant_id = tenant_of(key)
        if not _CONTENT_TYPE.fullmatch(content_type):
            raise StorageError(f"not a valid content type: {content_type!r}")
        size = len(data)
        if size > self._max_object_bytes:
            raise ObjectTooLargeError(
                f"{key} is {size} bytes; the store accepts up to {self._max_object_bytes}"
            )
        digest = hashlib.sha256(data).hexdigest()
        view = memoryview(data)
        starts = range(0, size, CHUNK_BYTES)

        try:
            with self._session_factory() as session:
                self._enforce_quota(session, tenant_id, key, size)
                # Replacing a key replaces its chunks too (ON DELETE CASCADE).
                session.execute(delete(StoredObject).where(StoredObject.key == key))
                session.execute(
                    insert(StoredObject).values(
                        key=key,
                        tenant_id=tenant_id,
                        content_type=content_type,
                        size_bytes=size,
                        sha256=digest,
                        chunk_count=len(starts),
                    )
                )
                # Core inserts, one chunk each: the ORM would keep every chunk
                # alive in the session until the commit, which is the whole file
                # held a second time.
                for seq, start in enumerate(starts):
                    session.execute(
                        insert(StoredObjectChunk).values(
                            key=key, seq=seq, data=bytes(view[start : start + CHUNK_BYTES])
                        )
                    )
                session.commit()
        except StorageError:
            raise
        except SQLAlchemyError as exc:
            raise StorageError(f"could not store {key}: {exc}") from exc
        return key

    def get(self, key: str) -> bytes:
        # `stream` verifies the checksum when it reaches the end, so a file that
        # does not match is an exception here and never a return value.
        return b"".join(self.stream(key))

    def stream(self, key: str) -> Iterator[bytes]:
        """The file one chunk at a time, holding one chunk in memory.

        Each chunk is read in its own short transaction, so a slow client never
        keeps a database connection checked out while it downloads.
        """
        info = self._require(key)
        digest = hashlib.sha256()
        for seq in range(info.chunk_count):
            chunk = self._chunk(key, seq)
            digest.update(chunk)
            yield chunk
        if digest.hexdigest() != info.sha256:
            raise StorageError(f"{key} is corrupt: its checksum does not match")

    def stat(self, key: str) -> ObjectInfo | None:
        """The catalogue entry for `key`, or `None`. Never reads the payload."""
        try:
            with self._session_factory() as session:
                row = session.execute(
                    select(
                        StoredObject.tenant_id,
                        StoredObject.content_type,
                        StoredObject.size_bytes,
                        StoredObject.sha256,
                        StoredObject.chunk_count,
                    ).where(StoredObject.key == key)
                ).first()
        except SQLAlchemyError as exc:
            raise StorageError(f"could not read {key}: {exc}") from exc
        if row is None:
            return None
        return ObjectInfo(
            key, row.tenant_id, row.content_type, row.size_bytes, row.sha256, row.chunk_count
        )

    def delete(self, key: str) -> bool:
        try:
            with self._session_factory() as session:
                deleted = session.execute(
                    delete(StoredObject).where(StoredObject.key == key).returning(StoredObject.key)
                ).first()
                session.commit()
        except SQLAlchemyError as exc:
            raise StorageError(f"could not delete {key}: {exc}") from exc
        return deleted is not None

    def exists(self, key: str) -> bool:
        return self.stat(key) is not None

    def usage(self, tenant_id: uuid.UUID) -> int:
        """Bytes a business has stored. One indexed query on the catalogue."""
        try:
            with self._session_factory() as session:
                return self._used(session, tenant_id)
        except SQLAlchemyError as exc:
            raise StorageError(f"could not measure storage for {tenant_id}: {exc}") from exc

    def presigned_url(self, key: str, *, expires_in: int = 900) -> str:
        """A link the API will serve `key` at, until it expires.

        No query: like an S3 presigned URL, signing is arithmetic, so listing
        thirty photos costs thirty signatures and no round trip.
        """
        if self._link_secret is None or self._public_base_url is None:
            raise StorageError("this store has no signing key or public URL configured")
        tenant_of(key)
        try:
            token, _ = links.sign(key, expires_in, secret=self._link_secret)
        except ValueError as exc:
            raise StorageError(str(exc)) from exc
        return f"{self._public_base_url}/api/v1/files/{token}"

    def resolve_link(self, token: str) -> links.VerifiedLink | None:
        """The key a link authorises, or `None` if it is not a live, genuine one."""
        if self._link_secret is None:
            return None
        verified = links.verify(token, secret=self._link_secret)
        if verified is None:
            return None
        try:
            # Belt and braces: a genuine signature over a malformed key is still no key.
            tenant_of(verified.key)
        except StorageError:
            return None
        return verified

    def stream_range(self, key: str, start: int, end: int) -> Iterator[bytes]:
        """Bytes `start` to `end` inclusive, a chunk at a time.

        Only the chunks that overlap the range are read, so seeking to the end of
        a 100 MB video costs one chunk, not a hundred. Not checksummed: a partial
        read cannot be, and `stream` is the path that proves the whole file.
        """
        info = self._require(key)
        if start < 0 or end < start or end >= info.size_bytes:
            raise StorageError(f"bytes {start}-{end} are outside {key} ({info.size_bytes} bytes)")
        for seq in range(start // CHUNK_BYTES, end // CHUNK_BYTES + 1):
            chunk = self._chunk(key, seq)
            offset = seq * CHUNK_BYTES
            yield chunk[max(start - offset, 0) : end - offset + 1]

    def _require(self, key: str) -> ObjectInfo:
        info = self.stat(key)
        if info is None:
            raise ObjectNotFoundError(key)
        return info

    def _chunk(self, key: str, seq: int) -> bytes:
        try:
            with self._session_factory() as session:
                data = session.scalar(
                    select(StoredObjectChunk.data).where(
                        StoredObjectChunk.key == key, StoredObjectChunk.seq == seq
                    )
                )
        except SQLAlchemyError as exc:
            raise StorageError(f"could not read {key}: {exc}") from exc
        if data is None:
            # The object was replaced or deleted while it was being read.
            raise ObjectNotFoundError(key)
        return bytes(data)

    def _enforce_quota(self, session: Session, tenant_id: uuid.UUID, key: str, size: int) -> None:
        if not self._tenant_quota_bytes:
            return
        # What this key already holds is about to be replaced, so it does not
        # count against the new file.
        used = self._used(session, tenant_id, excluding=key)
        if used + size > self._tenant_quota_bytes:
            raise QuotaExceededError(
                f"storing {key} would take {tenant_id} to {used + size} bytes; "
                f"the quota is {self._tenant_quota_bytes}"
            )

    @staticmethod
    def _used(session: Session, tenant_id: uuid.UUID, excluding: str | None = None) -> int:
        query = select(func.coalesce(func.sum(StoredObject.size_bytes), 0)).where(
            StoredObject.tenant_id == tenant_id
        )
        if excluding is not None:
            query = query.where(StoredObject.key != excluding)
        return int(session.scalar(query) or 0)
