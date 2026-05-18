"""Phase 2: Procurement agent orchestration tests.

Tests for the procurement agent that coordinates price forecasting,
vendor scoring, and contract risk analysis. All downstream services
are mocked.
"""

from __future__ import annotations

from unittest.mock import patch

from tests.fixtures.precon_mock_responses import MOCK_PRICE_HISTORY


class TestProcurementAgent:
    """Tests for the procurement agent orchestrator."""

    @patch("app.services.procurement.contract_risk.score_contract_risk")
    @patch("app.services.procurement.vendor_manager.score_vendor")
    @patch("app.services.procurement.price_forecaster.forecast_prices")
    async def test_procurement_pipeline_integration(
        self, mock_forecast, mock_vendor, mock_contract
    ):
        """The full procurement pipeline should run forecasting, scoring, and risk."""
        mock_forecast.return_value = {
            "forecasts": [
                {
                    "date": "2025-01-01",
                    "forecast_value": 290,
                    "lower_bound": 285,
                    "upper_bound": 295,
                }
            ],
            "model_used": "linear_trend",
            "rmse": 5.0,
            "trend": "rising",
            "summary": "Prices are rising.",
        }
        mock_vendor.return_value = {
            "vendor_id": "v1",
            "overall_score": 85,
            "criteria_scores": {},
            "recommendation": "recommended",
            "risk_flags": [],
        }
        mock_contract.return_value = {
            "overall_risk_score": 45,
            "risk_items": [],
            "recommendations": [],
            "model_used": "gpt-4o-mini",
        }

        from app.services.procurement.contract_risk import score_contract_risk
        from app.services.procurement.price_forecaster import forecast_prices
        from app.services.procurement.vendor_manager import score_vendor

        forecast = await forecast_prices(MOCK_PRICE_HISTORY, horizon_months=3)
        assert forecast["trend"] == "rising"

        vendor_score = await score_vendor({"vendor_id": "v1"})
        assert vendor_score["overall_score"] == 85

        risk = await score_contract_risk("Sample contract", "commercial")
        assert risk["overall_risk_score"] == 45

    @patch("app.services.procurement.price_forecaster.forecast_prices")
    async def test_procurement_forecast_only(self, mock_forecast):
        """Agent should handle forecast-only scenarios."""
        mock_forecast.return_value = {
            "forecasts": [],
            "model_used": "linear_trend",
            "rmse": 0.0,
            "trend": "stable",
            "summary": "No data.",
        }

        from app.services.procurement.price_forecaster import forecast_prices

        result = await forecast_prices([], horizon_months=3)
        assert result["trend"] == "stable"

    @patch("app.services.procurement.vendor_manager.score_vendor")
    async def test_procurement_vendor_scoring(self, mock_vendor):
        """Agent should score vendors correctly."""
        mock_vendor.return_value = {
            "vendor_id": "v1",
            "overall_score": 72,
            "criteria_scores": {"quality": {"score": 80, "weight": 0.2}},
            "recommendation": "recommended",
            "risk_flags": [],
        }

        from app.services.procurement.vendor_manager import score_vendor

        result = await score_vendor({"vendor_id": "v1"})
        assert result["recommendation"] == "recommended"
