"""S3-compatible object storage: MinIO in development, S3 in production.

boto3 rather than the `minio` SDK, per plan.md: the same client works against
both, so production does not run a different code path from development.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.providers.storage.base import ObjectNotFoundError, StorageError


class S3StorageProvider:
    """Objects in one bucket."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        public_endpoint_url: str | None = None,
    ) -> None:
        self._bucket = bucket

        def build(endpoint: str | None) -> Any:
            return boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                # Path addressing: MinIO does not serve virtual-host style
                # buckets on a bare hostname, and signature v4 is what it
                # validates.
                config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
            )

        self._client: Any = build(endpoint_url)
        # A presigned URL is signed over its host, so a link meant for a
        # browser has to be signed with the host the browser will reach, not
        # the in-network one (`minio:9000`) the backend uses. Signing is
        # arithmetic over the credential: this second client never connects.
        self._signer: Any = (
            build(public_endpoint_url)
            if public_endpoint_url and public_endpoint_url != endpoint_url
            else self._client
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not exist. Safe to call repeatedly."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            try:
                self._client.create_bucket(Bucket=self._bucket)
            except (ClientError, BotoCoreError) as exc:
                raise StorageError(f"could not create bucket {self._bucket}: {exc}") from exc

    def put(self, key: str, data: bytes, *, content_type: str) -> str:
        try:
            self._put(key, data, content_type)
        except ClientError as exc:
            # A fresh MinIO has no bucket, and nothing else creates it: the
            # first write would fail and, before this, took the request down
            # with it (the simulator's "No pudimos contactar al servidor").
            if exc.response.get("Error", {}).get("Code") != "NoSuchBucket":
                raise StorageError(f"could not store {key}: {exc}") from exc
            self.ensure_bucket()
            try:
                self._put(key, data, content_type)
            except (ClientError, BotoCoreError) as retry_exc:
                raise StorageError(f"could not store {key}: {retry_exc}") from retry_exc
        except BotoCoreError as exc:
            raise StorageError(f"could not store {key}: {exc}") from exc
        return key

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise ObjectNotFoundError(key) from exc
            raise StorageError(f"could not read {key}: {exc}") from exc
        except BotoCoreError as exc:
            # Unreachable endpoint, timeout: a storage failure like any other.
            raise StorageError(f"could not read {key}: {exc}") from exc
        body: bytes = response["Body"].read()
        return body

    def delete(self, key: str) -> bool:
        if not self.exists(key):
            return False
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"could not delete {key}: {exc}") from exc
        return True

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError:
            return False
        except BotoCoreError as exc:
            # An unreachable endpoint or a timeout is not "no such object". Answering
            # False would let a caller act on a store it could not even see.
            raise StorageError(f"could not check {key}: {exc}") from exc
        return True

    def iter_objects(self) -> Iterator[tuple[str, str]]:
        """Every `(key, content_type)` in the bucket, for moving files to another store.

        Not part of the port: listing is only needed to migrate, never to serve.
        """
        try:
            for page in self._client.get_paginator("list_objects_v2").paginate(Bucket=self._bucket):
                for entry in page.get("Contents", []):
                    head = self._client.head_object(Bucket=self._bucket, Key=entry["Key"])
                    yield entry["Key"], head.get("ContentType", "application/octet-stream")
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"could not list {self._bucket}: {exc}") from exc

    def presigned_url(self, key: str, *, expires_in: int = 900) -> str:
        try:
            url: str = self._signer.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"could not sign {key}: {exc}") from exc
        return url
