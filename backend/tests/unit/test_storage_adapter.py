"""The S3 adapter, against a stubbed botocore client.

`MockStorageProvider` is what the rest of the suite uses, and it proves the
*port* behaves. What it cannot prove is the translation this adapter does:
botocore signals a missing object with a `ClientError` carrying an error code
buried in a nested dictionary, and everything above `providers/` is written
against `ObjectNotFoundError`. Getting that mapping wrong turns "this PDF was
deleted" into a 500.

`Stubber` is botocore's own test double. No MinIO, no network, no bucket - so
these run in CI, which has neither.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from app.providers.storage.base import ObjectNotFoundError, StorageError
from app.providers.storage.s3_provider import S3StorageProvider

BUCKET = "leo-storage"
KEY = "tenants/1234/manual.pdf"


@pytest.fixture
def provider() -> S3StorageProvider:
    return S3StorageProvider(
        bucket=BUCKET,
        endpoint_url="http://storage.invalid:9000",
        access_key="key",
        secret_key="secret",
    )


@pytest.fixture
def stub(provider: S3StorageProvider) -> Iterator[Stubber]:
    # Reaching for the private client is deliberate: `Stubber` wraps a
    # botocore client, and the whole point of this adapter is that it builds
    # its own with the addressing style and signature version MinIO needs.
    # Injecting a client from the test would leave that construction - the
    # part most likely to be wrong - unexercised.
    stubber = Stubber(provider._client)  # noqa: SLF001
    stubber.activate()
    yield stubber
    stubber.deactivate()


def _body(data: bytes) -> StreamingBody:
    return StreamingBody(io.BytesIO(data), len(data))


class TestReading:
    def test_an_object_comes_back_as_bytes(
        self, provider: S3StorageProvider, stub: Stubber
    ) -> None:
        stub.add_response(
            "get_object",
            {"Body": _body(b"%PDF-1.7 ...")},
            {"Bucket": BUCKET, "Key": KEY},
        )

        assert provider.get(KEY) == b"%PDF-1.7 ..."
        stub.assert_no_pending_responses()

    def test_a_missing_object_is_not_found_rather_than_a_storage_failure(
        self, provider: S3StorageProvider, stub: Stubber
    ) -> None:
        """The distinction the knowledge service branches on: a deleted file
        is a 404 to the tenant, a broken bucket is a 500 to be alerted on."""
        stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)

        with pytest.raises(ObjectNotFoundError):
            provider.get(KEY)

    def test_a_bare_404_is_treated_the_same_way(
        self, provider: S3StorageProvider, stub: Stubber
    ) -> None:
        """MinIO answers `NoSuchKey`; S3 sometimes answers a bare `404` for a
        caller without ListBucket."""
        stub.add_client_error("get_object", service_error_code="404", http_status_code=404)

        with pytest.raises(ObjectNotFoundError):
            provider.get(KEY)

    def test_any_other_client_error_stays_a_storage_error(
        self, provider: S3StorageProvider, stub: Stubber
    ) -> None:
        stub.add_client_error("get_object", service_error_code="AccessDenied", http_status_code=403)

        with pytest.raises(StorageError) as caught:
            provider.get(KEY)
        assert not isinstance(caught.value, ObjectNotFoundError)


class TestWriting:
    def test_put_returns_the_key_it_stored_under(
        self, provider: S3StorageProvider, stub: Stubber
    ) -> None:
        stub.add_response(
            "put_object",
            {},
            {"Bucket": BUCKET, "Key": KEY, "Body": b"data", "ContentType": "application/pdf"},
        )

        assert provider.put(KEY, b"data", content_type="application/pdf") == KEY

    def test_a_failed_write_names_the_key(self, provider: S3StorageProvider, stub: Stubber) -> None:
        stub.add_client_error("put_object", service_error_code="AccessDenied")

        with pytest.raises(StorageError, match=KEY):
            provider.put(KEY, b"data", content_type="application/pdf")

    def test_a_missing_bucket_is_created_and_the_write_retried(
        self, provider: S3StorageProvider, stub: Stubber
    ) -> None:
        """A fresh MinIO has no bucket; the first upload used to 500 the request."""
        params = {"Bucket": BUCKET, "Key": KEY, "Body": b"data", "ContentType": "image/png"}
        stub.add_client_error("put_object", service_error_code="NoSuchBucket")
        stub.add_client_error("head_bucket", service_error_code="404")
        stub.add_response("create_bucket", {}, {"Bucket": BUCKET})
        stub.add_response("put_object", {}, params)

        assert provider.put(KEY, b"data", content_type="image/png") == KEY
        stub.assert_no_pending_responses()

    def test_an_unreachable_store_on_read_is_a_storage_error(
        self, provider: S3StorageProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unreachable(**_: object) -> None:
            raise EndpointConnectionError(endpoint_url="http://storage.invalid:9000")

        monkeypatch.setattr(provider._client, "get_object", unreachable)  # noqa: SLF001

        with pytest.raises(StorageError):
            provider.get(KEY)


class TestExistenceAndDeletion:
    def test_exists_is_true_when_the_head_succeeds(
        self, provider: S3StorageProvider, stub: Stubber
    ) -> None:
        stub.add_response("head_object", {}, {"Bucket": BUCKET, "Key": KEY})

        assert provider.exists(KEY) is True

    def test_exists_is_false_rather_than_an_error(
        self, provider: S3StorageProvider, stub: Stubber
    ) -> None:
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)

        assert provider.exists(KEY) is False

    def test_deleting_something_that_is_there_reports_it(
        self, provider: S3StorageProvider, stub: Stubber
    ) -> None:
        stub.add_response("head_object", {}, {"Bucket": BUCKET, "Key": KEY})
        stub.add_response("delete_object", {}, {"Bucket": BUCKET, "Key": KEY})

        assert provider.delete(KEY) is True
        stub.assert_no_pending_responses()

    def test_deleting_something_that_is_not_there_is_not_an_error(
        self, provider: S3StorageProvider, stub: Stubber
    ) -> None:
        """Cleaning up after a failed upload must not fail because the upload
        never got as far as writing anything."""
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)

        assert provider.delete(KEY) is False

    def test_a_failed_delete_is_raised(self, provider: S3StorageProvider, stub: Stubber) -> None:
        stub.add_response("head_object", {}, {"Bucket": BUCKET, "Key": KEY})
        stub.add_client_error("delete_object", service_error_code="AccessDenied")

        with pytest.raises(StorageError, match=KEY):
            provider.delete(KEY)


class TestTheBucket:
    def test_an_existing_bucket_is_left_alone(
        self, provider: S3StorageProvider, stub: Stubber
    ) -> None:
        stub.add_response("head_bucket", {}, {"Bucket": BUCKET})

        provider.ensure_bucket()
        stub.assert_no_pending_responses()

    def test_a_missing_bucket_is_created(self, provider: S3StorageProvider, stub: Stubber) -> None:
        stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
        stub.add_response("create_bucket", {}, {"Bucket": BUCKET})

        provider.ensure_bucket()
        stub.assert_no_pending_responses()

    def test_a_bucket_that_cannot_be_created_is_a_storage_error(
        self, provider: S3StorageProvider, stub: Stubber
    ) -> None:
        stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
        stub.add_client_error("create_bucket", service_error_code="AccessDenied")

        with pytest.raises(StorageError, match=BUCKET):
            provider.ensure_bucket()

    def test_the_name_is_readable(self, provider: S3StorageProvider) -> None:
        assert provider.bucket == BUCKET


class TestPresignedUrls:
    def test_the_url_is_signed_locally_and_points_at_the_object(
        self, provider: S3StorageProvider
    ) -> None:
        """No request is made: signing is arithmetic over the credential."""
        url = provider.presigned_url(KEY, expires_in=60)

        assert url.startswith("http://storage.invalid:9000/")
        # Path addressing, because MinIO does not serve virtual-host style
        # buckets on a bare hostname.
        assert f"/{BUCKET}/" in url
        assert "X-Amz-Expires=60" in url
        assert "X-Amz-Signature=" in url

    def test_a_signing_failure_is_a_storage_error(
        self, provider: S3StorageProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*args: object, **kwargs: object) -> str:
            raise ClientError({"Error": {"Code": "InvalidRequest"}}, "GetObject")

        monkeypatch.setattr(provider._client, "generate_presigned_url", explode)  # noqa: SLF001

        with pytest.raises(StorageError, match=KEY):
            provider.presigned_url(KEY)


class TestPublicEndpoint:
    def test_links_are_signed_for_the_public_host_not_the_internal_one(self) -> None:
        provider = S3StorageProvider(
            bucket=BUCKET,
            endpoint_url="http://minio:9000",
            public_endpoint_url="https://files.example.test",
            access_key="key",
            secret_key="secret",
        )

        url = provider.presigned_url(KEY, expires_in=60)

        assert url.startswith("https://files.example.test/")
        assert "minio" not in url

    def test_without_a_public_endpoint_links_use_the_internal_one(self) -> None:
        provider = S3StorageProvider(
            bucket=BUCKET,
            endpoint_url="http://minio:9000",
            access_key="key",
            secret_key="secret",
        )

        assert provider.presigned_url(KEY).startswith("http://minio:9000/")
