"""Sample productivity data for testing."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

SAMPLE_DAILY_LOG = {
    "log_date": date(2024, 6, 15),
    "weather": {
        "conditions": "Clear",
        "temperature_f": 78,
        "precipitation": "None",
        "wind_mph": 5,
    },
    "crew_count": 45,
    "work_hours": Decimal("360"),
    "activities_completed": [
        {"description": "Poured Level 3 slab section A", "trade": "concrete"},
        {"description": "Installed rebar grid Level 4", "trade": "ironworker"},
        {"description": "Erected formwork columns B1-B5", "trade": "carpenter"},
    ],
    "delays": [
        {"type": "material", "duration_hours": 1.5, "description": "Late rebar delivery"},
    ],
    "notes": "Good progress overall. Rebar delivery delayed by 1.5 hours.",
}

SAMPLE_CREW_PRODUCTIVITY = {
    "trade": "concrete",
    "crew_size": 8,
    "work_date": date(2024, 6, 15),
    "planned_units": Decimal("150"),
    "actual_units": Decimal("142"),
    "unit_of_measure": "cubic_yards",
    "conditions": {"weather": "clear", "temperature_f": 78},
}

SAMPLE_TELEMETRY_ISO15143 = {
    "equipmentId": "CAT-336F-001",
    "EquipmentType": "excavator",
    "DateTime": "2024-06-15T14:30:00+00:00",
    "CumulativeOperatingHours": {"Hour": 4521.5},
    "FuelUsed": {"FuelConsumed": 15.3},
    "CumulativeIdleHours": {"Hour": 1205.2},
    "Location": {
        "Latitude": 34.0522,
        "Longitude": -118.2437,
        "Altitude": 71.0,
    },
}

HISTORICAL_PRODUCTIVITY = [
    {
        "work_date": date(2024, 6, i),
        "actual_units": float(130 + i * 2),
        "planned_units": 150.0,
        "crew_size": 8,
    }
    for i in range(1, 15)
]
