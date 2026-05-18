"""MQTT client for edge device communication."""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt
    _HAS_MQTT = True
except ImportError:
    mqtt = None
    _HAS_MQTT = False


class EdgeMQTTClient:
    """MQTT client for publishing detection events from edge devices."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1883,
        device_id: str = "edge-001",
        keepalive: int = 60,
    ):
        self.host = host
        self.port = port
        self.device_id = device_id
        self.keepalive = keepalive
        self._client = None
        self._connected = False

    def connect(self):
        """Connect to MQTT broker."""
        if not _HAS_MQTT:
            raise RuntimeError("paho-mqtt not installed")

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"edge-{self.device_id}",
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        # Last will testament for device offline detection
        self._client.will_set(
            f"constructai/{self.device_id}/status",
            payload=json.dumps({"status": "offline", "device_id": self.device_id}),
            qos=1,
            retain=True,
        )

        self._client.connect(self.host, self.port, self.keepalive)
        self._client.loop_start()

        # Publish online status
        self.publish(
            f"constructai/{self.device_id}/status",
            json.dumps({"status": "online", "device_id": self.device_id, "timestamp": time.time()}),
            qos=1,
            retain=True,
        )

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self._connected = True
            logger.info("MQTT connected to %s:%d", self.host, self.port)
        else:
            logger.error("MQTT connection failed: rc=%d", rc)

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self._connected = False
        if rc != 0:
            logger.warning("MQTT unexpected disconnect: rc=%d", rc)

    def publish(self, topic: str, payload: str, qos: int = 1, retain: bool = False):
        """Publish message to MQTT topic."""
        if self._client and self._connected:
            result = self._client.publish(topic, payload, qos=qos, retain=retain)
            if result.rc != 0:
                logger.warning("MQTT publish failed: rc=%d", result.rc)
        else:
            raise ConnectionError("MQTT not connected")

    def publish_detection(self, camera_id: str, detection: dict):
        """Publish a detection event."""
        event = {
            "device_id": self.device_id,
            "camera_id": camera_id,
            "timestamp": time.time(),
            **detection,
        }
        self.publish(
            f"constructai/{self.device_id}/detections",
            json.dumps(event),
            qos=1,
        )

    def publish_health(self, health_data: dict):
        """Publish health metrics."""
        self.publish(
            f"constructai/{self.device_id}/health",
            json.dumps(health_data),
            qos=0,
        )

    def disconnect(self):
        """Disconnect from MQTT broker."""
        if self._client:
            self.publish(
                f"constructai/{self.device_id}/status",
                json.dumps({"status": "offline", "device_id": self.device_id}),
                qos=1,
                retain=True,
            )
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected
