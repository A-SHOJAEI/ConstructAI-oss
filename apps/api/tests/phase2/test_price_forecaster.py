"""Phase 2: Price forecasting tests.

Tests for the ARIMA+Prophet ensemble price forecaster. Uses the linear
trend fallback since statsmodels/prophet may or may not be installed.
"""

from __future__ import annotations

from app.services.procurement.price_forecaster import forecast_prices
from tests.fixtures.precon_mock_responses import MOCK_PRICE_HISTORY


class TestPriceForecaster:
    """Tests for the price forecasting service."""

    async def test_forecast_returns_predictions(self):
        """Forecast should return at least the requested number of periods."""
        result = await forecast_prices(MOCK_PRICE_HISTORY, horizon_months=3)
        assert "forecasts" in result
        assert len(result["forecasts"]) >= 3

    async def test_forecast_has_bounds(self):
        """Each forecast period should have lower and upper bounds."""
        result = await forecast_prices(MOCK_PRICE_HISTORY, horizon_months=3)
        for f in result["forecasts"]:
            assert "forecast_value" in f
            assert "lower_bound" in f
            assert "upper_bound" in f
            assert f["lower_bound"] <= f["forecast_value"] <= f["upper_bound"]

    async def test_forecast_trend(self):
        """Forecast should classify the trend direction."""
        result = await forecast_prices(MOCK_PRICE_HISTORY, horizon_months=6)
        assert result["trend"] in ("rising", "falling", "stable")

    async def test_forecast_model_used(self):
        """Forecast should identify the model used."""
        result = await forecast_prices(MOCK_PRICE_HISTORY, horizon_months=3)
        assert result["model_used"] in (
            "arima+prophet_ensemble",
            "arima_only",
            "prophet_only",
            "linear_trend",
        )

    async def test_forecast_rmse(self):
        """Forecast should include an RMSE value."""
        result = await forecast_prices(MOCK_PRICE_HISTORY, horizon_months=3)
        assert "rmse" in result
        assert result["rmse"] >= 0

    async def test_forecast_empty_history(self):
        """Empty history should return no forecasts."""
        result = await forecast_prices([], horizon_months=3)
        assert result["forecasts"] == []
        assert result["model_used"] == "none"
        assert result["trend"] == "stable"

    async def test_forecast_summary(self):
        """Forecast should include a human-readable summary."""
        result = await forecast_prices(MOCK_PRICE_HISTORY, horizon_months=3)
        assert "summary" in result
        assert len(result["summary"]) > 0
