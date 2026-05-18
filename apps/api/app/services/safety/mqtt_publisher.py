"""MQTT event publishing for safety detection events."""

from __future__ import annotations

import json
import logging

from app.config import settings

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt

    _HAS_MQTT = True
except ImportError:
    mqtt = None  # type: ignore[assignment]
    _HAS_MQTT = False


class MQTTPublisher:
    """Publish safety events to MQTT broker."""

    def __init__(self, broker_host: str = "localhost", broker_port: int = 1883):
        self.broker_host = broker_host
        self._tls_enabled = False
        self._client = None
        self._connected = False

        # Configure TLS if certificates are available
        tls_ca = (
            getattr(settings, "MQTT_CA_CERT", None) if hasattr(settings, "MQTT_CA_CERT") else None
        )
        self._tls_enabled = bool(tls_ca)
        self._tls_ca = tls_ca

        # Use TLS port (8883) if TLS is configured, otherwise use provided port
        self.broker_port = 8883 if self._tls_enabled and broker_port == 1883 else broker_port

    def connect(self):
        if not _HAS_MQTT:
            logger.warning("paho-mqtt not installed, MQTT publishing disabled")
            return
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        # Apply TLS configuration
        if self._tls_enabled and self._tls_ca:
            self._client.tls_set(ca_certs=self._tls_ca)
            logger.info("MQTT TLS configured with CA cert: %s", self._tls_ca)

        # Authenticate with broker if credentials are configured
        mqtt_username = getattr(settings, "MQTT_USERNAME", None) or None
        mqtt_password = getattr(settings, "MQTT_PASSWORD", None) or None
        if mqtt_username and mqtt_password:
            self._client.username_pw_set(mqtt_username, mqtt_password)
            logger.info("MQTT client authentication configured for user: %s", mqtt_username)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        try:
            self._client.connect(self.broker_host, self.broker_port, keepalive=60)
            self._client.loop_start()
        except Exception as exc:
            logger.error("MQTT connection failed: %s", exc)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self._connected = True
        logger.info("MQTT connected to %s:%s", self.broker_host, self.broker_port)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._connected = False
        logger.warning("MQTT disconnected: %s", reason_code)

    def publish_event(self, project_id: str, event: dict) -> bool:
        """Publish safety event to MQTT topic."""
        topic = f"constructai/{project_id}/safety/events"
        payload = json.dumps(event, default=str)
        if self._client and self._connected:
            result = self._client.publish(topic, payload, qos=1)
            return result.rc == 0
        logger.warning("MQTT not connected, event not published")
        return False

    def publish_health(self, device_id: str, metrics: dict) -> bool:
        topic = f"constructai/{device_id}/health"
        payload = json.dumps(metrics, default=str)
        if self._client and self._connected:
            result = self._client.publish(topic, payload, qos=0)
            return result.rc == 0
        return False

    def disconnect(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False
