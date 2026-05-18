"""Tests for telemetry ingestion."""

from __future__ import annotations

from decimal import Decimal

from app.services.productivity.telemetry_ingestor import (
    parse_iso15143_payload,
)
from tests.fixtures.sample_productivity_data import (
    SAMPLE_TELEMETRY_ISO15143,
)


class TestTelemetryIngestor:
    async def test_parse_iso15143(self):
        result = await parse_iso15143_payload(
            SAMPLE_TELEMETRY_ISO15143,
        )
        assert result["equipment_id"] == "CAT-336F-001"
        assert result["equipment_type"] == "excavator"
        assert result["engine_hours"] == Decimal("4521.5")
        assert result["fuel_consumption"] == Decimal("15.3")

    async def test_utilization_calculation(self):
        result = await parse_iso15143_payload(
            SAMPLE_TELEMETRY_ISO15143,
        )
        assert result["utilization_pct"] is not None
        assert result["utilization_pct"] > Decimal("0")
        assert result["utilization_pct"] < Decimal("100")

    async def test_location_data(self):
        result = await parse_iso15143_payload(
            SAMPLE_TELEMETRY_ISO15143,
        )
        loc = result["location_data"]
        assert loc["latitude"] == 34.0522
        assert loc["longitude"] == -118.2437

    async def test_empty_payload(self):
        result = await parse_iso15143_payload({})
        assert result["equipment_id"] == "unknown"
        assert result["engine_hours"] is None

    async def test_partial_payload(self):
        payload = {
            "equipmentId": "TEST-001",
            "EquipmentType": "loader",
            "DateTime": "2024-06-15T10:00:00+00:00",
        }
        result = await parse_iso15143_payload(payload)
        assert result["equipment_id"] == "TEST-001"
        assert result["fuel_consumption"] is None
        assert result["utilization_pct"] is None
