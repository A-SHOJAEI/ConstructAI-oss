"""Equipment telemetry data ingestion (ISO 15143-3)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

logger = logging.getLogger(__name__)


async def parse_iso15143_payload(
    raw_payload: dict,
) -> dict:
    """Parse ISO 15143-3 AEMP 2.0 telematics payload.

    Parameters
    ----------
    raw_payload: Raw telematics data from equipment

    Returns normalized telemetry dict.
    """
    equipment_id = raw_payload.get(
        "equipmentId",
        raw_payload.get("EquipmentID", "unknown"),
    )
    equipment_type = raw_payload.get(
        "equipmentType",
        raw_payload.get("EquipmentType", "unknown"),
    )

    # Parse engine hours
    engine_hours = _safe_decimal(raw_payload.get("CumulativeOperatingHours", {}).get("Hour"))

    # Parse fuel consumption
    fuel = _safe_decimal(raw_payload.get("FuelUsed", {}).get("FuelConsumed"))

    # Parse idle time
    idle_hours = _safe_decimal(raw_payload.get("CumulativeIdleHours", {}).get("Hour"))

    # Calculate utilization
    utilization_pct = None
    if engine_hours and idle_hours and engine_hours > 0:
        active = engine_hours - idle_hours
        utilization_pct = round(active / engine_hours * Decimal("100"), 2)

    # Parse location
    location = raw_payload.get("Location", {})
    location_data = {}
    if location:
        location_data = {
            "latitude": location.get("Latitude"),
            "longitude": location.get("Longitude"),
            "altitude": location.get("Altitude"),
        }

    # Parse timestamp
    ts_str = raw_payload.get(
        "DateTime",
        raw_payload.get("timestamp"),
    )
    timestamp = datetime.fromisoformat(ts_str) if ts_str else datetime.now(UTC)

    result = {
        "equipment_id": equipment_id,
        "equipment_type": equipment_type,
        "timestamp": timestamp,
        "engine_hours": engine_hours,
        "fuel_consumption": fuel,
        "idle_time_hours": idle_hours,
        "utilization_pct": utilization_pct,
        "location_data": location_data,
        "raw_payload": raw_payload,
    }

    logger.info(
        "Telemetry parsed for %s: utilization=%.1f%%",
        equipment_id,
        float(utilization_pct or 0),
    )
    return result


def _safe_decimal(value) -> Decimal | None:
    """Safely convert value to Decimal."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None
