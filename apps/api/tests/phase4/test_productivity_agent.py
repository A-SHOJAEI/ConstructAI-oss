"""Tests for productivity LangGraph agent."""

from __future__ import annotations

from app.services.agents.productivity_agent import (
    analyze_equipment_node,
    build_productivity_agent,
    forecast_node,
    run_productivity_agent,
)
from tests.fixtures.sample_productivity_data import (
    HISTORICAL_PRODUCTIVITY,
)


class TestProductivityAgent:
    async def test_forecast_node(self):
        state = {
            "project_id": "test-1",
            "frames": [],
            "historical_data": HISTORICAL_PRODUCTIVITY,
            "trade": "concrete",
            "telemetry_data": [],
            "activity_results": None,
            "forecast_results": None,
            "equipment_analysis": None,
            "status": "activity_recognized",
            "error": None,
        }
        result = await forecast_node(state)
        assert result["forecast_results"] is not None
        assert result["forecast_results"]["trade"] == "concrete"

    async def test_equipment_node_no_data(self):
        state = {
            "project_id": "test-1",
            "frames": [],
            "historical_data": [],
            "trade": "general",
            "telemetry_data": [],
            "activity_results": None,
            "forecast_results": None,
            "equipment_analysis": None,
            "status": "forecast_complete",
            "error": None,
        }
        result = await analyze_equipment_node(state)
        assert "No telemetry" in (result["equipment_analysis"]["summary"])

    async def test_equipment_node_with_data(self):
        state = {
            "project_id": "test-1",
            "frames": [],
            "historical_data": [],
            "trade": "general",
            "telemetry_data": [
                {
                    "equipment_id": "EX-001",
                    "engine_hours": 100,
                    "idle_time_hours": 20,
                    "fuel_consumption": 50,
                },
            ],
            "activity_results": None,
            "forecast_results": None,
            "equipment_analysis": None,
            "status": "forecast_complete",
            "error": None,
        }
        result = await analyze_equipment_node(state)
        analysis = result["equipment_analysis"]
        assert analysis["utilization_pct"] == 80.0

    async def test_build_graph(self):
        graph = build_productivity_agent()
        assert graph is not None

    async def test_run_full_agent(self):
        result = await run_productivity_agent(
            project_id="test-project-1",
            historical_data=HISTORICAL_PRODUCTIVITY,
            trade="concrete",
        )
        assert result["status"] == "completed"
        assert result["forecast_results"] is not None
