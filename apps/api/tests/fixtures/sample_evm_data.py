"""Sample EVM data for testing."""

from __future__ import annotations

from decimal import Decimal

SAMPLE_EVM_INPUT = {
    "bac": Decimal("1000000"),
    "pv": Decimal("500000"),
    "ev": Decimal("450000"),
    "ac": Decimal("480000"),
}

EXPECTED_EVM_METRICS = {
    "sv": Decimal("-50000.00"),
    "cv": Decimal("-30000.00"),
    "spi": Decimal("0.9000"),
    "cpi": Decimal("0.9375"),
    "eac": Decimal("1066666.67"),
    "etc": Decimal("586666.67"),
    "vac": Decimal("-66666.67"),
    "percent_complete": Decimal("45.00"),
}

SAMPLE_CHANGE_ORDER = {
    "co_number": "CO-001",
    "title": "Foundation redesign due to soil conditions",
    "description": "Unexpected soil conditions require deeper foundations.",
    "change_type": "field_condition",
    "cost_impact": Decimal("150000"),
    "schedule_impact_days": 21,
}

SAMPLE_SCHEDULE_ACTIVITIES = [
    {
        "id": "1",
        "name": "Mobilization",
        "duration_days": 10,
        "predecessors": [],
    },
    {
        "id": "2",
        "name": "Excavation",
        "duration_days": 15,
        "predecessors": ["1"],
    },
    {
        "id": "3",
        "name": "Foundation",
        "duration_days": 25,
        "predecessors": ["2"],
    },
    {
        "id": "4",
        "name": "Structure",
        "duration_days": 60,
        "predecessors": ["3"],
    },
    {
        "id": "5",
        "name": "Finishes",
        "duration_days": 45,
        "predecessors": ["4"],
    },
]
