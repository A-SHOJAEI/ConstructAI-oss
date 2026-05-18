"""Test zone polygon definitions."""

from __future__ import annotations

MOCK_RESTRICTED_ZONE = {
    "id": "zone-restricted-001",
    "zone_type": "restricted",
    "polygon_points": [[100, 100], [300, 100], [300, 400], [100, 400]],
    "ppe_requirements": [],
    "severity_override": None,
}

MOCK_PPE_ZONE = {
    "id": "zone-ppe-001",
    "zone_type": "ppe_required",
    "polygon_points": [[0, 0], [640, 0], [640, 640], [0, 640]],
    "ppe_requirements": ["hardhat", "vest"],
    "severity_override": None,
}

MOCK_CRANE_ZONE = {
    "id": "zone-crane-001",
    "zone_type": "crane_swing",
    "polygon_points": [[200, 200], [400, 200], [400, 400], [200, 400]],
    "ppe_requirements": [],
    "severity_override": None,
}

MOCK_EQUIPMENT_ZONE = {
    "id": "zone-equip-001",
    "zone_type": "equipment_only",
    "polygon_points": [[50, 50], [150, 50], [150, 150], [50, 150]],
    "ppe_requirements": [],
    "severity_override": None,
}

ALL_TEST_ZONES = [MOCK_RESTRICTED_ZONE, MOCK_PPE_ZONE, MOCK_CRANE_ZONE, MOCK_EQUIPMENT_ZONE]
