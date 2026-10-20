"""Builds the configured `StorageProvider`.

The API and the worker both need the store, and both used to build the S3 client
by hand. Going through one factory means a new backend is a change here and
nowhere else - the same rule the email and LLM adapters follow.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.db import get_session_factory
from app.providers.storage.base import StorageProvider
from app.providers.storage.links import derive_secret
from app.providers.storage.postgres_provider import PostgresStorageProvider
from app.providers.storage.s3_provider import S3StorageProvider


def build_storage_provider(settings: Settings | None = None) -> StorageProvider:
    """The store `STORAGE_BACKEND` names: Postgres, or an S3-compatible service."""
    settings = settings or get_settings()
    if settings.storage_backend == "postgres":
        return PostgresStorageProvider(
            get_session_factory(),
            max_object_bytes=settings.storage_max_object_bytes,
            tenant_quota_bytes=settings.storage_tenant_quota_bytes,
            link_secret=derive_secret(settings.secret_key),
            public_base_url=settings.api_public_base_url,
        )
    return S3StorageProvider(
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        public_endpoint_url=settings.s3_public_endpoint_url,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )
