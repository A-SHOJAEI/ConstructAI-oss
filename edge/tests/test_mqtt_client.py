"""Tests for the edge MQTT client."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

mqtt = pytest.importorskip("paho.mqtt.client")

from edge.src.mqtt_client import EdgeMQTTClient  # noqa: E402


class TestEdgeMQTTClient:
    def test_init(self):
        client = EdgeMQTTClient(
            host="test-host",
            port=1883,
            device_id="test-001",
        )
        assert client.host == "test-host"
        assert client.device_id == "test-001"
        assert client.is_connected is False

    @patch("edge.src.mqtt_client.mqtt.Client")
    def test_connect(self, mock_client_class):
        mock_instance = MagicMock()
        mock_client_class.return_value = mock_instance

        client = EdgeMQTTClient(device_id="test-001")
        client.connect()

        mock_instance.connect.assert_called_once()
        mock_instance.loop_start.assert_called_once()

    def test_disconnect_without_connect(self):
        client = EdgeMQTTClient(device_id="test-001")
        # Should not raise
        client.disconnect()
