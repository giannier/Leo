"""In-memory `StorageProvider` for tests."""

from __future__ import annotations

from app.providers.storage.base import ObjectNotFoundError


class MockStorageProvider:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        self.objects[key] = (data, content_type)
        return key

    def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise ObjectNotFoundError(key)
        return self.objects[key][0]

    def delete(self, key: str) -> bool:
        return self.objects.pop(key, None) is not None

    def exists(self, key: str) -> bool:
        return key in self.objects

    def presigned_url(self, key: str, *, expires_in: int = 900) -> str:
        if key not in self.objects:
            raise ObjectNotFoundError(key)
        return f"https://mock-storage.local/{key}?expires_in={expires_in}"
