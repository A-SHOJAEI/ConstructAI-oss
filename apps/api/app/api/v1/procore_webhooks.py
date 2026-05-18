"""Procore webhook handler.

Receives real-time event notifications from Procore and publishes
them to Kafka for async processing. The endpoint:
  - Verifies HMAC-SHA256 signature (X-Procore-Signature header)
  - Deduplicates via Redis (X-Procore-Delivery-Id, 24hr TTL)
  - Returns 200 immediately (Procore has a 30-second timeout)
  - Publishes to Kafka topic "procore.webhooks" for async processing
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
from collections import OrderedDict

from fastapi import APIRouter, Request, Response, status

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Redis key prefix and TTL for idempotency
_WEBHOOK_SEEN_PREFIX = "procore:webhook:seen:"
_WEBHOOK_SEEN_TTL = 86_400  # 24 hours

# ---------------------------------------------------------------------------
# In-memory LRU dedup cache (fallback when Redis is briefly unavailable)
# ---------------------------------------------------------------------------
_LRU_MAX = 1000
_lru_lock = threading.Lock()
_lru_cache: OrderedDict[str, bool] = OrderedDict()


def _lru_check_and_add(delivery_id: str) -> bool:
    """Return True if *delivery_id* was already seen. Thread-safe LRU."""
    with _lru_lock:
        if delivery_id in _lru_cache:
            _lru_cache.move_to_end(delivery_id)
            return True
        _lru_cache[delivery_id] = True
        if len(_lru_cache) > _LRU_MAX:
            _lru_cache.popitem(last=False)
        return False


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Procore's HMAC-SHA256 webhook signature.

    Procore signs each webhook payload with the shared secret using
    HMAC-SHA256 and sends the hex digest in X-Procore-Signature.
    """
    if not secret:
        logger.warning("PROCORE_WEBHOOK_SECRET not configured; rejecting webhook")
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def procore_webhook(request: Request) -> Response:
    """Handle inbound Procore webhook events.

    Returns 200 immediately to avoid Procore's 30-second timeout.
    Actual processing happens asynchronously via Kafka consumer.
    """
    # Read raw body for signature verification
    body = await request.body()

    # Verify HMAC signature
    signature = request.headers.get("X-Procore-Signature", "")
    if not verify_signature(body, signature, settings.PROCORE_WEBHOOK_SECRET):
        logger.warning("Procore webhook signature verification failed")
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    # Idempotency check via Redis (with in-memory LRU fallback)
    delivery_id = request.headers.get("X-Procore-Delivery-Id", "")
    delivery_id = delivery_id[:256]
    if delivery_id:
        from app.services.cache import CacheService

        cache = CacheService()
        redis_available = False
        try:
            if not await cache._ensure_client():
                logger.warning("Redis unavailable for webhook dedup")
            elif cache._client is not None:
                redis_available = True
                # Use SET NX for atomic check-and-set
                was_set = await cache._client.set(
                    f"{_WEBHOOK_SEEN_PREFIX}{delivery_id}",
                    "1",
                    ex=_WEBHOOK_SEEN_TTL,
                    nx=True,
                )
                if not was_set:
                    logger.info("Duplicate webhook delivery %s; skipping", delivery_id)
                    return Response(
                        status_code=status.HTTP_200_OK,
                        content='{"status": "duplicate"}',
                        media_type="application/json",
                    )
        except Exception as exc:
            logger.warning("Redis dedup check failed: %s", exc)

        if not redis_available:
            # Brief in-memory LRU fallback for dedup during short Redis outages
            if _lru_check_and_add(delivery_id):
                logger.info("Duplicate webhook delivery %s (LRU fallback); skipping", delivery_id)
                return Response(
                    status_code=status.HTTP_200_OK,
                    content='{"status": "duplicate"}',
                    media_type="application/json",
                )
            # Return 503 so Procore retries when Redis is back for durable dedup
            logger.warning(
                "Redis unavailable; returning 503 for delivery %s (LRU dedup applied)",
                delivery_id,
            )
            return Response(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content='{"status": "temporarily_unavailable", "detail": "Dedup service offline, please retry"}',
                media_type="application/json",
            )

    # Parse the JSON payload
    import json

    try:
        event = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        logger.error("Invalid JSON in Procore webhook payload")
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    # Extract event metadata
    resource_name = event.get("resource_name", "unknown")
    event_type = event.get("event_type", "unknown")
    resource_id = event.get("resource_id")
    project_id = event.get("project_id")
    company_id = event.get("company_id")

    logger.info(
        "Procore webhook received: %s.%s (resource_id=%s, project_id=%s)",
        resource_name,
        event_type,
        resource_id,
        project_id,
    )

    # Publish to Kafka for async processing
    try:
        producer = _get_kafka_producer()
        if producer:
            await producer.publish(
                event_type=f"constructai.procore.{resource_name}.{event_type}",
                data={
                    "resource_name": resource_name,
                    "event_type": event_type,
                    "resource_id": resource_id,
                    "project_id": project_id,
                    "company_id": company_id,
                    "delivery_id": delivery_id,
                    "payload": event,
                },
                source="/procore-webhook",
            )
        else:
            logger.warning(
                "Kafka unavailable; webhook event %s.%s dropped", resource_name, event_type
            )
    except Exception as exc:
        logger.error("Failed to publish webhook to Kafka: %s", exc)

    return Response(
        status_code=status.HTTP_200_OK,
        content='{"status": "accepted"}',
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# Kafka producer singleton
# ---------------------------------------------------------------------------

_kafka_producer = None
_kafka_init_attempted = False


def _get_kafka_producer():
    """Lazy singleton for the Kafka producer."""
    global _kafka_producer, _kafka_init_attempted
    if _kafka_init_attempted:
        return _kafka_producer
    _kafka_init_attempted = True

    from app.services.messaging.kafka_producer import KafkaEventProducer

    _kafka_producer = KafkaEventProducer(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
    )
    if not _kafka_producer.available:
        _kafka_producer = None
    return _kafka_producer
