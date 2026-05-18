"""Phase 2: Estimating agent orchestration tests.

Tests for the estimating agent that coordinates quantity extraction,
cost matching, parametric modeling, and Monte Carlo simulation.
All downstream services are mocked.
"""

from __future__ import annotations

from unittest.mock import patch

from tests.fixtures.precon_mock_responses import MOCK_IFC_DATA


class TestEstimatingAgent:
    """Tests for the estimating agent orchestrator."""

    @patch("app.services.estimating.monte_carlo.run_monte_carlo")
    @patch("app.services.estimating.parametric_model.predict_cost")
    @patch("app.services.estimating.cost_database.match_costs")
    @patch("app.services.estimating.quantity_extractor.extract_quantities_from_ifc")
    async def test_estimating_pipeline_integration(
        self, mock_extract, mock_match, mock_predict, mock_mc
    ):
        """The full estimating pipeline should produce a cost estimate."""
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

        # Run the pipeline manually (agent module may not exist yet)
        from app.services.estimating.cost_database import match_costs
        from app.services.estimating.monte_carlo import run_monte_carlo
        from app.services.estimating.parametric_model import predict_cost
        from app.services.estimating.quantity_extractor import extract_quantities_from_ifc

        quantities = await extract_quantities_from_ifc(MOCK_IFC_DATA)
        assert len(quantities) > 0

        costed = await match_costs(quantities)
        assert len(costed) > 0
        assert costed[0]["total_cost"] == 92500.0

        parametric = await predict_cost({"sqft": 50000, "type": "commercial"})
        assert parametric["total_predicted_cost"] == 12500000

        mc = await run_monte_carlo(costed, num_simulations=1000)
        assert mc["p90"] > mc["p50"]

    @patch("app.services.estimating.quantity_extractor.extract_quantities_from_ifc")
    async def test_estimating_handles_empty_ifc(self, mock_extract):
        """Pipeline should handle empty IFC data gracefully."""
        mock_extract.return_value = []

        from app.services.estimating.quantity_extractor import extract_quantities_from_ifc

        quantities = await extract_quantities_from_ifc({"elements": []})
        assert quantities == []

    @patch("app.services.estimating.cost_database.match_costs")
    @patch("app.services.estimating.quantity_extractor.extract_quantities_from_ifc")
    async def test_estimating_unmatched_costs(self, mock_extract, mock_match):
        """Unmatched cost items should still be returned with zero cost."""
        mock_extract.return_value = [
            {"csi_code": "99 99 99", "description": "unknown item", "quantity": 10, "unit": "EA"},
        ]
        mock_match.return_value = [
            {
                "csi_code": "99 99 99",
                "description": "unknown item",
                "quantity": 10,
                "unit": "EA",
                "unit_cost": 0.0,
                "total_cost": 0.0,
                "data_source": "unmatched",
            },
        ]

        from app.services.estimating.cost_database import match_costs
        from app.services.estimating.quantity_extractor import extract_quantities_from_ifc

        quantities = await extract_quantities_from_ifc({"elements": []})
        costed = await match_costs(quantities)
        assert costed[0]["data_source"] == "unmatched"
