"""Phase 2: Parametric cost model tests.

Tests for the heuristic-based parametric cost estimation. No external
services are called since the model uses internal lookup tables.
"""

from __future__ import annotations

from app.services.estimating.parametric_model import predict_cost


class TestParametricModel:
    """Tests for the parametric cost prediction model."""

    async def test_predict_cost_commercial(self):
        """Should return valid cost prediction for a commercial building."""
        result = await predict_cost(
            {
                "sqft": 50000,
                "stories": 3,
                "type": "commercial",
                "region": "national",
                "quality_level": "standard",
            }
        )
        assert "predicted_cost_per_sqft" in result
        assert "total_predicted_cost" in result
        assert result["total_predicted_cost"] > 0
        assert result["predicted_cost_per_sqft"] > 0

    async def test_predict_cost_quality_impact(self):
        """Premium quality should produce higher cost than standard."""
        standard = await predict_cost(
            {
                "sqft": 50000,
                "stories": 3,
                "type": "commercial",
                "region": "national",
                "quality_level": "standard",
            }
        )
        premium = await predict_cost(
            {
                "sqft": 50000,
                "stories": 3,
                "type": "commercial",
                "region": "national",
                "quality_level": "premium",
            }
        )
        assert premium["total_predicted_cost"] > standard["total_predicted_cost"]

    async def test_predict_cost_has_confidence_interval(self):
        """Result should include confidence interval bounds."""
        result = await predict_cost(
            {
                "sqft": 50000,
                "stories": 3,
                "type": "commercial",
                "region": "national",
                "quality_level": "standard",
            }
        )
        assert "confidence_interval" in result
        ci = result["confidence_interval"]
        assert "low" in ci
        assert "high" in ci
        assert ci["low"] < result["total_predicted_cost"]
        assert ci["high"] > result["total_predicted_cost"]

    async def test_predict_cost_story_multiplier(self):
        """Buildings with more stories should cost more per sqft."""
        low_rise = await predict_cost(
            {
                "sqft": 50000,
                "stories": 3,
                "type": "commercial",
                "region": "national",
                "quality_level": "standard",
            }
        )
        high_rise = await predict_cost(
            {
                "sqft": 50000,
                "stories": 10,
                "type": "commercial",
                "region": "national",
                "quality_level": "standard",
            }
        )
        assert high_rise["predicted_cost_per_sqft"] > low_rise["predicted_cost_per_sqft"]

    async def test_predict_cost_model_used(self):
        """Result should identify the model used (heuristic fallback)."""
        result = await predict_cost(
            {
                "sqft": 50000,
                "stories": 3,
                "type": "commercial",
                "region": "national",
                "quality_level": "standard",
            }
        )
        assert result["model_used"] == "heuristic"
