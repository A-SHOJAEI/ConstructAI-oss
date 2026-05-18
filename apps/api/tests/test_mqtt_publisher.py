"""Tests for the safety MQTT publisher.

The wrapper handles broker auth, TLS, connect/disconnect lifecycle,
and topic-name construction. Mocked at the paho-mqtt boundary so the
tests run without a real broker.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.safety.mqtt_publisher import MQTTPublisher

# ---- TLS configuration ---------------------------------------------------


class _SettingsWithCert:
    """Stand-in for ``app.config.settings`` exposing MQTT_CA_CERT.

    Pydantic v2 ``Settings`` is strict about extra fields, so we can't
    monkey-patch a CA path onto the real instance. Replace the module-
    level reference with this stub for TLS-enabled tests.
    """

    MQTT_CA_CERT = "/etc/ssl/ca.pem"
    MQTT_USERNAME = ""
    MQTT_PASSWORD = ""


def test_no_tls_when_ca_not_configured():
    """The default Settings has no MQTT_CA_CERT field — TLS stays off
    and the publisher uses the plaintext port."""
    pub = MQTTPublisher(broker_port=1883)
    assert pub._tls_enabled is False
    assert pub.broker_port == 1883


def test_tls_implicitly_switches_to_8883(monkeypatch):
    """When MQTT_CA_CERT is configured and the caller passes the
    standard plaintext port (1883), the publisher must switch to the
    TLS port (8883). This guards against accidental plaintext writes
    when ops adds a CA cert."""
    monkeypatch.setattr("app.services.safety.mqtt_publisher.settings", _SettingsWithCert())
    pub = MQTTPublisher(broker_port=1883)
    assert pub._tls_enabled is True
    assert pub.broker_port == 8883


def test_tls_keeps_explicit_non_default_port(monkeypatch):
    """A non-default explicit port (e.g. 9001) should be respected even
    when TLS is enabled — operator has chosen a custom config."""
    monkeypatch.setattr("app.services.safety.mqtt_publisher.settings", _SettingsWithCert())
    pub = MQTTPublisher(broker_port=9001)
    assert pub._tls_enabled is True
    assert pub.broker_port == 9001


# ---- connect ------------------------------------------------------------


def test_connect_skipped_when_paho_not_installed(monkeypatch):
    monkeypatch.setattr("app.services.safety.mqtt_publisher._HAS_MQTT", False)
    pub = MQTTPublisher()
    pub.connect()  # must not raise
    assert pub._client is None


def test_connect_configures_credentials(monkeypatch):
    monkeypatch.setattr("app.config.settings.MQTT_USERNAME", "constructai", raising=False)
    monkeypatch.setattr("app.config.settings.MQTT_PASSWORD", "secret", raising=False)

    fake_client = MagicMock()
    monkeypatch.setattr("app.services.safety.mqtt_publisher._HAS_MQTT", True)
    fake_mqtt_module = MagicMock()
    fake_mqtt_module.Client = MagicMock(return_value=fake_client)
    fake_mqtt_module.CallbackAPIVersion.VERSION2 = MagicMock()

    monkeypatch.setattr("app.services.safety.mqtt_publisher.mqtt", fake_mqtt_module)

    pub = MQTTPublisher()
    pub.connect()

    fake_client.username_pw_set.assert_called_once_with("constructai", "secret")
    fake_client.connect.assert_called_once()
    fake_client.loop_start.assert_called_once()


def test_connect_skips_credentials_when_unset(monkeypatch):
    monkeypatch.setattr("app.config.settings.MQTT_USERNAME", "", raising=False)
    monkeypatch.setattr("app.config.settings.MQTT_PASSWORD", "", raising=False)

    fake_client = MagicMock()
    monkeypatch.setattr("app.services.safety.mqtt_publisher._HAS_MQTT", True)
    fake_mqtt = MagicMock()
    fake_mqtt.Client.return_value = fake_client
    monkeypatch.setattr("app.services.safety.mqtt_publisher.mqtt", fake_mqtt)

    pub = MQTTPublisher()
    pub.connect()

    fake_client.username_pw_set.assert_not_called()


def test_connect_swallows_broker_errors(monkeypatch):
    """Broker unreachable at connect time must not raise — the publisher
    just stays disconnected and ``publish_event`` returns False."""
    fake_client = MagicMock()
    fake_client.connect = MagicMock(side_effect=ConnectionRefusedError("no broker"))
    monkeypatch.setattr("app.services.safety.mqtt_publisher._HAS_MQTT", True)
    fake_mqtt = MagicMock()
    fake_mqtt.Client.return_value = fake_client
    monkeypatch.setattr("app.services.safety.mqtt_publisher.mqtt", fake_mqtt)

    pub = MQTTPublisher()
    # Must not raise:
    pub.connect()
    assert pub._connected is False


def test_connect_applies_tls_config_when_enabled(monkeypatch):
    monkeypatch.setattr("app.services.safety.mqtt_publisher.settings", _SettingsWithCert())

    fake_client = MagicMock()
    monkeypatch.setattr("app.services.safety.mqtt_publisher._HAS_MQTT", True)
    fake_mqtt = MagicMock()
    fake_mqtt.Client.return_value = fake_client
    monkeypatch.setattr("app.services.safety.mqtt_publisher.mqtt", fake_mqtt)

    pub = MQTTPublisher()
    pub.connect()

    fake_client.tls_set.assert_called_once_with(ca_certs="/etc/ssl/ca.pem")


# ---- publish_event ------------------------------------------------------


@pytest.fixture
def connected_publisher(monkeypatch):
    """A publisher that's marked connected with a mock client.

    Skips the actual connect() flow — the publish path is what we want
    to exercise here."""
    pub = MQTTPublisher()
    fake = MagicMock()
    fake.publish.return_value = MagicMock(rc=0)
    pub._client = fake
    pub._connected = True
    return pub


def test_publish_event_uses_safety_topic_name(connected_publisher: MQTTPublisher):
    connected_publisher.publish_event("proj-1", {"alert_type": "ppe_violation"})
    args, kwargs = connected_publisher._client.publish.call_args
    assert args[0] == "constructai/proj-1/safety/events"
    assert kwargs.get("qos") == 1


def test_publish_event_returns_true_on_success(connected_publisher: MQTTPublisher):
    out = connected_publisher.publish_event("proj-1", {"x": 1})
    assert out is True


def test_publish_event_returns_false_on_publish_failure(
    connected_publisher: MQTTPublisher,
):
    """Non-zero rc from paho indicates broker rejection — surface as False."""
    connected_publisher._client.publish.return_value = MagicMock(rc=4)
    assert connected_publisher.publish_event("proj-1", {"x": 1}) is False


def test_publish_event_returns_false_when_disconnected():
    pub = MQTTPublisher()
    # Default state: no client, not connected
    assert pub.publish_event("proj-1", {"x": 1}) is False


def test_publish_event_returns_false_when_client_is_none():
    pub = MQTTPublisher()
    pub._connected = True  # claim connected but no client
    pub._client = None
    assert pub.publish_event("proj-1", {"x": 1}) is False


def test_publish_event_serializes_unjsonable_via_default_str(
    connected_publisher: MQTTPublisher,
):
    """The publisher uses ``default=str`` so e.g. datetime objects in
    the event don't break the JSON payload."""
    from datetime import UTC, datetime

    payload = {"timestamp": datetime(2026, 1, 1, tzinfo=UTC)}
    out = connected_publisher.publish_event("proj-1", payload)
    assert out is True


# ---- publish_health -----------------------------------------------------


def test_publish_health_uses_health_topic(connected_publisher: MQTTPublisher):
    connected_publisher.publish_health("device-42", {"cpu": 0.4})
    args, kwargs = connected_publisher._client.publish.call_args
    assert args[0] == "constructai/device-42/health"
    assert kwargs.get("qos") == 0  # health uses fire-and-forget


def test_publish_health_returns_false_when_disconnected():
    pub = MQTTPublisher()
    assert pub.publish_health("device-42", {"cpu": 0.1}) is False


# ---- disconnect ---------------------------------------------------------


def test_disconnect_calls_paho_lifecycle_cleanly(connected_publisher: MQTTPublisher):
    connected_publisher.disconnect()
    connected_publisher._client.loop_stop.assert_called_once()
    connected_publisher._client.disconnect.assert_called_once()
    assert connected_publisher._connected is False


def test_disconnect_safe_when_never_connected():
    pub = MQTTPublisher()
    # Must not raise:
    pub.disconnect()


# ---- callbacks ----------------------------------------------------------


def test_on_connect_marks_publisher_connected():
    pub = MQTTPublisher()
    pub._on_connect(client=MagicMock(), userdata=None, flags={}, reason_code=0)
    assert pub._connected is True


def test_on_disconnect_marks_publisher_disconnected():
    pub = MQTTPublisher()
    pub._connected = True
    pub._on_disconnect(client=MagicMock(), userdata=None, flags={}, reason_code=0)
    assert pub._connected is False
