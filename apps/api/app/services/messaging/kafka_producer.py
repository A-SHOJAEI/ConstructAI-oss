"""Kafka producer with CloudEvents serialization."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Topic mapping: regex on event_type -> Kafka topic
# Order matters -- first match wins.
# ---------------------------------------------------------------------------
TOPIC_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^constructai\.safety\."), "constructai.safety"),
    (re.compile(r"^constructai\.controls\."), "constructai.controls"),
    (re.compile(r"^constructai\.documents?\."), "constructai.documents"),
    (re.compile(r"^constructai\.procore\."), "procore.webhooks"),
]
DEFAULT_TOPIC = "constructai.events"


def _resolve_topic(event_type: str) -> str:
    """Return the Kafka topic for *event_type*."""
    for pattern, topic in TOPIC_RULES:
        if pattern.search(event_type):
            return topic
    return DEFAULT_TOPIC


def _build_cloudevent(
    event_type: str,
    data: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    """Build a CloudEvents 1.0 structured-content envelope."""
    return {
        "specversion": "1.0",
        "id": str(uuid.uuid4()),
        "type": event_type,
        "source": source,
        "time": datetime.now(UTC).isoformat(),
        "datacontenttype": "application/json",
        "data": data,
    }


def _delivery_callback(err: Any, msg: Any) -> None:  # pragma: no cover
    """Called once per message by librdkafka when delivery completes."""
    if err is not None:
        logger.error(
            "Kafka delivery failed for %s: %s",
            msg.topic() if msg else "unknown",
            err,
        )
    else:
        logger.debug(
            "Kafka message delivered to %s [%s] @ offset %s",
            msg.topic(),
            msg.partition(),
            msg.offset(),
        )


class KafkaEventProducer:
    """Async-friendly Kafka producer that serializes CloudEvents.

    Usage::

        producer = KafkaEventProducer(bootstrap_servers="localhost:29092")
        await producer.publish(
            event_type="constructai.safety.incident_detected",
            data={"project_id": "abc", "severity": "critical"},
            source="/safety-agent",
        )
        await producer.flush()

    If ``confluent-kafka`` is not installed or the broker is unreachable the
    producer degrades gracefully -- a warning is logged and no exception is
    raised to the caller.
    """

    def __init__(self, bootstrap_servers: str = "localhost:29092") -> None:
        self._bootstrap_servers = bootstrap_servers
        self._producer: Any | None = None
        self._available: bool = False
        self._init_producer()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    def _init_producer(self) -> None:
        """Try to create the underlying confluent-kafka Producer."""
        try:
            from confluent_kafka import Producer  # type: ignore[import-untyped]

            # H-18: acks=all + enable.idempotence=true gives us end-to-end
            # at-least-once guarantees with safe retries. max.in.flight<=5 is
            # required for librdkafka to preserve ordering when retries > 0.
            # min.insync.replicas=2 is enforced on the broker side (production
            # compose), so acks=all here means "2 of 3 brokers must ack".
            self._producer = Producer(
                {
                    "bootstrap.servers": self._bootstrap_servers,
                    "client.id": "constructai-api-producer",
                    "acks": "all",
                    "enable.idempotence": True,
                    "max.in.flight.requests.per.connection": 5,
                    "retries": 10,
                    "retry.backoff.ms": 200,
                    "linger.ms": 5,
                    "compression.type": "snappy",
                    # Fail the produce call rather than silently hanging if
                    # the broker is partitioned away for a long time.
                    "delivery.timeout.ms": 120_000,
                }
            )
            self._available = True
            logger.info(
                "KafkaEventProducer initialised (servers=%s)",
                self._bootstrap_servers,
            )
        except ImportError:
            logger.warning(
                "confluent-kafka is not installed -- "
                "Kafka producer is disabled. Install the 'execution' "
                "extras to enable it."
            )
        except Exception:
            logger.warning(
                "Failed to create Kafka producer (servers=%s). "
                "Events will NOT be published to Kafka.",
                self._bootstrap_servers,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """Return *True* when the producer is ready to publish."""
        return self._available

    async def publish(
        self,
        event_type: str,
        data: dict[str, Any],
        source: str = "/constructai-api",
    ) -> str | None:
        """Publish a CloudEvent to the appropriate Kafka topic.

        Returns the CloudEvent ``id`` on success, or ``None`` when
        Kafka is unavailable.
        """
        if not self._available or self._producer is None:
            logger.warning(
                "Kafka unavailable -- dropping event %s",
                event_type,
            )
            return None

        envelope = _build_cloudevent(event_type, data, source)
        topic = _resolve_topic(event_type)
        value = json.dumps(envelope).encode("utf-8")

        try:
            self._producer.produce(
                topic=topic,
                value=value,
                key=envelope["id"].encode("utf-8"),
                callback=_delivery_callback,
            )
            # Trigger delivery report callbacks without blocking the
            # asyncio event loop for long.
            self._producer.poll(0)
            logger.info(
                "Event %s (%s) queued to topic %s",
                envelope["id"],
                event_type,
                topic,
            )
            return envelope["id"]
        except Exception:
            logger.error(
                "Failed to produce event %s to %s",
                event_type,
                topic,
                exc_info=True,
            )
            return None

    async def flush(self, timeout: float = 5.0) -> None:
        """Block until all outstanding messages are delivered."""
        if self._producer is not None:
            self._producer.flush(timeout)

    async def close(self) -> None:
        """Flush remaining messages and release resources."""
        await self.flush()
        self._producer = None
        self._available = False
        logger.info("KafkaEventProducer closed.")
