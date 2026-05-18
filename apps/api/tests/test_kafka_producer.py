"""Tests for the Kafka producer's pure helpers and degradation behavior.

The producer wraps confluent-kafka. The pure parts — topic routing,
CloudEvent envelope construction, and the graceful-degrade fallback
when confluent-kafka isn't installed — are testable without a real
broker. The actual produce/flush calls are exercised through a stubbed
producer object.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.services.messaging.kafka_producer import (
    DEFAULT_TOPIC,
    TOPIC_RULES,
    KafkaEventProducer,
    _build_cloudevent,
    _resolve_topic,
)

# =========================================================================
# _resolve_topic
# =========================================================================


def test_safety_event_routes_to_safety_topic():
    assert _resolve_topic("constructai.safety.incident_detected") == "constructai.safety"


def test_controls_event_routes_to_controls_topic():
    assert _resolve_topic("constructai.controls.policy_violation") == "constructai.controls"


def test_documents_event_routes_to_documents_topic():
    """Pattern matches both ``document.`` and ``documents.``."""
    assert _resolve_topic("constructai.documents.uploaded") == "constructai.documents"
    assert _resolve_topic("constructai.document.processed") == "constructai.documents"


def test_procore_event_routes_to_procore_webhooks_topic():
    assert _resolve_topic("constructai.procore.rfi.created") == "procore.webhooks"


def test_unrecognized_event_falls_back_to_default():
    assert _resolve_topic("constructai.something.else") == DEFAULT_TOPIC
    assert _resolve_topic("foo.bar.baz") == DEFAULT_TOPIC
    assert _resolve_topic("") == DEFAULT_TOPIC


def test_topic_rules_canonical():
    """Pin the four canonical topic rules — refactor must not drop one."""
    topics = {topic for _, topic in TOPIC_RULES}
    expected = {
        "constructai.safety",
        "constructai.controls",
        "constructai.documents",
        "procore.webhooks",
    }
    assert topics == expected


def test_first_matching_rule_wins():
    """If event_type would match multiple rules, the first listed rule
    wins (TOPIC_RULES order matters per the module docstring)."""
    # Documents rule matches before any default — sanity:
    out = _resolve_topic("constructai.documents.x")
    assert out == "constructai.documents"


# =========================================================================
# _build_cloudevent
# =========================================================================


def test_cloudevent_has_required_specversion_1_0():
    """CloudEvents 1.0 spec requires ``specversion`` field at top level."""
    ev = _build_cloudevent("x.y", {"k": 1}, source="/test")
    assert ev["specversion"] == "1.0"


def test_cloudevent_includes_required_attributes():
    ev = _build_cloudevent("x.y", {"k": 1}, source="/test")
    # CloudEvents required: id, source, specversion, type
    for required in ("id", "source", "specversion", "type"):
        assert required in ev
    assert ev["type"] == "x.y"
    assert ev["source"] == "/test"


def test_cloudevent_id_is_uuid():
    """Each event must have a unique UUID id — two consecutive events
    must NOT share an id."""
    a = _build_cloudevent("x", {}, source="/s")
    b = _build_cloudevent("x", {}, source="/s")
    assert a["id"] != b["id"]
    # Valid UUID format:
    import uuid

    uuid.UUID(a["id"])  # raises ValueError if malformed


def test_cloudevent_time_is_iso_format():
    ev = _build_cloudevent("x", {}, source="/s")
    # Round-trip through fromisoformat to verify:
    from datetime import datetime

    parsed = datetime.fromisoformat(ev["time"])
    assert parsed is not None


def test_cloudevent_datacontenttype_is_json():
    ev = _build_cloudevent("x", {}, source="/s")
    assert ev["datacontenttype"] == "application/json"


def test_cloudevent_carries_data_payload():
    payload = {"project_id": "p-123", "severity": "critical", "count": 5}
    ev = _build_cloudevent("safety.incident", payload, source="/agent")
    assert ev["data"] == payload


# =========================================================================
# KafkaEventProducer — graceful degradation
# =========================================================================


def test_producer_unavailable_when_confluent_kafka_missing():
    """If confluent-kafka isn't installed, the producer must construct
    successfully (no exception) but mark itself unavailable."""
    with patch.dict("sys.modules", {"confluent_kafka": None}):
        prod = KafkaEventProducer()
        assert prod.available is False


def test_producer_unavailable_when_init_raises():
    """Network problems / bad config → constructor raises something
    other than ImportError → producer marks unavailable, doesn't crash."""
    fake_module = MagicMock()
    fake_module.Producer = MagicMock(side_effect=RuntimeError("connection refused"))
    with patch.dict("sys.modules", {"confluent_kafka": fake_module}):
        prod = KafkaEventProducer()
        assert prod.available is False


# =========================================================================
# KafkaEventProducer.publish — happy path with mocked producer
# =========================================================================


async def test_publish_returns_none_when_unavailable():
    """When producer isn't available, publish should drop the event
    and return None, not raise."""
    with patch.dict("sys.modules", {"confluent_kafka": None}):
        prod = KafkaEventProducer()
    out = await prod.publish("safety.incident", {"x": 1})
    assert out is None


async def test_publish_succeeds_with_mocked_producer():
    """Drive the happy path with a stubbed Producer — verify the
    producer.produce() call carries the right topic, key, and JSON
    payload."""
    fake_module = MagicMock()
    fake_producer = MagicMock()
    fake_producer.poll = MagicMock(return_value=None)
    fake_producer.produce = MagicMock(return_value=None)
    fake_module.Producer = MagicMock(return_value=fake_producer)

    with patch.dict("sys.modules", {"confluent_kafka": fake_module}):
        prod = KafkaEventProducer()

    assert prod.available is True

    eid = await prod.publish(
        event_type="constructai.safety.incident",
        data={"severity": "critical"},
    )
    # Returns the CloudEvent id:
    assert isinstance(eid, str)
    fake_producer.produce.assert_called_once()
    call_kwargs = fake_producer.produce.call_args.kwargs
    assert call_kwargs["topic"] == "constructai.safety"
    # Key is the CloudEvent id encoded as utf-8:
    assert call_kwargs["key"] == eid.encode("utf-8")
    # Value is JSON-encoded CloudEvent envelope:
    envelope = json.loads(call_kwargs["value"].decode("utf-8"))
    assert envelope["type"] == "constructai.safety.incident"
    assert envelope["data"]["severity"] == "critical"


async def test_publish_returns_none_on_produce_exception():
    """If producer.produce() raises, publish swallows it and returns
    None — caller doesn't get an exception."""
    fake_module = MagicMock()
    fake_producer = MagicMock()
    fake_producer.produce = MagicMock(side_effect=BufferError("queue full"))
    fake_module.Producer = MagicMock(return_value=fake_producer)

    with patch.dict("sys.modules", {"confluent_kafka": fake_module}):
        prod = KafkaEventProducer()

    out = await prod.publish("x.y", {})
    assert out is None


async def test_close_marks_producer_unavailable():
    """After close(), publish() must reject with None."""
    fake_module = MagicMock()
    fake_producer = MagicMock()
    fake_producer.flush = MagicMock(return_value=None)
    fake_module.Producer = MagicMock(return_value=fake_producer)

    with patch.dict("sys.modules", {"confluent_kafka": fake_module}):
        prod = KafkaEventProducer()
    assert prod.available is True

    await prod.close()
    assert prod.available is False
    out = await prod.publish("x.y", {})
    assert out is None


async def test_flush_no_op_when_no_producer():
    """flush() on an unavailable producer must be a no-op, not crash."""
    with patch.dict("sys.modules", {"confluent_kafka": None}):
        prod = KafkaEventProducer()
    # No exception:
    await prod.flush()
