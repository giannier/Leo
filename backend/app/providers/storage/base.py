"""The `StorageProvider` port.

Objects are addressed by key, never by URL. Buckets are private, so a URL is
generated on demand and expires; storing one in the database would either
leak access or rot.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class StorageError(RuntimeError):
    """The object store could not complete the operation."""


class ObjectNotFoundError(StorageError):
    """No object exists at that key."""


class ObjectTooLargeError(StorageError):
    """The file is larger than the store accepts for one object."""


class QuotaExceededError(StorageError):
    """Storing the file would take its owner past their storage quota."""


@runtime_checkable
class StorageProvider(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        """Store bytes and return the key they can be read back with."""
        ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> bool: ...

    def exists(self, key: str) -> bool: ...

    def presigned_url(self, key: str, *, expires_in: int = 900) -> str:
        """Time-limited read URL, safe to hand to a browser."""
        ...
