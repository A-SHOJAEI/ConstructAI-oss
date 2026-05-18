"""Phase 2: Monte Carlo simulation tests.

Tests for Monte Carlo cost estimation and sensitivity analysis.
Uses numpy internally but no external API calls.
"""

from __future__ import annotations

from app.services.estimating.monte_carlo import run_monte_carlo, sensitivity_analysis


class TestMonteCarlo:
    """Tests for the Monte Carlo simulation service."""

    async def test_run_simulation_returns_percentiles(self):
        """Simulation should return p50, p80, p90 percentiles in order."""
        line_items = [
            {"description": "Concrete", "quantity": 500, "unit_cost": 185.0},
            {"description": "Rebar", "quantity": 25, "unit_cost": 1250.0},
            {"description": "Formwork", "quantity": 2000, "unit_cost": 8.50},
        ]
        result = await run_monte_carlo(line_items, num_simulations=1000)
        assert "p50" in result
        assert "p80" in result
        assert "p90" in result
        assert result["p80"] > result["p50"]
        assert result["p90"] > result["p80"]

    async def test_monte_carlo_histogram_data(self):
        """Simulation should return histogram data for visualization."""
        line_items = [{"description": "Item", "quantity": 100, "unit_cost": 50.0}]
        result = await run_monte_carlo(line_items, num_simulations=500)
        assert "histogram_data" in result
        assert len(result["histogram_data"]) > 0
        # Histogram should have 50 bins
        assert len(result["histogram_data"]) == 50

    async def test_monte_carlo_empty_items(self):
        """Empty line items should return zeroed-out result."""
        result = await run_monte_carlo([], num_simulations=100)
        assert result["p50"] == 0.0
        assert result["mean"] == 0.0
        assert result["histogram_data"] == []

    async def test_monte_carlo_statistics(self):
        """Result should include mean and standard deviation."""
        line_items = [
            {"description": "Concrete", "quantity": 500, "unit_cost": 185.0},
        ]
        result = await run_monte_carlo(line_items, num_simulations=1000)
        assert "mean" in result
        assert "std_dev" in result
        # _to_money returns 2-decimal strings (monetary precision); compare
        # via float to keep the assertion intent.
        assert float(result["mean"]) > 0
        assert float(result["std_dev"]) > 0

    async def test_sensitivity_analysis(self):
        """Sensitivity analysis should return contribution percentages."""
        line_items = [
            {
                "description": "Concrete",
                "csi_code": "03 30 00",
                "quantity": 500,
                "unit_cost": 185.0,
            },
            {
                "description": "Steel",
                "csi_code": "05 12 00",
                "quantity": 25,
                "unit_cost": 3200.0,
            },
        ]
        result = await sensitivity_analysis(line_items, num_simulations=500)
        assert len(result) == 2
        assert all("contribution_pct" in item for item in result)
        # Contributions should sum to approximately 100%
        total_contribution = sum(item["contribution_pct"] for item in result)
        assert 95.0 <= total_contribution <= 105.0

    async def test_sensitivity_analysis_ranking(self):
        """Sensitivity results should be sorted by absolute correlation descending."""
        line_items = [
            {
                "description": "Concrete",
                "csi_code": "03 30 00",
                "quantity": 500,
                "unit_cost": 185.0,
            },
            {
                "description": "Steel",
                "csi_code": "05 12 00",
                "quantity": 25,
                "unit_cost": 3200.0,
            },
        ]
        result = await sensitivity_analysis(line_items, num_simulations=500)
        if len(result) >= 2:
            assert abs(result[0]["correlation_coefficient"]) >= abs(
                result[1]["correlation_coefficient"]
            )
