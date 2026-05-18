"""Mock documents for onboarding workflow tests."""

from __future__ import annotations

SAMPLE_ONBOARDING_INPUT = {
    "project_id": "test-project-onboarding",
    "document_ids": [
        "doc-spec-001",
        "doc-drawing-001",
        "doc-schedule-001",
    ],
    "project_name": "Test Commercial Office Building",
    "project_type": "commercial_office",
    "location": {
        "address": "123 Main St",
        "city": "Austin",
        "state": "TX",
    },
}

SAMPLE_CLASSIFIED_DOCUMENTS = [
    {
        "document_id": "doc-spec-001",
        "type": "specification",
        "title": "Project Specifications",
        "csi_divisions": ["03", "05", "09"],
        "confidence": 0.95,
    },
    {
        "document_id": "doc-drawing-001",
        "type": "drawing",
        "title": "Architectural Drawings",
        "discipline": "architectural",
        "sheet_count": 45,
        "confidence": 0.92,
    },
    {
        "document_id": "doc-schedule-001",
        "type": "schedule",
        "title": "Master Schedule",
        "format": "mpp",
        "activities_count": 230,
        "confidence": 0.88,
    },
]

SAMPLE_CHANGE_ORDER_INPUT = {
    "description": "Foundation redesign due to soil conditions",
    "type": "design_error",
    "cost_impact": 150000,
    "schedule_impact_days": 14,
    "original_contract": 5000000,
    "submitted_by": "structural_engineer",
}

SAMPLE_SAFETY_INCIDENT_INPUT = {
    "type": "fall_hazard",
    "severity": "high",
    "location": "Zone A - Level 3",
    "description": ("Worker observed without fall protection near open floor edge"),
    "reporter": "safety_officer",
    "witnesses": ["foreman_jones", "superintendent_smith"],
}
