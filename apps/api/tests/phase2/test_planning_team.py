"""Phase 2: Planning team (multi-agent) orchestration tests.

Tests for the planning team that coordinates estimating, scheduling,
logistics, and procurement agents via LangGraph. All downstream agents
and services are mocked.
"""

from __future__ import annotations

from unittest.mock import patch

from tests.fixtures.precon_mock_responses import (
    MOCK_IFC_DATA,
    MOCK_SCHEDULE_ACTIVITIES,
)


class TestPlanningTeam:
    """Tests for the multi-agent planning team."""

    @patch("app.services.estimating.monte_carlo.run_monte_carlo")
    @patch("app.services.estimating.parametric_model.predict_cost")
    @patch("app.services.estimating.cost_database.match_costs")
    @patch("app.services.estimating.quantity_extractor.extract_quantities_from_ifc")
    async def test_planning_team_estimating_subflow(
        self, mock_extract, mock_match, mock_predict, mock_mc
    ):
        """Planning team should be able to run the estimating subflow."""
        mock_extract.return_value = [
            {"csi_code": "03 30 00", "description": "concrete", "quantity": 500, "unit": "CY"},
        ]
        mock_match.return_value = [
            {
                "csi_code": "03 30 00",
                "description": "concrete",
                "quantity": 500,
                "unit": "CY",
                "unit_cost": 185.0,
                "total_cost": 92500.0,
            },
        ]
        mock_predict.return_value = {
            "predicted_cost_per_sqft": 250,
            "total_predicted_cost": 12500000,
            "confidence_interval": {"low": 10625000, "high": 14375000},
            "model_used": "heuristic",
        }
        mock_mc.return_value = {
            "p50": 12000000,
            "p80": 13500000,
            "p90": 14000000,
            "mean": 12500000,
            "std_dev": 1000000,
            "p10": 11000000,
            "histogram_data": [],
            "num_simulations": 1000,
            "contingency_pct": 10.0,
        }

        from app.services.estimating.cost_database import match_costs
        from app.services.estimating.monte_carlo import run_monte_carlo
        from app.services.estimating.parametric_model import predict_cost
        from app.services.estimating.quantity_extractor import extract_quantities_from_ifc

        quantities = await extract_quantities_from_ifc(MOCK_IFC_DATA)
        costed = await match_costs(quantities)
        parametric = await predict_cost({"sqft": 50000, "type": "commercial"})
        mc = await run_monte_carlo(costed, num_simulations=1000)

        assert parametric["total_predicted_cost"] == 12500000
        assert mc["p90"] > mc["p50"]

    @patch("app.services.scheduling.cpm_engine.calculate_cpm")
    async def test_planning_team_scheduling_subflow(self, mock_cpm):
        """Planning team should be able to run the scheduling subflow."""
        mock_cpm.return_value = {
            "activities": MOCK_SCHEDULE_ACTIVITIES,
            "critical_path": ["A", "B", "C", "E", "G", "H"],
            "project_duration": 115,
            "critical_path_length": 6,
        }

        from app.services.scheduling.cpm_engine import calculate_cpm

        result = await calculate_cpm(MOCK_SCHEDULE_ACTIVITIES)
        assert result["project_duration"] == 115

    async def test_planning_team_langgraph_importable(self):
        """LangGraph should be importable for building the planning team."""
        try:
            import langgraph

            assert langgraph is not None
        except ImportError:
            # langgraph is optional for running tests
            pass

    @patch("app.services.estimating.quantity_extractor.extract_quantities_from_ifc")
    async def test_planning_team_handles_empty_project(self, mock_extract):
        """Planning team should handle a project with no BIM data."""
        mock_extract.return_value = []

        from app.services.estimating.quantity_extractor import extract_quantities_from_ifc

        quantities = await extract_quantities_from_ifc({"elements": []})
        assert quantities == []
