"""Tests for EAC forecasting methods."""

from __future__ import annotations

from decimal import Decimal

from app.services.controls.eac_forecaster import forecast_eac


class TestEACForecaster:
    async def test_cpi_method(self):
        result = await forecast_eac(
            bac=Decimal("1000000"),
            ev=Decimal("450000"),
            ac=Decimal("480000"),
            spi=Decimal("0.9"),
            cpi=Decimal("0.9375"),
            method="cpi",
        )
        assert result["method"] == "cpi"
        assert result["eac_value"] > Decimal("1000000")
        assert result["confidence_low"] < result["eac_value"]
        assert result["confidence_high"] > result["eac_value"]

    async def test_spi_cpi_method(self):
        result = await forecast_eac(
            bac=Decimal("1000000"),
            ev=Decimal("450000"),
            ac=Decimal("480000"),
            spi=Decimal("0.9"),
            cpi=Decimal("0.9375"),
            method="spi_cpi",
        )
        assert result["method"] == "spi_cpi"
        assert result["eac_value"] > Decimal("0")

    async def test_remaining_work_method(self):
        result = await forecast_eac(
            bac=Decimal("1000000"),
            ev=Decimal("450000"),
            ac=Decimal("480000"),
            spi=Decimal("0.9"),
            cpi=Decimal("0.9375"),
            method="remaining_work",
        )
        assert result["method"] == "remaining_work"
        expected = Decimal("480000") + (Decimal("1000000") - Decimal("450000"))
        assert result["eac_value"] == expected

    async def test_mgmt_estimate_method(self):
        result = await forecast_eac(
            bac=Decimal("1000000"),
            ev=Decimal("450000"),
            ac=Decimal("480000"),
            spi=Decimal("0.9"),
            cpi=Decimal("0.9375"),
            method="mgmt_estimate",
        )
        assert result["method"] == "mgmt_estimate"
        assert result["eac_value"] > Decimal("0")

    async def test_confidence_intervals(self):
        result = await forecast_eac(
            bac=Decimal("1000000"),
            ev=Decimal("450000"),
            ac=Decimal("480000"),
            spi=Decimal("0.9"),
            cpi=Decimal("0.9375"),
        )
        assert result["confidence_low"] is not None
        assert result["confidence_high"] is not None
        assert result["confidence_low"] < result["eac_value"]
        assert result["confidence_high"] > result["eac_value"]
        # With phase-aware CI (EV/BAC=0.45 -> mid phase -> ~15% margin),
        # just verify the interval is reasonable (within 5-30% of EAC)
        margin = result["eac_value"] - result["confidence_low"]
        margin_pct = margin / result["eac_value"]
        assert Decimal("0.05") <= margin_pct <= Decimal("0.30")
