"""`STORAGE_BACKEND` picks the store, and the choice is validated at startup."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.providers.storage import PostgresStorageProvider, S3StorageProvider
from app.providers.storage.factory import build_storage_provider


def test_postgres_builds_the_postgres_store() -> None:
    store = build_storage_provider(Settings(storage_backend="postgres"))

    assert isinstance(store, PostgresStorageProvider)


def test_the_postgres_store_can_sign_links_on_the_public_address() -> None:
    store = build_storage_provider(
        Settings(storage_backend="postgres", api_public_base_url="https://leo.example.pe")
    )
    key = "tenants/6f1c2d3e-0000-4000-8000-000000000001/media/photo.png"

    assert isinstance(store, PostgresStorageProvider)
    assert store.presigned_url(key).startswith("https://leo.example.pe/api/v1/files/v1.")


def test_s3_builds_the_s3_store() -> None:
    store = build_storage_provider(Settings(storage_backend="s3"))

    assert isinstance(store, S3StorageProvider)


def test_an_unknown_backend_stops_the_application_from_starting() -> None:
    with pytest.raises(ValidationError):
        Settings(storage_backend="floppy")  # type: ignore[arg-type]


def test_the_default_is_postgres_with_no_quota() -> None:
    """Nothing to run, and nothing refused that was accepted before."""
    settings = Settings()

    assert settings.storage_backend == "postgres"
    assert settings.storage_tenant_quota_bytes == 0
