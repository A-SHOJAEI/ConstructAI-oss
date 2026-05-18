"""Tests for the S3/MinIO client wrapper.

Pin retry behavior (3 attempts, exponential backoff), the
documented retryable error code set, NoSuchKey -> FileNotFoundError
conversion, and presigned URL parameter passing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.utils import s3 as s3_module
from app.utils.s3 import (
    _MAX_RETRIES,
    _RETRY_BACKOFF,
    _RETRYABLE_ERROR_CODES,
    download_file,
    ensure_bucket_exists,
    generate_presigned_url,
    upload_file,
)


@pytest.fixture(autouse=True)
def _reset_client():
    """Reset the singleton client between tests so patches don't leak."""
    s3_module._client = None
    yield
    s3_module._client = None


# =========================================================================
# Constants
# =========================================================================


def test_max_retries_is_3():
    """[contract] 3 retry attempts. Pin so a refactor doesn't quietly
    bump to 1 (loses transient-error tolerance) or 100 (DoS attempts)."""
    assert _MAX_RETRIES == 3


def test_retry_backoff_is_1_second():
    """[contract] 1s base backoff. Combined with 2^attempt yields
    1s, 2s, 4s — total ~7s worst case before giving up."""
    assert _RETRY_BACKOFF == 1.0


def test_retryable_error_codes_canonical():
    """[contract] Pin the retryable error set. Pin so a refactor
    doesn't accidentally retry on auth errors (would mask config
    issues) or stop retrying on transient errors."""
    assert {"SlowDown", "ServiceUnavailable", "InternalError"} == _RETRYABLE_ERROR_CODES


# =========================================================================
# upload_file
# =========================================================================


def test_upload_file_happy_path():
    fake_client = MagicMock()
    fake_client.put_object.return_value = {}

    with patch("app.utils.s3.get_s3_client", return_value=fake_client):
        out = upload_file("docs/test.pdf", b"file content", content_type="application/pdf")

    assert out == "docs/test.pdf"
    fake_client.put_object.assert_called_once()
    call_kwargs = fake_client.put_object.call_args.kwargs
    assert call_kwargs["Key"] == "docs/test.pdf"
    assert call_kwargs["Body"] == b"file content"
    assert call_kwargs["ContentType"] == "application/pdf"


def test_upload_file_default_content_type():
    """[fallback] Default content_type is application/octet-stream."""
    fake_client = MagicMock()
    with patch("app.utils.s3.get_s3_client", return_value=fake_client):
        upload_file("docs/test.bin", b"x")

    assert fake_client.put_object.call_args.kwargs["ContentType"] == "application/octet-stream"


def test_upload_file_retries_on_retryable_error():
    """SlowDown error -> retry, eventually succeeds."""
    err = ClientError(
        {"Error": {"Code": "SlowDown", "Message": "Slow down"}},
        "PutObject",
    )
    fake_client = MagicMock()
    # Fail twice with SlowDown, then succeed:
    fake_client.put_object.side_effect = [err, err, {}]

    with (
        patch("app.utils.s3.get_s3_client", return_value=fake_client),
        patch("app.utils.s3.time.sleep"),  # don't actually sleep
    ):
        out = upload_file("docs/test.pdf", b"x")

    assert out == "docs/test.pdf"
    assert fake_client.put_object.call_count == 3


def test_upload_file_non_retryable_error_raises_immediately():
    """[invariant] Non-retryable error code (e.g., AccessDenied) -> raise
    immediately. Pin so a refactor doesn't mask config issues with
    silent retries."""
    err = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Forbidden"}},
        "PutObject",
    )
    fake_client = MagicMock()
    fake_client.put_object.side_effect = err

    with (
        patch("app.utils.s3.get_s3_client", return_value=fake_client),
        patch("app.utils.s3.time.sleep"),
    ):
        with pytest.raises(ClientError, match="AccessDenied"):
            upload_file("docs/test.pdf", b"x")

    # Only ONE attempt — no retries on non-retryable errors:
    assert fake_client.put_object.call_count == 1


def test_upload_file_exhausted_retries_raises_runtime_error():
    """[contract] After 3 failed retries -> RuntimeError. Pin the
    error type so callers can catch it specifically."""
    err = ClientError(
        {"Error": {"Code": "ServiceUnavailable", "Message": "503"}},
        "PutObject",
    )
    fake_client = MagicMock()
    fake_client.put_object.side_effect = err

    with (
        patch("app.utils.s3.get_s3_client", return_value=fake_client),
        patch("app.utils.s3.time.sleep"),
    ):
        with pytest.raises(RuntimeError, match="failed after 3 retries"):
            upload_file("docs/test.pdf", b"x")

    assert fake_client.put_object.call_count == 3


def test_upload_file_uses_exponential_backoff():
    """[contract] Backoff schedule: 1s, 2s, 4s (2^attempt * base)."""
    err = ClientError(
        {"Error": {"Code": "InternalError", "Message": "500"}},
        "PutObject",
    )
    fake_client = MagicMock()
    fake_client.put_object.side_effect = err

    sleep_durations = []

    def capture_sleep(seconds):
        sleep_durations.append(seconds)

    with (
        patch("app.utils.s3.get_s3_client", return_value=fake_client),
        patch("app.utils.s3.time.sleep", side_effect=capture_sleep),
    ):
        with pytest.raises(RuntimeError):
            upload_file("docs/test.pdf", b"x")

    # 3 retries -> 3 sleeps with 1.0, 2.0, 4.0 (2^attempt):
    assert sleep_durations == [1.0, 2.0, 4.0]


# =========================================================================
# download_file
# =========================================================================


def test_download_file_happy_path():
    fake_body = MagicMock()
    fake_body.read.return_value = b"file content"
    fake_client = MagicMock()
    fake_client.get_object.return_value = {"Body": fake_body}

    with patch("app.utils.s3.get_s3_client", return_value=fake_client):
        out = download_file("docs/test.pdf")

    assert out == b"file content"


def test_download_file_no_such_key_raises_filenotfound():
    """[invariant] NoSuchKey -> FileNotFoundError (NOT ClientError).
    Pin so callers can catch FileNotFoundError without unpacking
    ClientError details."""
    err = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
        "GetObject",
    )
    fake_client = MagicMock()
    fake_client.get_object.side_effect = err

    with patch("app.utils.s3.get_s3_client", return_value=fake_client):
        with pytest.raises(FileNotFoundError, match=r"docs/missing\.pdf"):
            download_file("docs/missing.pdf")


def test_download_file_retries_on_transient_error():
    err = ClientError(
        {"Error": {"Code": "SlowDown", "Message": "Slow down"}},
        "GetObject",
    )
    fake_body = MagicMock()
    fake_body.read.return_value = b"after retry"
    fake_client = MagicMock()
    fake_client.get_object.side_effect = [err, {"Body": fake_body}]

    with (
        patch("app.utils.s3.get_s3_client", return_value=fake_client),
        patch("app.utils.s3.time.sleep"),
    ):
        out = download_file("docs/test.pdf")

    assert out == b"after retry"
    assert fake_client.get_object.call_count == 2


def test_download_file_non_retryable_raises_immediately():
    """AccessDenied -> raise (single attempt)."""
    err = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Forbidden"}},
        "GetObject",
    )
    fake_client = MagicMock()
    fake_client.get_object.side_effect = err

    with (
        patch("app.utils.s3.get_s3_client", return_value=fake_client),
        patch("app.utils.s3.time.sleep"),
    ):
        with pytest.raises(ClientError, match="AccessDenied"):
            download_file("docs/test.pdf")

    assert fake_client.get_object.call_count == 1


def test_download_file_exhausted_retries_raises_runtime_error():
    err = ClientError(
        {"Error": {"Code": "ServiceUnavailable", "Message": "503"}},
        "GetObject",
    )
    fake_client = MagicMock()
    fake_client.get_object.side_effect = err

    with (
        patch("app.utils.s3.get_s3_client", return_value=fake_client),
        patch("app.utils.s3.time.sleep"),
    ):
        with pytest.raises(RuntimeError, match="download failed after 3 retries"):
            download_file("docs/test.pdf")


# =========================================================================
# ensure_bucket_exists
# =========================================================================


def test_ensure_bucket_exists_already_exists():
    """head_bucket succeeds -> no create call."""
    fake_client = MagicMock()
    fake_client.head_bucket.return_value = {}
    with patch("app.utils.s3.get_s3_client", return_value=fake_client):
        ensure_bucket_exists()

    fake_client.head_bucket.assert_called_once()
    fake_client.create_bucket.assert_not_called()


def test_ensure_bucket_exists_creates_when_missing():
    """head_bucket raises -> create_bucket is called."""
    err = ClientError(
        {"Error": {"Code": "NoSuchBucket", "Message": "Not found"}},
        "HeadBucket",
    )
    fake_client = MagicMock()
    fake_client.head_bucket.side_effect = err
    fake_client.create_bucket.return_value = {}
    with patch("app.utils.s3.get_s3_client", return_value=fake_client):
        ensure_bucket_exists()

    fake_client.create_bucket.assert_called_once()


# =========================================================================
# generate_presigned_url
# =========================================================================


def test_generate_presigned_url_passes_canonical_params():
    """[contract] Calls boto3.generate_presigned_url with
    ('get_object', Params={Bucket, Key}, ExpiresIn=...). Pin so a
    refactor doesn't switch to a different boto3 method."""
    fake_client = MagicMock()
    fake_client.generate_presigned_url.return_value = "https://signed/url"

    with patch("app.utils.s3.get_s3_client", return_value=fake_client):
        out = generate_presigned_url("docs/test.pdf", expires_in=600)

    assert out == "https://signed/url"
    args, kwargs = fake_client.generate_presigned_url.call_args
    assert args[0] == "get_object"
    assert kwargs["Params"]["Key"] == "docs/test.pdf"
    assert kwargs["ExpiresIn"] == 600


def test_generate_presigned_url_default_expires_in_3600():
    """[contract] Default 1-hour expiration. Pin so a refactor
    doesn't quietly extend (security risk) or shorten (UX issue)."""
    fake_client = MagicMock()
    fake_client.generate_presigned_url.return_value = "x"

    with patch("app.utils.s3.get_s3_client", return_value=fake_client):
        generate_presigned_url("docs/test.pdf")

    assert fake_client.generate_presigned_url.call_args.kwargs["ExpiresIn"] == 3600


# =========================================================================
# get_s3_client singleton
# =========================================================================


def test_get_s3_client_returns_singleton():
    """[contract] get_s3_client memoizes the boto3 client (one
    connection per process). Pin so refactor doesn't create per-call
    clients (connection-pool exhaustion risk)."""
    # boto3 is imported lazily inside get_s3_client, so patch the
    # top-level boto3 module that the import resolves to:
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = MagicMock()
    with patch.dict("sys.modules", {"boto3": fake_boto3}):
        c1 = s3_module.get_s3_client()
        c2 = s3_module.get_s3_client()
        c3 = s3_module.get_s3_client()

    assert c1 is c2 is c3
    # boto3.client called exactly once (memoized):
    assert fake_boto3.client.call_count == 1
