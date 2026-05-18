from __future__ import annotations

from app.services.agents.safety_agent import run_safety_agent
from tests.fixtures.mock_detections import MOCK_SAFETY_EVENT


class TestSafetyAgent:
    async def test_agent_enriches_context(self):
        result = await run_safety_agent(
            project_id="proj-1",
            detection_events=[MOCK_SAFETY_EVENT],
            schedule_phase="foundation",
        )
        assert result["status"] == "completed"
        assert len(result["alerts_generated"]) > 0

    async def test_agent_classifies_severity(self):
        result = await run_safety_agent(
            project_id="proj-1",
            detection_events=[MOCK_SAFETY_EVENT],
        )
        for alert in result["alerts_generated"]:
            assert "priority" in alert
            assert alert["priority"].startswith("P")

    async def test_agent_handles_empty_events(self):
        result = await run_safety_agent(
            project_id="proj-1",
            detection_events=[],
        )
        assert result["status"] == "completed"
        assert len(result["alerts_generated"]) == 0
