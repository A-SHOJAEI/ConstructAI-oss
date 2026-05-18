"""Mock response data for Phase 2 pre-construction agent tests."""

from __future__ import annotations

import json

# BLS PPI mock data
MOCK_BLS_PPI_DATA = {
    "series_id": "PCU236211236211",
    "latest_value": 295.3,
    "latest_period": "2025-12",
    "base_value": 268.1,
    "ppi_factor": 1.1014,
}

# FRED data mock
MOCK_FRED_DATA = [
    {"date": "2024-01-01", "value": 280.5},
    {"date": "2024-02-01", "value": 282.1},
    {"date": "2024-03-01", "value": 283.8},
    {"date": "2024-04-01", "value": 285.2},
    {"date": "2024-05-01", "value": 286.9},
    {"date": "2024-06-01", "value": 288.0},
    {"date": "2024-07-01", "value": 289.5},
    {"date": "2024-08-01", "value": 290.2},
    {"date": "2024-09-01", "value": 291.8},
    {"date": "2024-10-01", "value": 292.5},
    {"date": "2024-11-01", "value": 293.7},
    {"date": "2024-12-01", "value": 295.3},
]

# Mock IFC data for quantity extraction
MOCK_IFC_DATA = {
    "elements": [
        {
            "id": "wall-001",
            "type": "IfcWall",
            "properties": {"material": "Concrete"},
            "quantities": {"volume": 25.5, "area": 120.0},
        },
        {
            "id": "col-001",
            "type": "IfcColumn",
            "properties": {"material": "Concrete"},
            "quantities": {"volume": 3.2, "count": 12},
        },
        {
            "id": "slab-001",
            "type": "IfcSlab",
            "properties": {"material": "Concrete"},
            "quantities": {"volume": 180.0, "area": 5400.0},
        },
        {
            "id": "beam-001",
            "type": "IfcBeam",
            "properties": {"material": "Steel"},
            "quantities": {"length": 450.0, "weight_tons": 15.5},
        },
        {
            "id": "door-001",
            "type": "IfcDoor",
            "properties": {"material": "Wood"},
            "quantities": {"count": 24},
        },
        {
            "id": "window-001",
            "type": "IfcWindow",
            "properties": {"material": "Aluminum"},
            "quantities": {"count": 36, "area": 72.0},
        },
    ]
}

# Mock schedule activities
MOCK_SCHEDULE_ACTIVITIES = [
    {"id": "A", "name": "Site Preparation", "duration_days": 10, "predecessors": []},
    {"id": "B", "name": "Foundation", "duration_days": 20, "predecessors": ["A"]},
    {"id": "C", "name": "Structural Steel", "duration_days": 30, "predecessors": ["B"]},
    {"id": "D", "name": "MEP Rough-in", "duration_days": 25, "predecessors": ["B"]},
    {"id": "E", "name": "Exterior Envelope", "duration_days": 20, "predecessors": ["C"]},
    {"id": "F", "name": "Interior Framing", "duration_days": 15, "predecessors": ["C", "D"]},
    {"id": "G", "name": "Finishes", "duration_days": 25, "predecessors": ["E", "F"]},
    {"id": "H", "name": "Commissioning", "duration_days": 10, "predecessors": ["G"]},
]

# Mock weather data
MOCK_WEATHER_DATA = [
    {
        "date": "2025-03-01",
        "temperature_max": 55,
        "temperature_min": 35,
        "precipitation_mm": 0,
        "wind_speed_max": 15,
        "weather_code": 0,
    },
    {
        "date": "2025-03-02",
        "temperature_max": 48,
        "temperature_min": 30,
        "precipitation_mm": 12,
        "wind_speed_max": 25,
        "weather_code": 61,
    },
    {
        "date": "2025-03-03",
        "temperature_max": 52,
        "temperature_min": 33,
        "precipitation_mm": 0,
        "wind_speed_max": 10,
        "weather_code": 0,
    },
    {
        "date": "2025-03-04",
        "temperature_max": 25,
        "temperature_min": 15,
        "precipitation_mm": 5,
        "wind_speed_max": 35,
        "weather_code": 71,
    },
    {
        "date": "2025-03-05",
        "temperature_max": 60,
        "temperature_min": 42,
        "precipitation_mm": 0,
        "wind_speed_max": 8,
        "weather_code": 0,
    },
]

# Mock site facilities for optimization
MOCK_FACILITIES = [
    {
        "id": "office",
        "name": "Site Office",
        "type": "admin",
        "width": 12,
        "length": 6,
        "fixed": True,
        "x": 5,
        "y": 5,
    },
    {
        "id": "crane",
        "name": "Tower Crane",
        "type": "equipment",
        "width": 4,
        "length": 4,
        "fixed": True,
        "x": 50,
        "y": 50,
    },
    {
        "id": "storage",
        "name": "Material Storage",
        "type": "storage",
        "width": 20,
        "length": 10,
        "fixed": False,
    },
    {
        "id": "parking",
        "name": "Worker Parking",
        "type": "parking",
        "width": 30,
        "length": 15,
        "fixed": False,
    },
    {
        "id": "waste",
        "name": "Waste Area",
        "type": "hazardous",
        "width": 8,
        "length": 8,
        "fixed": False,
    },
]

MOCK_SITE_BOUNDARY = {"width": 100, "length": 100, "exclusion_zones": []}

# Mock deliveries for routing
MOCK_DELIVERIES = [
    {
        "id": "d1",
        "location": {"lat": 40.7128, "lng": -74.0060},
        "demand_units": 5,
        "time_window": {"start": "08:00", "end": "12:00"},
        "duration_minutes": 30,
    },
    {
        "id": "d2",
        "location": {"lat": 40.7580, "lng": -73.9855},
        "demand_units": 3,
        "time_window": {"start": "09:00", "end": "15:00"},
        "duration_minutes": 20,
    },
    {
        "id": "d3",
        "location": {"lat": 40.7484, "lng": -73.9857},
        "demand_units": 8,
        "time_window": {"start": "07:00", "end": "11:00"},
        "duration_minutes": 45,
    },
]

MOCK_VEHICLES = [
    {"id": "v1", "capacity_units": 15, "cost_per_km": 2.5, "max_distance_km": 100},
    {"id": "v2", "capacity_units": 10, "cost_per_km": 2.0, "max_distance_km": 80},
]

MOCK_DEPOT = {
    "location": {"lat": 40.6892, "lng": -74.0445},
    "open_time": "06:00",
    "close_time": "18:00",
}

# Mock LLM responses
MOCK_LLM_QUANTITY_RESPONSE = json.dumps(
    {
        "quantities": [
            {
                "description": "Ready-mix concrete 4000 PSI",
                "quantity": 850,
                "unit": "CY",
                "csi_code": "03 30 00",
                "confidence": 0.9,
            },
            {
                "description": "Reinforcing steel",
                "quantity": 45,
                "unit": "TON",
                "csi_code": "03 20 00",
                "confidence": 0.85,
            },
        ]
    }
)

MOCK_LLM_CONTRACT_RISK_RESPONSE = json.dumps(
    {
        "overall_risk_score": 65,
        "risk_items": [
            {
                "clause": "Section 8.1 - Liquidated Damages",
                "risk_type": "liquidated_damages",
                "severity": "high",
                "explanation": "LD rate of $5000/day is above market average",
                "mitigation": "Negotiate cap on total LDs",
            },
            {
                "clause": "Section 12.3 - Indemnification",
                "risk_type": "indemnification",
                "severity": "medium",
                "explanation": "Broad indemnification clause",
                "mitigation": "Limit to negligent acts",
            },
        ],
        "recommendations": [
            "Negotiate LD cap",
            "Clarify scope boundaries",
            "Add dispute resolution timeline",
        ],
    }
)

MOCK_LLM_CONTRACT_COMPARISON_RESPONSE = json.dumps(
    {
        "comparison": [
            {
                "topic": "Payment Terms",
                "contract_a_terms": "Net 30",
                "contract_b_terms": "Net 45",
                "risk_difference": "Contract B has slower payment",
            },
        ],
        "recommendation": "Contract A has more favorable terms overall",
    }
)

# Mock simulation scenario
MOCK_SIMULATION_SCENARIO = {
    "resources": {"cranes": 2, "trucks": 5, "crews": 8},
    "tasks": [
        {
            "name": "concrete_pour",
            "duration_hours": 4,
            "resources_needed": {"cranes": 1, "crews": 2},
            "priority": 1,
        },
        {
            "name": "steel_erection",
            "duration_hours": 6,
            "resources_needed": {"cranes": 1, "crews": 3},
            "priority": 2,
        },
        {
            "name": "material_delivery",
            "duration_hours": 2,
            "resources_needed": {"trucks": 1},
            "priority": 3,
        },
    ],
    "arrival_rate": 5.0,
}

# Historical price data for forecasting
MOCK_PRICE_HISTORY = [
    {"date": f"2024-{m:02d}-01", "price_index": 280 + m * 1.2 + (m % 3) * 0.5} for m in range(1, 13)
]
