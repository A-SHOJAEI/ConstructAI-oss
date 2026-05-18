"""Tests for safety incident response workflow."""

from __future__ import annotations

from app.services.orchestration.workflows.safety_incident_response import (
    run_safety_incident_response,
)


class TestSafetyIncidentWorkflow:
    async def test_basic_incident(self):
        result = await run_safety_incident_response(
            project_id="test-p1",
            incident_data={
                "type": "fall_hazard",
                "severity": "high",
                "location": "Zone A",
            },
        )
        assert result["status"] == "completed"
        assert len(result["steps_completed"]) == 5

    async def test_critical_severity_notifications(self):
        result = await run_safety_incident_response(
            project_id="test-p1",
            incident_data={
                "type": "structural_collapse",
                "severity": "critical",
            },
        )
        recipients = result["notifications"]["recipients"]
        assert "safety_manager" in recipients
        assert "project_manager" in recipients
        assert "osha_liaison" in recipients

    async def test_low_severity_notifications(self):
        result = await run_safety_incident_response(
            project_id="test-p1",
            incident_data={
                "type": "near_miss",
                "severity": "low",
            },
        )
        recipients = result["notifications"]["recipients"]
        assert "safety_manager" in recipients
        assert "osha_liaison" not in recipients

    async def test_schedule_impact_critical(self):
        result = await run_safety_incident_response(
            project_id="test-p1",
            incident_data={"severity": "critical"},
        )
        impact = result["schedule_impact"]
        assert impact["work_stoppage_hours"] == 24

    async def test_incident_report_generated(self):
        result = await run_safety_incident_response(
            project_id="test-p1",
            incident_data={
                "type": "fall_hazard",
                "severity": "medium",
            },
        )
        report = result["report"]
        assert "Fall Hazard" in report["title"]
        assert report["status"] == "generated"
