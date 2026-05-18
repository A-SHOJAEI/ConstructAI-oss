"""Tests for project controls LangGraph agent."""

from __future__ import annotations

from decimal import Decimal

from app.services.agents.controls_agent import (
    build_controls_agent,
    compute_evm_node,
    forecast_eac_node,
    run_controls_agent,
)


class TestControlsAgent:
    async def test_compute_evm_node(self):
        state = {
            "project_id": "test-1",
            "bac": "1000000",
            "pv": "500000",
            "ev": "450000",
            "ac": "480000",
            "activities": [],
            "evm_results": None,
            "eac_results": None,
            "risk_results": None,
            "scurve_results": None,
            "status": "processing",
            "error": None,
        }
        result = await compute_evm_node(state)
        assert result["evm_results"] is not None
        assert "spi" in result["evm_results"]
        assert "cpi" in result["evm_results"]

    async def test_forecast_eac_node(self):
        state = {
            "project_id": "test-1",
            "bac": "1000000",
            "pv": "500000",
            "ev": "450000",
            "ac": "480000",
            "activities": [],
            "evm_results": {
                "spi": Decimal("0.9"),
                "cpi": Decimal("0.9375"),
            },
            "eac_results": None,
            "risk_results": None,
            "scurve_results": None,
            "status": "evm_computed",
            "error": None,
        }
        result = await forecast_eac_node(state)
        assert result["eac_results"] is not None
        assert "cpi" in result["eac_results"]

    async def test_build_graph(self):
        graph = build_controls_agent()
        assert graph is not None

    async def test_run_full_agent(self):
        result = await run_controls_agent(
            project_id="test-project-1",
            bac=Decimal("1000000"),
            pv=Decimal("500000"),
            ev=Decimal("450000"),
            ac=Decimal("480000"),
        )
        assert result["status"] == "completed"
        assert result["evm_results"] is not None
        assert result["eac_results"] is not None

    async def test_agent_with_activities(self):
        activities = [
            {
                "id": "1",
                "name": "Task A",
                "duration_days": 20,
                "predecessors": [],
            },
            {
                "id": "2",
                "name": "Task B",
                "duration_days": 30,
                "predecessors": ["1"],
            },
        ]
        result = await run_controls_agent(
            project_id="test-project-1",
            activities=activities,
        )
        assert result["status"] == "completed"
        assert result["risk_results"] is not None
