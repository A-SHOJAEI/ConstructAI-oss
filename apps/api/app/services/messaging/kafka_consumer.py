"""Kafka consumer with CloudEvents deserialization and DLQ support."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Topics this consumer group subscribes to.
SUBSCRIBE_TOPICS: list[str] = [
    "constructai.events",
    "constructai.safety",
    "constructai.controls",
    "constructai.documents",
]

CONSUMER_GROUP = "constructai-api"
DLQ_TOPIC = "constructai.dlq"

# Type alias for event handler callbacks.
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


def _deserialize_cloudevent(raw: bytes) -> dict[str, Any]:
    """Deserialize a CloudEvents 1.0 structured JSON envelope.

    Returns the full envelope dict (including ``data``).  Raises on
    malformed payloads so callers can route to the DLQ.
    """
    envelope: dict[str, Any] = json.loads(raw)
    if envelope.get("specversion") != "1.0":
        raise ValueError(f"Unsupported specversion: {envelope.get('specversion')}")
    if "type" not in envelope:
        raise ValueError("CloudEvent missing required 'type' attribute")
    return envelope


class KafkaEventConsumer:
    """Async Kafka consumer that deserializes CloudEvents and routes
    them to a callback.

    Usage::

        async def handle(event: dict) -> None:
            print(event["type"], event["data"])

        consumer = KafkaEventConsumer(
            bootstrap_servers="localhost:29092",
            handler=handle,
        )
        await consumer.start()   # runs until stop() is called
        await consumer.stop()

    Messages that fail processing are forwarded to the dead-letter
    queue topic (``constructai.dlq``).
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:29092",
        handler: EventHandler | None = None,
        group_id: str = CONSUMER_GROUP,
        topics: list[str] | None = None,
        poll_interval: float = 0.1,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._handler = handler
        self._group_id = group_id
        self._topics = topics or list(SUBSCRIBE_TOPICS)
        self._poll_interval = poll_interval

        self._consumer: Any | None = None
        self._producer: Any | None = None  # for DLQ forwarding
        self._running: bool = False
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Initialise the consumer/producer and begin polling."""
        try:
            from confluent_kafka import Consumer, Producer  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("confluent-kafka is not installed -- Kafka consumer is disabled.")
            return

        try:
            self._consumer = Consumer(
                {
                    "bootstrap.servers": self._bootstrap_servers,
                    "group.id": self._group_id,
                    "auto.offset.reset": "earliest",
                    "enable.auto.commit": False,
                    "max.poll.interval.ms": 300_000,
                    "session.timeout.ms": 45_000,
                }
            )
            self._consumer.subscribe(self._topics)

            self._producer = Producer(
                {
                    "bootstrap.servers": self._bootstrap_servers,
                    "client.id": "constructai-api-dlq-producer",
                }
            )

            self._running = True
            self._task = asyncio.create_task(self._poll_loop())
            logger.info(
                "KafkaEventConsumer started (group=%s, topics=%s)",
                self._group_id,
                self._topics,
            )
        except Exception:
            logger.warning(
                "Failed to start Kafka consumer (servers=%s). Consumer is disabled.",
                self._bootstrap_servers,
                exc_info=True,
            )

    async def stop(self) -> None:
        """Signal the poll loop to stop and wait for it to finish."""
        self._running = False
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except TimeoutError:
                logger.warning("Consumer poll loop did not stop in time; cancelling.")
                self._task.cancel()
            self._task = None

        if self._consumer is not None:
            try:
                self._consumer.close()
            except Exception:
                logger.warning("Error closing Kafka consumer.", exc_info=True)
            self._consumer = None

        if self._producer is not None:
            try:
                self._producer.flush(timeout=5.0)
            except Exception:
                logger.warning("Error flushing DLQ producer.", exc_info=True)
            self._producer = None

        logger.info("KafkaEventConsumer stopped.")

    # ------------------------------------------------------------------
    # Internal poll loop
    # ------------------------------------------------------------------
    async def _poll_loop(self) -> None:
        """Poll Kafka in a loop, yielding to the event loop regularly."""
        while self._running:
            if self._consumer is None:
                await asyncio.sleep(self._poll_interval)
                continue
            msg = self._consumer.poll(timeout=0.0)  # non-blocking
            if msg is None:
                await asyncio.sleep(self._poll_interval)
                continue

            error = msg.error()
            if error is not None:
                logger.error("Consumer poll error: %s", error)
                await asyncio.sleep(self._poll_interval)
                continue

            await self._process_message(msg)

    async def _process_message(self, msg: Any) -> None:
        """Deserialize, route, and commit a single message."""
        raw: bytes = msg.value()
        try:
            event = _deserialize_cloudevent(raw)
        except Exception:
            logger.error(
                "Failed to deserialize message from %s [%s] @ %s -- sending to DLQ.",
                msg.topic(),
                msg.partition(),
                msg.offset(),
                exc_info=True,
            )
            self._forward_to_dlq(raw, msg.topic(), "deserialization_error")
            self._commit(msg)
            return

        try:
            if self._handler is not None:
                await self._handler(event)
            logger.debug(
                "Processed event %s (%s) from %s",
                event.get("id"),
                event.get("type"),
                msg.topic(),
            )
        except Exception:
            logger.error(
                "Handler failed for event %s (%s) -- sending to DLQ.",
                event.get("id"),
                event.get("type"),
                exc_info=True,
            )
            self._forward_to_dlq(raw, msg.topic(), "handler_error")

        self._commit(msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _commit(self, msg: Any) -> None:
        """Commit the offset for *msg*."""
        if self._consumer is None:
            return
        try:
            self._consumer.commit(message=msg, asynchronous=True)
        except Exception:
            logger.warning("Offset commit failed.", exc_info=True)

    def _forward_to_dlq(
        self,
        raw: bytes,
        original_topic: str,
        reason: str,
    ) -> None:
        """Produce the failing message to the dead-letter queue topic."""
        if self._producer is None:
            logger.error("DLQ producer unavailable; message dropped.")
            return

        headers = [
            ("dlq.original.topic", original_topic.encode("utf-8")),
            ("dlq.error.reason", reason.encode("utf-8")),
        ]
        try:
            self._producer.produce(
                topic=DLQ_TOPIC,
                value=raw,
                headers=headers,
            )
            self._producer.poll(0)
            logger.warning(
                "Message forwarded to DLQ (%s) from %s -- reason: %s",
                DLQ_TOPIC,
                original_topic,
                reason,
            )
        except Exception:
            logger.error("Failed to forward message to DLQ.", exc_info=True)
