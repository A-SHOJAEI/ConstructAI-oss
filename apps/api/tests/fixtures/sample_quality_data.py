"""Sample quality data for testing."""

from __future__ import annotations

SAMPLE_INSPECTION = {
    "inspection_type": "concrete_placement",
    "location": "Level 3 - Column Grid A1-A5",
    "checklist_data": {
        "slump_test": {"required": True, "passed": True, "value": "4 inches"},
        "air_content": {"required": True, "passed": True, "value": "5.5%"},
        "temperature": {"required": True, "passed": True, "value": "72F"},
        "rebar_placement": {"required": True, "passed": False, "note": "Spacing off by 1 inch"},
    },
}

SAMPLE_DEFECT = {
    "defect_type": "crack_structural",
    "severity": "major",
    "description": "Vertical crack observed in column C3, approximately 2mm wide.",
    "location": "Level 2 - Column C3",
    "image_urls": ["s3://constructai-images/defect-001.jpg"],
}

SAMPLE_NCR = {
    "ncr_number": "NCR-2024-001",
    "title": "Concrete strength below specification",
    "description": (
        "28-day cylinder break test shows 3800 PSI, specification requires 4000 PSI minimum."
    ),
    "severity": "major",
}

OSHA_TEST_REGULATIONS = [
    "1926.451",
    "1926.501",
    "1926.100",
]
