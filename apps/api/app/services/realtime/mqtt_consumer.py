"""MQTT consumer for detection events."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt

    _HAS_MQTT = True
except ImportError:
    mqtt = None  # type: ignore[assignment]
    _HAS_MQTT = False


class MQTTConsumer:
    """Subscribe to MQTT topics and process detection events."""

    def __init__(self, broker_host: str = "localhost", broker_port: int = 1883):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self._client = None
        self._handlers: dict[str, list] = {}

    def connect(self):
        if not _HAS_MQTT:
            logger.warning("paho-mqtt not installed")
            return
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_message = self._on_message
        self._client.on_connect = self._on_connect
        try:
            self._client.connect(self.broker_host, self.broker_port, keepalive=60)
            self._client.loop_start()
        except Exception as exc:
            logger.error("MQTT consumer connection failed: %s", exc)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        logger.info("MQTT consumer connected")
        for topic in self._handlers:
            client.subscribe(topic, qos=1)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            for handler in self._handlers.get(msg.topic, []):
                handler(msg.topic, payload)
        except Exception as exc:
            logger.error("Error processing MQTT message: %s", exc)

    def subscribe(self, topic: str, handler):
        self._handlers.setdefault(topic, []).append(handler)
        if self._client:
            self._client.subscribe(topic, qos=1)

    def disconnect(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
