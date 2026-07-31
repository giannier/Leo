"""S3-compatible object storage: MinIO in development, S3 in production.

boto3 rather than the `minio` SDK, per plan.md: the same client works against
both, so production does not run a different code path from development.
"""

from __future__ import annotations

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
    ) -> None:
        self._bucket = bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            # Path addressing: MinIO does not serve virtual-host style buckets
            # on a bare hostname, and signature v4 is what it validates.
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
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
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"could not store {key}: {exc}") from exc
        return key

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise ObjectNotFoundError(key) from exc
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
        return True

    def presigned_url(self, key: str, *, expires_in: int = 900) -> str:
        try:
            url: str = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"could not sign {key}: {exc}") from exc
        return url
