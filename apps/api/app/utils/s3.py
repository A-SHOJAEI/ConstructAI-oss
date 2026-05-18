"""S3 / MinIO client wrapper for document storage."""

from __future__ import annotations

import time

import structlog
from botocore.exceptions import ClientError

from app.config import settings

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.0  # seconds

# S3 error codes that are safe to retry (transient server-side issues)
_RETRYABLE_ERROR_CODES = {"SlowDown", "ServiceUnavailable", "InternalError"}

# ---------------------------------------------------------------------------
# Lazy-initialised boto3 client
# ---------------------------------------------------------------------------

_client = None


def get_s3_client():
    """Return a configured boto3 S3 client (singleton)."""
    global _client
    if _client is None:
        import boto3

        _client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name="us-east-1",
        )
    return _client


# ---------------------------------------------------------------------------
# Bucket management
# ---------------------------------------------------------------------------


def ensure_bucket_exists() -> None:
    """Create the documents bucket if it does not already exist."""
    client = get_s3_client()
    bucket = settings.S3_BUCKET_DOCUMENTS
    try:
        client.head_bucket(Bucket=bucket)
        logger.debug("s3_bucket_exists", bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)
        logger.info("s3_bucket_created", bucket=bucket)


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------


def upload_file(
    s3_key: str, file_bytes: bytes, content_type: str = "application/octet-stream"
) -> str:
    """Upload *file_bytes* to S3 under *s3_key* and return the key.

    Retries up to ``_MAX_RETRIES`` times on transient S3 errors
    (SlowDown, ServiceUnavailable, InternalError) with exponential backoff.
    """
    client = get_s3_client()
    bucket = settings.S3_BUCKET_DOCUMENTS
    last_error: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            client.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=file_bytes,
                ContentType=content_type,
            )
            logger.info(
                "s3_upload_complete",
                bucket=bucket,
                s3_key=s3_key,
                size_bytes=len(file_bytes),
                content_type=content_type,
            )
            return s3_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in _RETRYABLE_ERROR_CODES:
                last_error = e
                logger.warning(
                    "s3_upload_retry",
                    bucket=bucket,
                    s3_key=s3_key,
                    attempt=attempt + 1,
                    error_code=error_code,
                )
                time.sleep(_RETRY_BACKOFF * (2**attempt))
                continue
            raise  # Non-retryable error
        except Exception as e:
            last_error = e
            logger.warning(
                "s3_upload_retry",
                bucket=bucket,
                s3_key=s3_key,
                attempt=attempt + 1,
                error=str(e),
            )
            time.sleep(_RETRY_BACKOFF * (2**attempt))
            continue

    raise RuntimeError(
        f"S3 upload failed after {_MAX_RETRIES} retries: {last_error}"
    ) from last_error


def download_file(s3_key: str) -> bytes:
    """Download a file from S3 and return its contents as bytes.

    Retries up to ``_MAX_RETRIES`` times on transient S3 errors with
    exponential backoff. Raises ``FileNotFoundError`` when the key
    does not exist (NoSuchKey).
    """
    client = get_s3_client()
    bucket = settings.S3_BUCKET_DOCUMENTS
    last_error: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            response = client.get_object(Bucket=bucket, Key=s3_key)
            data = response["Body"].read()
            logger.info(
                "s3_download_complete",
                bucket=bucket,
                s3_key=s3_key,
                size_bytes=len(data),
            )
            return data
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "NoSuchKey":
                raise FileNotFoundError(f"S3 key not found: {s3_key} (bucket={bucket})") from e
            if error_code in _RETRYABLE_ERROR_CODES:
                last_error = e
                logger.warning(
                    "s3_download_retry",
                    bucket=bucket,
                    s3_key=s3_key,
                    attempt=attempt + 1,
                    error_code=error_code,
                )
                time.sleep(_RETRY_BACKOFF * (2**attempt))
                continue
            raise  # Non-retryable error
        except Exception as e:
            last_error = e
            logger.warning(
                "s3_download_retry",
                bucket=bucket,
                s3_key=s3_key,
                attempt=attempt + 1,
                error=str(e),
            )
            time.sleep(_RETRY_BACKOFF * (2**attempt))
            continue

    raise RuntimeError(
        f"S3 download failed after {_MAX_RETRIES} retries: {last_error}"
    ) from last_error


def generate_presigned_url(s3_key: str, expires_in: int = 3600) -> str:
    """Generate a pre-signed URL that grants temporary GET access to *s3_key*."""
    client = get_s3_client()
    bucket = settings.S3_BUCKET_DOCUMENTS
    url: str = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=expires_in,
    )
    logger.debug(
        "s3_presigned_url_generated",
        bucket=bucket,
        s3_key=s3_key,
        expires_in=expires_in,
    )
    return url
