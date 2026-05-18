"""Tests for the Kafka consumer pure helpers.

The full async consumer needs a real broker; these tests pin the
deterministic CloudEvents deserialization (validation gate that
routes malformed payloads to DLQ) and module constants.
"""

from __future__ import annotations

import json

import pytest

from app.services.messaging.kafka_consumer import (
    CONSUMER_GROUP,
    DLQ_TOPIC,
    SUBSCRIBE_TOPICS,
    _deserialize_cloudevent,
)

# =========================================================================
# Module constants
# =========================================================================


def test_subscribe_topics_canonical():
    """Pin the subscribed topics — must match the producer's emit list."""
    expected = {
        "constructai.events",
        "constructai.safety",
        "constructai.controls",
        "constructai.documents",
    }
    assert set(SUBSCRIBE_TOPICS) == expected


def test_consumer_group_canonical():
    """Pin the consumer group name — Kafka uses this for offset
    tracking; renaming would orphan production offsets."""
    assert CONSUMER_GROUP == "constructai-api"


def test_dlq_topic_canonical():
    """Pin the DLQ topic — operations rely on this name being stable
    for alerting / dashboards."""
    assert DLQ_TOPIC == "constructai.dlq"


# =========================================================================
# _deserialize_cloudevent
# =========================================================================


def test_deserialize_valid_cloudevent():
    """Well-formed CloudEvent 1.0 envelope round-trips."""
    envelope = {
        "specversion": "1.0",
        "id": "abc-123",
        "type": "constructai.safety.incident",
        "source": "/agent/safety",
        "data": {"project_id": "p-1", "severity": "critical"},
    }
    raw = json.dumps(envelope).encode("utf-8")
    out = _deserialize_cloudevent(raw)
    assert out == envelope


def test_deserialize_unsupported_specversion_raises():
    """[contract] specversion must be "1.0" — older versions or
    typos must raise so the consumer routes to DLQ."""
    envelope = {"specversion": "0.9", "type": "x", "data": {}}
    raw = json.dumps(envelope).encode("utf-8")
    with pytest.raises(ValueError, match="Unsupported specversion"):
        _deserialize_cloudevent(raw)


def test_deserialize_missing_specversion_raises():
    """No specversion at all → also rejected."""
    envelope = {"type": "x", "data": {}}
    raw = json.dumps(envelope).encode("utf-8")
    with pytest.raises(ValueError, match="Unsupported specversion"):
        _deserialize_cloudevent(raw)


def test_deserialize_missing_type_raises():
    """[contract] CloudEvent type is required — without it, handlers
    can't route. Must reject so message goes to DLQ instead of being
    silently consumed."""
    envelope = {"specversion": "1.0", "id": "x", "data": {}}
    raw = json.dumps(envelope).encode("utf-8")
    with pytest.raises(ValueError, match="missing required 'type'"):
        _deserialize_cloudevent(raw)


def test_deserialize_invalid_json_raises():
    """Non-JSON bytes → JSONDecodeError (caller routes to DLQ)."""
    raw = b"not valid json {{"
    with pytest.raises(json.JSONDecodeError):
        _deserialize_cloudevent(raw)


def test_deserialize_empty_bytes_raises():
    raw = b""
    with pytest.raises(json.JSONDecodeError):
        _deserialize_cloudevent(raw)


def test_deserialize_minimal_valid_envelope():
    """Only the documented required fields (specversion, type) — plus
    minimal valid metadata."""
    envelope = {"specversion": "1.0", "type": "minimal.event"}
    raw = json.dumps(envelope).encode("utf-8")
    out = _deserialize_cloudevent(raw)
    assert out["specversion"] == "1.0"
    assert out["type"] == "minimal.event"


def test_deserialize_preserves_data_payload():
    """The full data field is preserved end-to-end so handlers can
    consume it."""
    payload = {
        "nested": {"value": 42, "list": [1, 2, 3]},
        "string_field": "hello",
        "bool_field": True,
    }
    envelope = {
        "specversion": "1.0",
        "type": "test.event",
        "data": payload,
    }
    raw = json.dumps(envelope).encode("utf-8")
    out = _deserialize_cloudevent(raw)
    assert out["data"] == payload


def test_deserialize_handles_unicode_correctly():
    """UTF-8 encoded payloads with international characters must
    deserialize correctly — construction projects span jurisdictions."""
    envelope = {
        "specversion": "1.0",
        "type": "constructai.safety.incident",
        "data": {"location": "Montréal", "trade": "maçonnerie", "emoji": "🏗️"},
    }
    raw = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    out = _deserialize_cloudevent(raw)
    assert out["data"]["location"] == "Montréal"
    assert out["data"]["emoji"] == "🏗️"
