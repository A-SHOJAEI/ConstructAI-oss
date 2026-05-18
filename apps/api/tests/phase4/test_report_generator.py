"""Tests for daily report generation."""

from __future__ import annotations

from datetime import date

from app.services.communication.report_generator import (
    generate_daily_report,
)
from tests.fixtures.sample_communication_data import (
    SAMPLE_DAILY_REPORT_INPUT,
)


class TestReportGenerator:
    async def test_basic_report(self):
        inp = SAMPLE_DAILY_REPORT_INPUT
        result = await generate_daily_report(
            project_id="test-project-1",
            report_date=inp["report_date"],
            daily_log=inp["daily_log"],
            evm_snapshot=inp["evm_snapshot"],
            safety_events=inp["safety_events"],
        )
        assert "content_markdown" in result
        assert "sections" in result
        assert result["status"] == "draft"

    async def test_report_markdown_content(self):
        result = await generate_daily_report(
            project_id="test-project-1",
            report_date=date(2024, 6, 15),
            daily_log={
                "crew_count": 30,
                "work_hours": "240",
                "activities_completed": [
                    {"description": "Poured slab"},
                ],
                "delays": [],
            },
        )
        md = result["content_markdown"]
        assert "Daily Construction Report" in md
        assert "Crew Count: 30" in md

    async def test_report_without_data(self):
        result = await generate_daily_report(
            project_id="test-project-1",
            report_date=date(2024, 6, 15),
        )
        assert result["content_markdown"] is not None
        sections = result["sections"]
        assert sections["workforce"]["crew_count"] == 0
        assert sections["progress"]["spi"] == "N/A"

    async def test_report_sections(self):
        result = await generate_daily_report(
            project_id="test-project-1",
            report_date=date(2024, 6, 15),
        )
        sections = result["sections"]
        assert "header" in sections
        assert "weather" in sections
        assert "workforce" in sections
        assert "progress" in sections
        assert "safety" in sections

    async def test_safety_events_in_report(self):
        events = [
            {"type": "near_miss", "description": "Test"},
        ]
        result = await generate_daily_report(
            project_id="test-project-1",
            report_date=date(2024, 6, 15),
            safety_events=events,
        )
        assert result["sections"]["safety"]["incidents"] == 1
