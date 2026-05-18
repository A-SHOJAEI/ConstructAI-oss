"""Tests for Compliance Team supervisor."""

from __future__ import annotations

from app.services.agents.compliance_team import (
    _infer_task_type,
    analyze_node,
    build_compliance_team,
)


class TestComplianceTeam:
    def test_infer_quality(self):
        assert _infer_task_type("run quality inspection") == "quality"

    def test_infer_communication(self):
        assert _infer_task_type("generate daily report") == "communication"

    def test_infer_full(self):
        assert _infer_task_type("check everything") == "full"

    async def test_analyze_node(self):
        state = {
            "project_id": "test-1",
            "request": "run quality inspection for defects",
            "task_type": "auto",
            "quality_results": None,
            "communication_results": None,
            "human_feedback": None,
            "final_report": None,
            "status": "processing",
            "error": None,
        }
        result = await analyze_node(state)
        assert result["task_type"] == "quality"

    def test_build_graph(self):
        graph = build_compliance_team()
        assert graph is not None
