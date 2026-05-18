"""Tests for productivity forecasting."""

from __future__ import annotations

from datetime import date

from app.services.productivity.productivity_forecaster import (
    forecast_productivity,
)
from tests.fixtures.sample_productivity_data import (
    HISTORICAL_PRODUCTIVITY,
)


class TestProductivityForecaster:
    async def test_basic_forecast(self):
        result = await forecast_productivity(
            historical_data=HISTORICAL_PRODUCTIVITY,
            trade="concrete",
            forecast_days=14,
        )
        assert result["trade"] == "concrete"
        assert len(result["forecast_dates"]) == 14
        assert len(result["predicted_rates"]) == 14
        assert result["trend"] in (
            "improving",
            "declining",
            "stable",
        )

    async def test_confidence_intervals(self):
        result = await forecast_productivity(
            historical_data=HISTORICAL_PRODUCTIVITY,
            trade="concrete",
            forecast_days=7,
        )
        assert len(result["confidence_intervals"]) == 7
        for ci in result["confidence_intervals"]:
            assert "lower" in ci
            assert "upper" in ci
            assert ci["lower"] <= ci["upper"]

    async def test_insufficient_data(self):
        # "concrete" has baseline rates, so returns baseline forecast
        result = await forecast_productivity(
            historical_data=[],
            trade="concrete",
        )
        assert result["trend"] == "baseline"
        assert len(result["forecast_dates"]) > 0

    async def test_insufficient_data_unknown_trade(self):
        # Unknown trade with no baseline → truly insufficient data
        result = await forecast_productivity(
            historical_data=[],
            trade="unknown_trade_xyz",
        )
        assert result["trend"] == "insufficient_data"
        assert len(result["forecast_dates"]) == 0

    async def test_two_data_points(self):
        result = await forecast_productivity(
            historical_data=[
                {
                    "work_date": date(2024, 6, 1),
                    "actual_units": 100,
                    "planned_units": 150,
                },
                {
                    "work_date": date(2024, 6, 2),
                    "actual_units": 120,
                    "planned_units": 150,
                },
            ],
            trade="rebar",
        )
        assert result["trend"] == "insufficient_data"

    async def test_improving_trend(self):
        data = [
            {
                "work_date": date(2024, 6, i),
                "actual_units": float(100 + i * 5),
                "planned_units": 150.0,
            }
            for i in range(1, 15)
        ]
        result = await forecast_productivity(
            historical_data=data,
            trade="concrete",
        )
        assert result["trend"] == "improving"
