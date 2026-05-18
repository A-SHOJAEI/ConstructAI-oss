"""Mock detection results for Phase 3 tests."""

from __future__ import annotations

MOCK_PERSON_DETECTION = {
    "class_name": "person",
    "confidence": 0.92,
    "bbox": [100, 150, 200, 400],
    "track_id": 1,
    "attributes": {"ppe": {"hardhat": True, "vest": True}},
}

MOCK_PERSON_NO_HARDHAT = {
    "class_name": "person",
    "confidence": 0.88,
    "bbox": [300, 200, 400, 450],
    "track_id": 2,
    "attributes": {"ppe": {"hardhat": False, "vest": True}},
}

MOCK_PERSON_NO_VEST = {
    "class_name": "person",
    "confidence": 0.85,
    "bbox": [500, 100, 600, 350],
    "track_id": 3,
    "attributes": {"ppe": {"hardhat": True, "vest": False}},
}

MOCK_TRUCK_DETECTION = {
    "class_name": "truck",
    "confidence": 0.95,
    "bbox": [50, 50, 250, 200],
    "track_id": 4,
    "attributes": {},
}

MOCK_DETECTIONS_LIST = [
    MOCK_PERSON_DETECTION,
    MOCK_PERSON_NO_HARDHAT,
    MOCK_PERSON_NO_VEST,
    MOCK_TRUCK_DETECTION,
]

MOCK_SAFETY_EVENT = {
    "camera_id": "cam-001",
    "project_id": "project-001",
    "detection": MOCK_PERSON_NO_HARDHAT,
    "violation": {
        "zone_id": "zone-001",
        "zone_type": "ppe_required",
        "violation": "missing_hardhat",
        "severity_override": None,
    },
    "timestamp": "2025-03-15T10:30:00Z",
}
