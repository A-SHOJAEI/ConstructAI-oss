"""Tests for S-Curve data generation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.controls.scurve_generator import (
    generate_scurve_data,
)


class TestSCurveGenerator:
    async def test_basic_scurve(self):
        snapshots = [
            {
                "snapshot_date": "2024-01-15",
                "pv": "100000",
                "ev": "95000",
                "ac": "98000",
                "spi": "0.95",
            },
            {
                "snapshot_date": "2024-02-15",
                "pv": "200000",
                "ev": "190000",
                "ac": "195000",
                "spi": "0.95",
            },
        ]
        result = await generate_scurve_data(
            snapshots=snapshots,
            bac=Decimal("1000000"),
            start_date=date(2024, 1, 1),
        )
        assert len(result["data_points"]) == 2
        assert result["bac"] == Decimal("1000000")

    async def test_empty_snapshots(self):
        result = await generate_scurve_data(
            snapshots=[],
            bac=Decimal("1000000"),
            start_date=date(2024, 1, 1),
        )
        assert len(result["data_points"]) == 0
        assert result["forecast_completion"] is None

    async def test_forecast_completion(self):
        snapshots = [
            {
                "snapshot_date": "2024-03-01",
                "pv": "300000",
                "ev": "280000",
                "ac": "290000",
                "spi": "0.93",
            },
        ]
        result = await generate_scurve_data(
            snapshots=snapshots,
            bac=Decimal("1000000"),
            start_date=date(2024, 1, 1),
        )
        assert result["forecast_completion"] is not None

    async def test_data_point_structure(self):
        snapshots = [
            {
                "snapshot_date": "2024-01-15",
                "pv": "100000",
                "ev": "95000",
                "ac": "98000",
                "spi": "0.95",
            },
        ]
        result = await generate_scurve_data(
            snapshots=snapshots,
            bac=Decimal("1000000"),
            start_date=date(2024, 1, 1),
        )
        dp = result["data_points"][0]
        assert "date" in dp
        assert "planned_value" in dp
        assert "earned_value" in dp
        assert "actual_cost" in dp
