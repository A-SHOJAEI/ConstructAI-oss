"""Tests for the S3 / MinIO wrapper.

The wrapper handles upload, download, presigned URL, and bucket
ensure-exists. Mocks the boto3 client to exercise retry and
error-handling paths without hitting a real S3.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError


@pytest.fixture
def fake_s3_client(monkeypatch):
    """Reset the singleton between tests and patch get_s3_client to
    return a MagicMock — also patch ``time.sleep`` so retry tests don't
    actually wait."""
    import app.utils.s3 as s3_mod

    monkeypatch.setattr(s3_mod, "_client", None)
    fake = MagicMock()
    monkeypatch.setattr(s3_mod, "get_s3_client", lambda: fake)
    monkeypatch.setattr(s3_mod.time, "sleep", lambda _seconds: None)
    return fake


# ---- ensure_bucket_exists ----------------------------------------------


def test_ensure_bucket_exists_when_already_present(fake_s3_client):
    from app.utils.s3 import ensure_bucket_exists

    fake_s3_client.head_bucket = MagicMock()
    fake_s3_client.create_bucket = MagicMock()

    ensure_bucket_exists()

    fake_s3_client.head_bucket.assert_called_once()
    fake_s3_client.create_bucket.assert_not_called()


def test_ensure_bucket_exists_creates_when_missing(fake_s3_client):
    """``head_bucket`` raises a ClientError (404) → create_bucket runs."""
    from app.utils.s3 import ensure_bucket_exists

    fake_s3_client.head_bucket = MagicMock(
        side_effect=ClientError({"Error": {"Code": "404"}}, "HeadBucket")
    )
    fake_s3_client.create_bucket = MagicMock()

    ensure_bucket_exists()

    fake_s3_client.create_bucket.assert_called_once()


# ---- upload_file --------------------------------------------------------


def test_upload_file_returns_key_on_success(fake_s3_client):
    from app.utils.s3 import upload_file

    fake_s3_client.put_object = MagicMock()
    out = upload_file("docs/x.pdf", b"hello", content_type="application/pdf")
    assert out == "docs/x.pdf"
    fake_s3_client.put_object.assert_called_once()
    _, kwargs = fake_s3_client.put_object.call_args
    assert kwargs["Key"] == "docs/x.pdf"
    assert kwargs["Body"] == b"hello"
    assert kwargs["ContentType"] == "application/pdf"


def test_upload_file_retries_on_slowdown(fake_s3_client):
    """SlowDown is retryable; the wrapper should attempt up to 3 times
    and succeed on the second try."""
    from app.utils.s3 import upload_file

    slowdown = ClientError({"Error": {"Code": "SlowDown"}}, "PutObject")
    fake_s3_client.put_object = MagicMock(side_effect=[slowdown, None])

    out = upload_file("k", b"x")
    assert out == "k"
    assert fake_s3_client.put_object.call_count == 2


def test_upload_file_retries_on_service_unavailable(fake_s3_client):
    from app.utils.s3 import upload_file

    err = ClientError({"Error": {"Code": "ServiceUnavailable"}}, "PutObject")
    fake_s3_client.put_object = MagicMock(side_effect=[err, err, None])

    out = upload_file("k", b"x")
    assert out == "k"
    assert fake_s3_client.put_object.call_count == 3


def test_upload_file_raises_after_max_retries(fake_s3_client):
    """Exhausting the retry budget on a retryable error must raise
    RuntimeError so callers know the upload didn't make it."""
    from app.utils.s3 import upload_file

    slowdown = ClientError({"Error": {"Code": "SlowDown"}}, "PutObject")
    fake_s3_client.put_object = MagicMock(side_effect=slowdown)

    with pytest.raises(RuntimeError, match="S3 upload failed"):
        upload_file("k", b"x")
    assert fake_s3_client.put_object.call_count == 3


def test_upload_file_does_not_retry_non_retryable_error(fake_s3_client):
    """AccessDenied is not retryable — the wrapper must surface it
    immediately rather than burn 3 attempts."""
    from app.utils.s3 import upload_file

    denied = ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
    fake_s3_client.put_object = MagicMock(side_effect=denied)

    with pytest.raises(ClientError):
        upload_file("k", b"x")
    assert fake_s3_client.put_object.call_count == 1


def test_upload_file_retries_on_generic_exception(fake_s3_client):
    """Network/timeout errors that aren't ClientError still get retried
    — the wrapper treats any unexpected exception as transient."""
    from app.utils.s3 import upload_file

    fake_s3_client.put_object = MagicMock(side_effect=[TimeoutError("read timeout"), None])
    out = upload_file("k", b"x")
    assert out == "k"


# ---- download_file ------------------------------------------------------


def test_download_file_returns_bytes(fake_s3_client):
    from app.utils.s3 import download_file

    body = MagicMock()
    body.read = MagicMock(return_value=b"file-contents")
    fake_s3_client.get_object = MagicMock(return_value={"Body": body})

    out = download_file("docs/x.pdf")
    assert out == b"file-contents"


def test_download_file_raises_filenotfound_on_no_such_key(fake_s3_client):
    """NoSuchKey is the canonical 'file is gone' signal — wrap as
    FileNotFoundError so callers don't have to know boto3 error codes."""
    from app.utils.s3 import download_file

    err = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    fake_s3_client.get_object = MagicMock(side_effect=err)

    with pytest.raises(FileNotFoundError, match="S3 key not found"):
        download_file("docs/missing.pdf")
    # No retry on NoSuchKey:
    assert fake_s3_client.get_object.call_count == 1


def test_download_file_retries_on_slowdown(fake_s3_client):
    from app.utils.s3 import download_file

    body = MagicMock()
    body.read = MagicMock(return_value=b"data")
    err = ClientError({"Error": {"Code": "SlowDown"}}, "GetObject")
    fake_s3_client.get_object = MagicMock(side_effect=[err, {"Body": body}])

    assert download_file("k") == b"data"
    assert fake_s3_client.get_object.call_count == 2


def test_download_file_does_not_retry_access_denied(fake_s3_client):
    from app.utils.s3 import download_file

    err = ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")
    fake_s3_client.get_object = MagicMock(side_effect=err)

    with pytest.raises(ClientError):
        download_file("k")
    assert fake_s3_client.get_object.call_count == 1


def test_download_file_raises_runtime_error_after_max_retries(fake_s3_client):
    from app.utils.s3 import download_file

    err = ClientError({"Error": {"Code": "InternalError"}}, "GetObject")
    fake_s3_client.get_object = MagicMock(side_effect=err)

    with pytest.raises(RuntimeError, match="S3 download failed"):
        download_file("k")


# ---- generate_presigned_url --------------------------------------------


def test_generate_presigned_url_passes_expires_in(fake_s3_client):
    from app.utils.s3 import generate_presigned_url

    fake_s3_client.generate_presigned_url = MagicMock(return_value="https://signed.example/key")
    url = generate_presigned_url("docs/x.pdf", expires_in=900)
    assert url == "https://signed.example/key"
    _, kwargs = fake_s3_client.generate_presigned_url.call_args
    assert kwargs["ExpiresIn"] == 900


def test_generate_presigned_url_default_ttl_is_one_hour(fake_s3_client):
    from app.utils.s3 import generate_presigned_url

    fake_s3_client.generate_presigned_url = MagicMock(return_value="https://signed.example/x")
    generate_presigned_url("k")
    _, kwargs = fake_s3_client.generate_presigned_url.call_args
    assert kwargs["ExpiresIn"] == 3600


# ---- get_s3_client singleton -------------------------------------------


def test_get_s3_client_caches_singleton(monkeypatch):
    """Repeated calls return the same client — boto3 instances are
    expensive and not safe to recreate per request."""
    import app.utils.s3 as s3_mod

    monkeypatch.setattr(s3_mod, "_client", None)
    fake = MagicMock()
    fake_boto = MagicMock()
    fake_boto.client = MagicMock(return_value=fake)
    with patch.dict("sys.modules", {"boto3": fake_boto}):
        a = s3_mod.get_s3_client()
        b = s3_mod.get_s3_client()
    assert a is b
    fake_boto.client.assert_called_once()
