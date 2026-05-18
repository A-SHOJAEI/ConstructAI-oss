"""Tests for Execution Team supervisor."""

from __future__ import annotations

from app.services.agents.execution_team import (
    _infer_task_type,
    analyze_node,
    build_execution_team,
)


class TestExecutionTeam:
    def test_infer_controls(self):
        assert _infer_task_type("review EVM metrics") == "controls"

    def test_infer_productivity(self):
        assert _infer_task_type("check crew productivity") == "productivity"

    def test_infer_full(self):
        assert _infer_task_type("general review") == "full"

    async def test_analyze_node(self):
        state = {
            "project_id": "test-1",
            "request": "check earned value",
            "task_type": "auto",
            "controls_results": None,
            "productivity_results": None,
            "human_feedback": None,
            "final_report": None,
            "status": "processing",
            "error": None,
        }
        result = await analyze_node(state)
        assert result["task_type"] == "controls"

    def test_build_graph(self):
        graph = build_execution_team()
        assert graph is not None
