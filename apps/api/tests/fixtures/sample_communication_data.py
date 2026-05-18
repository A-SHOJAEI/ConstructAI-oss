"""Sample communication data for testing."""

from __future__ import annotations

from datetime import date

SAMPLE_DAILY_REPORT_INPUT = {
    "report_date": date(2024, 6, 15),
    "daily_log": {
        "crew_count": 45,
        "work_hours": "360",
        "activities_completed": [
            {"description": "Poured Level 3 slab"},
        ],
        "delays": [],
    },
    "evm_snapshot": {
        "percent_complete": "45.0",
        "spi": "0.90",
        "cpi": "0.94",
    },
    "safety_events": [],
}

SAMPLE_MEETING = {
    "meeting_type": "weekly_progress",
    "meeting_date": date(2024, 6, 15),
    "title": "Weekly Progress Meeting #24",
    "attendees": [
        {"name": "John Smith", "role": "Project Manager"},
        {"name": "Jane Doe", "role": "Superintendent"},
        {"name": "Bob Builder", "role": "Safety Manager"},
    ],
}

SAMPLE_RFI = {
    "rfi_number": "RFI-2024-042",
    "subject": "Column reinforcement detail at grid B3",
    "question": (
        'Drawing S-301 shows #8 bars at 6" OC for column B3, '
        "but specification section 03300 calls for #9 bars. "
        "Please clarify which is correct."
    ),
    "priority": "high",
}

SAMPLE_SUBMITTAL = {
    "submittal_number": "SUB-2024-018",
    "title": "Structural Steel Shop Drawings - Phase 2",
    "spec_section": "05120",
    "document_urls": [
        "s3://constructai-docs/submittals/steel-shop-dwgs.pdf",
    ],
}
