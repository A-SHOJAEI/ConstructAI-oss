"""Tests for Kafka producer and consumer with CloudEvents serialization.

Mocks confluent_kafka entirely -- no broker required.
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build mock confluent_kafka module
# ---------------------------------------------------------------------------


def _make_mock_confluent_kafka():
    """Return a fake ``confluent_kafka`` module with Producer/Consumer stubs."""
    mod = types.ModuleType("confluent_kafka")
    mod.Producer = MagicMock  # each instantiation returns a new MagicMock
    mod.Consumer = MagicMock
    return mod


# ---------------------------------------------------------------------------
# CloudEvents serialisation (producer-side)
# ---------------------------------------------------------------------------


class TestCloudEventsSerialization:
    """Verify the CloudEvents 1.0 structured envelope format."""

    def test_build_cloudevent_has_required_fields(self):
        from app.services.messaging.kafka_producer import _build_cloudevent

        envelope = _build_cloudevent(
            event_type="constructai.safety.alert",
            data={"severity": "high"},
            source="/test",
        )
        # CloudEvents 1.0 required attributes
        assert envelope["specversion"] == "1.0"
        assert envelope["type"] == "constructai.safety.alert"
        assert envelope["source"] == "/test"
        assert "id" in envelope  # must be a UUID string
        assert "time" in envelope
        assert envelope["datacontenttype"] == "application/json"
        assert envelope["data"] == {"severity": "high"}

    def test_build_cloudevent_id_is_unique(self):
        from app.services.messaging.kafka_producer import _build_cloudevent

        ids = {_build_cloudevent("t", {}, "/s")["id"] for _ in range(50)}
        assert len(ids) == 50, "Each CloudEvent should get a unique id"


class TestTopicRouting:
    """Verify regex-based topic resolution."""

    def test_safety_event_routes_to_safety_topic(self):
        from app.services.messaging.kafka_producer import _resolve_topic

        assert _resolve_topic("constructai.safety.incident_detected") == "constructai.safety"

    def test_controls_event_routes_to_controls_topic(self):
        from app.services.messaging.kafka_producer import _resolve_topic

        assert _resolve_topic("constructai.controls.budget_alert") == "constructai.controls"

    def test_document_event_routes_to_documents_topic(self):
        from app.services.messaging.kafka_producer import _resolve_topic

        assert _resolve_topic("constructai.document.processed") == "constructai.documents"

    def test_procore_event_routes_to_procore_topic(self):
        from app.services.messaging.kafka_producer import _resolve_topic

        assert _resolve_topic("constructai.procore.rfi.created") == "procore.webhooks"

    def test_unknown_event_routes_to_default_topic(self):
        from app.services.messaging.kafka_producer import DEFAULT_TOPIC, _resolve_topic

        assert _resolve_topic("something.unknown") == DEFAULT_TOPIC


# ---------------------------------------------------------------------------
# Delivery callback
# ---------------------------------------------------------------------------


class TestDeliveryCallback:
    """Producer delivery callback success / failure handling."""

    def test_delivery_callback_logs_error_on_failure(self, caplog):
        from app.services.messaging.kafka_producer import _delivery_callback

        mock_msg = MagicMock()
        mock_msg.topic.return_value = "test-topic"

        import logging

        with caplog.at_level(logging.ERROR, logger="app.services.messaging.kafka_producer"):
            _delivery_callback(err="BrokerNotAvailable", msg=mock_msg)

        assert any("delivery failed" in r.message.lower() for r in caplog.records)

    def test_delivery_callback_logs_debug_on_success(self, caplog):
        from app.services.messaging.kafka_producer import _delivery_callback

        mock_msg = MagicMock()
        mock_msg.topic.return_value = "test-topic"
        mock_msg.partition.return_value = 0
        mock_msg.offset.return_value = 42

        import logging

        with caplog.at_level(logging.DEBUG, logger="app.services.messaging.kafka_producer"):
            _delivery_callback(err=None, msg=mock_msg)

        assert any("delivered" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Graceful degradation -- confluent-kafka not installed
# ---------------------------------------------------------------------------


class TestProducerGracefulDegradation:
    """Producer must degrade gracefully when confluent-kafka is unavailable."""

    @pytest.mark.asyncio
    async def test_producer_unavailable_when_import_fails(self):
        """If confluent_kafka cannot be imported, the producer logs a warning
        and marks itself as unavailable (no exception raised)."""
        with patch.dict(sys.modules, {"confluent_kafka": None}):
            # Force re-import so the guard fires
            from app.services.messaging import kafka_producer

            producer = kafka_producer.KafkaEventProducer.__new__(
                kafka_producer.KafkaEventProducer,
            )
            producer._bootstrap_servers = "localhost:29092"
            producer._producer = None
            producer._available = False
            producer._init_producer()

            assert producer.available is False

    @pytest.mark.asyncio
    async def test_publish_returns_none_when_unavailable(self):
        from app.services.messaging.kafka_producer import KafkaEventProducer

        producer = KafkaEventProducer.__new__(KafkaEventProducer)
        producer._available = False
        producer._producer = None

        result = await producer.publish("test.event", {"x": 1})
        assert result is None


class TestProducerBrokerUnreachable:
    """Producer degrades when the broker cannot be reached."""

    @pytest.mark.asyncio
    async def test_init_catches_broker_error(self):
        fake_mod = _make_mock_confluent_kafka()
        fake_mod.Producer = MagicMock(side_effect=RuntimeError("broker down"))

        with patch.dict(sys.modules, {"confluent_kafka": fake_mod}):
            from app.services.messaging.kafka_producer import KafkaEventProducer

            producer = KafkaEventProducer.__new__(KafkaEventProducer)
            producer._bootstrap_servers = "bad-host:1234"
            producer._producer = None
            producer._available = False
            producer._init_producer()

            assert producer.available is False

    @pytest.mark.asyncio
    async def test_publish_catches_produce_exception(self):
        from app.services.messaging.kafka_producer import KafkaEventProducer

        mock_inner = MagicMock()
        mock_inner.produce.side_effect = BufferError("queue full")

        producer = KafkaEventProducer.__new__(KafkaEventProducer)
        producer._available = True
        producer._producer = mock_inner
        producer._bootstrap_servers = "localhost:29092"

        result = await producer.publish("constructai.safety.alert", {"x": 1})
        assert result is None


# ---------------------------------------------------------------------------
# Consumer: DLQ routing on deserialization error
# ---------------------------------------------------------------------------


class TestConsumerDLQ:
    """Consumer routes bad messages to the dead-letter queue."""

    @pytest.mark.asyncio
    async def test_deserialization_error_routes_to_dlq(self):
        from app.services.messaging.kafka_consumer import KafkaEventConsumer

        consumer = KafkaEventConsumer.__new__(KafkaEventConsumer)
        consumer._handler = AsyncMock()

        # Mock the DLQ producer and consumer
        mock_dlq_producer = MagicMock()
        consumer._producer = mock_dlq_producer
        mock_kafka_consumer = MagicMock()
        consumer._consumer = mock_kafka_consumer

        # Build a bad message (invalid JSON)
        mock_msg = MagicMock()
        mock_msg.value.return_value = b"NOT-JSON"
        mock_msg.topic.return_value = "constructai.events"
        mock_msg.partition.return_value = 0
        mock_msg.offset.return_value = 5

        await consumer._process_message(mock_msg)

        # The DLQ producer should have been called
        mock_dlq_producer.produce.assert_called_once()
        call_kwargs = mock_dlq_producer.produce.call_args
        assert call_kwargs[1]["topic"] == "constructai.dlq"
        # Handler should NOT have been invoked
        consumer._handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handler_error_routes_to_dlq(self):
        from app.services.messaging.kafka_consumer import KafkaEventConsumer

        handler = AsyncMock(side_effect=RuntimeError("boom"))
        consumer = KafkaEventConsumer.__new__(KafkaEventConsumer)
        consumer._handler = handler

        mock_dlq_producer = MagicMock()
        consumer._producer = mock_dlq_producer
        mock_kafka_consumer = MagicMock()
        consumer._consumer = mock_kafka_consumer

        valid_event = json.dumps(
            {
                "specversion": "1.0",
                "id": "abc",
                "type": "test.event",
                "source": "/test",
                "data": {},
            }
        ).encode()

        mock_msg = MagicMock()
        mock_msg.value.return_value = valid_event
        mock_msg.topic.return_value = "constructai.events"
        mock_msg.partition.return_value = 0
        mock_msg.offset.return_value = 10

        await consumer._process_message(mock_msg)

        mock_dlq_producer.produce.assert_called_once()
        assert mock_dlq_producer.produce.call_args[1]["topic"] == "constructai.dlq"


class TestConsumerDeserialization:
    """CloudEvents deserialization on the consumer side."""

    def test_valid_cloudevent_deserialized(self):
        from app.services.messaging.kafka_consumer import _deserialize_cloudevent

        raw = json.dumps(
            {
                "specversion": "1.0",
                "id": "123",
                "type": "test.event",
                "source": "/src",
                "data": {"key": "value"},
            }
        ).encode()
        envelope = _deserialize_cloudevent(raw)
        assert envelope["type"] == "test.event"
        assert envelope["data"]["key"] == "value"

    def test_wrong_specversion_raises(self):
        from app.services.messaging.kafka_consumer import _deserialize_cloudevent

        raw = json.dumps({"specversion": "0.3", "type": "x"}).encode()
        with pytest.raises(ValueError, match="specversion"):
            _deserialize_cloudevent(raw)

    def test_missing_type_raises(self):
        from app.services.messaging.kafka_consumer import _deserialize_cloudevent

        raw = json.dumps({"specversion": "1.0"}).encode()
        with pytest.raises(ValueError, match="type"):
            _deserialize_cloudevent(raw)


# ---------------------------------------------------------------------------
# Consumer graceful shutdown
# ---------------------------------------------------------------------------


class TestConsumerGracefulShutdown:
    """Consumer stop() should close resources without raising."""

    @pytest.mark.asyncio
    async def test_stop_closes_consumer_and_producer(self):
        from app.services.messaging.kafka_consumer import KafkaEventConsumer

        consumer = KafkaEventConsumer.__new__(KafkaEventConsumer)
        consumer._running = True
        consumer._task = None
        consumer._consumer = MagicMock()
        consumer._producer = MagicMock()

        await consumer.stop()

        assert consumer._consumer is None
        assert consumer._producer is None
        assert consumer._running is False

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self):
        from app.services.messaging.kafka_consumer import KafkaEventConsumer

        consumer = KafkaEventConsumer.__new__(KafkaEventConsumer)
        consumer._running = False
        consumer._task = None
        consumer._consumer = None
        consumer._producer = None

        # Should not raise
        await consumer.stop()

    @pytest.mark.asyncio
    async def test_consumer_disabled_when_import_fails(self):
        """start() should be a no-op when confluent-kafka is missing."""
        with patch.dict(sys.modules, {"confluent_kafka": None}):
            from app.services.messaging.kafka_consumer import KafkaEventConsumer

            consumer = KafkaEventConsumer.__new__(KafkaEventConsumer)
            consumer._bootstrap_servers = "localhost:29092"
            consumer._handler = None
            consumer._group_id = "test"
            consumer._topics = ["test"]
            consumer._poll_interval = 0.1
            consumer._consumer = None
            consumer._producer = None
            consumer._running = False
            consumer._task = None

            await consumer.start()

            assert consumer._consumer is None
            assert consumer._running is False
