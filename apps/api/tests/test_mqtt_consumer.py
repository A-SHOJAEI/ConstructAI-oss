"""Tests for the MQTT consumer wrapper.

Pin handler registration, multi-handler dispatch, the JSON
decoding contract, and the no-paho-mqtt graceful-degradation
path. Doesn't exercise an actual MQTT broker.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.realtime.mqtt_consumer import MQTTConsumer

# =========================================================================
# Construction defaults
# =========================================================================


def test_default_broker_host_localhost():
    c = MQTTConsumer()
    assert c.broker_host == "localhost"


def test_default_broker_port_1883():
    """[contract] 1883 is the canonical MQTT plaintext port. Pin so a
    refactor doesn't accidentally use the TLS port (8883)."""
    c = MQTTConsumer()
    assert c.broker_port == 1883


def test_explicit_host_port():
    c = MQTTConsumer(broker_host="mqtt.example.com", broker_port=8883)
    assert c.broker_host == "mqtt.example.com"
    assert c.broker_port == 8883


def test_initial_state_no_client_no_handlers():
    c = MQTTConsumer()
    assert c._client is None
    assert c._handlers == {}


# =========================================================================
# subscribe — handler registration
# =========================================================================


def test_subscribe_registers_handler():
    c = MQTTConsumer()
    h = MagicMock()
    c.subscribe("safety/events", h)
    assert h in c._handlers["safety/events"]


def test_subscribe_multiple_handlers_same_topic():
    """Multiple handlers per topic -> all stored."""
    c = MQTTConsumer()
    h1, h2, h3 = MagicMock(), MagicMock(), MagicMock()
    c.subscribe("topic", h1)
    c.subscribe("topic", h2)
    c.subscribe("topic", h3)
    assert c._handlers["topic"] == [h1, h2, h3]


def test_subscribe_different_topics_isolated():
    c = MQTTConsumer()
    h1, h2 = MagicMock(), MagicMock()
    c.subscribe("topic-A", h1)
    c.subscribe("topic-B", h2)
    assert c._handlers["topic-A"] == [h1]
    assert c._handlers["topic-B"] == [h2]


def test_subscribe_when_connected_calls_client_subscribe():
    """If the client is connected at subscribe time, the topic is
    immediately subscribed on the wire."""
    c = MQTTConsumer()
    fake_client = MagicMock()
    c._client = fake_client
    c.subscribe("my/topic", MagicMock())
    fake_client.subscribe.assert_called_once_with("my/topic", qos=1)


def test_subscribe_qos_1_default():
    """[contract] QoS 1 (at-least-once) for safety events. Pin so a
    refactor doesn't accidentally drop to QoS 0 (best-effort)."""
    c = MQTTConsumer()
    fake_client = MagicMock()
    c._client = fake_client
    c.subscribe("topic", MagicMock())
    _args, kwargs = fake_client.subscribe.call_args
    assert kwargs.get("qos") == 1


# =========================================================================
# _on_message — JSON decode + handler dispatch
# =========================================================================


def test_on_message_dispatches_to_handler():
    """Valid JSON payload -> all subscribed handlers receive
    (topic, decoded_payload)."""
    c = MQTTConsumer()
    h1 = MagicMock()
    h2 = MagicMock()
    c.subscribe("safety/events", h1)
    c.subscribe("safety/events", h2)

    msg = MagicMock()
    msg.topic = "safety/events"
    msg.payload = b'{"event":"ppe_violation","camera":"cam-1"}'

    c._on_message(MagicMock(), None, msg)

    h1.assert_called_once_with(
        "safety/events",
        {"event": "ppe_violation", "camera": "cam-1"},
    )
    h2.assert_called_once_with(
        "safety/events",
        {"event": "ppe_violation", "camera": "cam-1"},
    )


def test_on_message_no_handlers_for_topic_no_dispatch():
    """[edge case] Message on un-subscribed topic -> no crash, no
    dispatch."""
    c = MQTTConsumer()
    c.subscribe("topic-A", MagicMock())  # only A

    msg = MagicMock()
    msg.topic = "topic-B"
    msg.payload = b'{"x": 1}'

    # Just verify no exception:
    c._on_message(MagicMock(), None, msg)


def test_on_message_invalid_json_swallowed():
    """[error isolation] Malformed JSON -> exception logged but NOT
    raised. Handler not called. Pin: a single bad payload must NOT
    kill the consumer loop."""
    c = MQTTConsumer()
    h = MagicMock()
    c.subscribe("topic", h)

    msg = MagicMock()
    msg.topic = "topic"
    msg.payload = b"not valid json {{{"

    # Must not raise:
    c._on_message(MagicMock(), None, msg)
    h.assert_not_called()


def test_on_message_handler_exception_swallowed():
    """[error isolation] Handler raises -> caught (so other handlers
    don't run, but the consumer loop survives)."""
    c = MQTTConsumer()
    boom = MagicMock(side_effect=RuntimeError("handler crashed"))
    c.subscribe("topic", boom)

    msg = MagicMock()
    msg.topic = "topic"
    msg.payload = b'{"x": 1}'

    # Must not raise:
    c._on_message(MagicMock(), None, msg)


def test_on_message_decodes_utf8_payload():
    """Payload is bytes -> decoded as UTF-8 before JSON parse."""
    c = MQTTConsumer()
    h = MagicMock()
    c.subscribe("topic", h)

    msg = MagicMock()
    msg.topic = "topic"
    msg.payload = '{"name":"café"}'.encode()

    c._on_message(MagicMock(), None, msg)
    h.assert_called_once_with("topic", {"name": "café"})


# =========================================================================
# _on_connect — auto-subscribe on reconnect
# =========================================================================


def test_on_connect_subscribes_to_all_registered_topics():
    """[contract] On connect, the consumer re-subscribes to every
    topic in self._handlers — this is what makes resubscription
    after disconnect work."""
    c = MQTTConsumer()
    c.subscribe("topic-A", MagicMock())
    c.subscribe("topic-B", MagicMock())
    c.subscribe("topic-C", MagicMock())

    fake_client = MagicMock()
    c._on_connect(fake_client, None, {}, 0)

    subscribed_topics = {call.args[0] for call in fake_client.subscribe.call_args_list}
    assert subscribed_topics == {"topic-A", "topic-B", "topic-C"}


def test_on_connect_subscribes_with_qos_1():
    c = MQTTConsumer()
    c.subscribe("topic", MagicMock())

    fake_client = MagicMock()
    c._on_connect(fake_client, None, {}, 0)

    fake_client.subscribe.assert_called_once_with("topic", qos=1)


def test_on_connect_no_handlers_no_subscribe():
    """No registered handlers -> no subscribe calls (no crash)."""
    c = MQTTConsumer()
    fake_client = MagicMock()
    c._on_connect(fake_client, None, {}, 0)
    fake_client.subscribe.assert_not_called()


# =========================================================================
# disconnect — cleanup
# =========================================================================


def test_disconnect_with_no_client_is_noop():
    """[edge case] disconnect() before connect() -> no crash."""
    c = MQTTConsumer()
    c.disconnect()  # Just verify no exception


def test_disconnect_stops_loop_and_disconnects():
    c = MQTTConsumer()
    fake_client = MagicMock()
    c._client = fake_client
    c.disconnect()
    fake_client.loop_stop.assert_called_once()
    fake_client.disconnect.assert_called_once()


# =========================================================================
# connect — graceful degradation
# =========================================================================


def test_connect_without_paho_mqtt_no_crash():
    """[fallback] paho-mqtt not installed -> warning logged, no
    crash, _client stays None."""
    c = MQTTConsumer()
    with patch("app.services.realtime.mqtt_consumer._HAS_MQTT", False):
        c.connect()
    assert c._client is None


def test_connect_broker_failure_no_crash():
    """[error isolation] Broker connection failure -> error logged,
    no crash. The consumer can be reattempted later."""
    fake_mqtt_client = MagicMock()
    fake_mqtt_client.connect.side_effect = ConnectionRefusedError("broker down")

    fake_module = MagicMock()
    fake_module.Client.return_value = fake_mqtt_client
    fake_module.CallbackAPIVersion.VERSION2 = MagicMock()

    c = MQTTConsumer()
    with (
        patch("app.services.realtime.mqtt_consumer._HAS_MQTT", True),
        patch("app.services.realtime.mqtt_consumer.mqtt", fake_module),
    ):
        c.connect()  # must not raise
