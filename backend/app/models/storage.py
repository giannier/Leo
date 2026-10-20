"""Files kept in Postgres, behind the `StorageProvider` port.

Two tables, on purpose:

- `stored_objects` is the catalogue: key, owner, type, size, checksum. Everything
  that is not "give me the bytes" (existence, size, quota, ETag) reads only this
  table, so none of it ever touches the payload.
- `stored_object_chunks` holds the bytes in pieces of at most `CHUNK_BYTES`, one
  row each. A 100 MB PDF is a hundred rows, not one 100 MB value: no single huge
  write-ahead record, no single huge value to hold in memory, and a reader can
  stream it one chunk at a time.

The payload column is stored uncompressed and out of line (see migration 0045):
images, PDFs and video are already compressed, so compressing them again only
burns CPU.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# One chunk is one row and, at most, this much memory at a time on a read.
CHUNK_BYTES = 1024 * 1024


class StoredObject(Base):
    """One file: who owns it and how to check it, never the bytes themselves."""

    __tablename__ = "stored_objects"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_not_negative"),
        CheckConstraint("chunk_count >= 0", name="chunks_not_negative"),
    )

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    # Taken from the key (`tenants/<id>/...`), so quota and clean-up are a
    # single indexed query instead of a scan of every reference in the schema.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Hex SHA-256 of the whole file: the ETag, and the proof that the chunks
    # read back are the chunks written.
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StoredObjectChunk(Base):
    """A slice of a file. Deleting the object deletes its chunks."""

    __tablename__ = "stored_object_chunks"

    key: Mapped[str] = mapped_column(
        Text, ForeignKey("stored_objects.key", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
