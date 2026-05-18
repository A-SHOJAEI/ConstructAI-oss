"""Tests for EVM calculation engine."""

from __future__ import annotations

from decimal import Decimal

from app.services.controls.evm_engine import (
    calculate_evm_metrics,
    compute_evm_snapshot,
)


class TestEVMEngine:
    def test_basic_evm_calculation(self):
        result = calculate_evm_metrics(
            bac=Decimal("1000000"),
            pv=Decimal("500000"),
            ev=Decimal("450000"),
            ac=Decimal("480000"),
        )
        assert result["sv"] == Decimal("-50000.00")
        assert result["cv"] == Decimal("-30000.00")
        assert result["spi"] == Decimal("0.9000")
        assert result["cpi"] == Decimal("0.9375")
        assert result["percent_complete"] == Decimal("45.00")

    def test_evm_on_track(self):
        result = calculate_evm_metrics(
            bac=Decimal("1000000"),
            pv=Decimal("500000"),
            ev=Decimal("500000"),
            ac=Decimal("500000"),
        )
        assert result["spi"] == Decimal("1.0000")
        assert result["cpi"] == Decimal("1.0000")
        assert result["sv"] == Decimal("0.00")
        assert result["cv"] == Decimal("0.00")

    def test_evm_ahead_of_schedule(self):
        result = calculate_evm_metrics(
            bac=Decimal("1000000"),
            pv=Decimal("500000"),
            ev=Decimal("600000"),
            ac=Decimal("480000"),
        )
        assert result["spi"] > Decimal("1")
        assert result["cpi"] > Decimal("1")

    def test_evm_zero_pv(self):
        """When PV=0, SPI defaults to 1.0 with is_valid=False (M-22)."""
        result = calculate_evm_metrics(
            bac=Decimal("1000000"),
            pv=Decimal("0"),
            ev=Decimal("0"),
            ac=Decimal("0"),
        )
        # Code returns synthetic 1.0 with is_provisional flag set, not None.
        # This keeps numeric callers (dashboards, alerts) crash-free; consumers
        # check `is_valid` to know the ratio isn't real.
        assert result["spi"] == Decimal("1")
        assert result["cpi"] == Decimal("1")
        assert result["is_valid"] is False

    def test_evm_zero_bac(self):
        """BAC must be positive — raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="BAC must be positive"):
            calculate_evm_metrics(
                bac=Decimal("0"),
                pv=Decimal("0"),
                ev=Decimal("0"),
                ac=Decimal("0"),
            )

    async def test_compute_evm_snapshot_async(self):
        result = await compute_evm_snapshot(
            bac=Decimal("1000000"),
            pv=Decimal("500000"),
            ev=Decimal("450000"),
            ac=Decimal("480000"),
        )
        assert "spi" in result
        assert "cpi" in result
        assert "eac" in result

    def test_tcpi_calculation(self):
        result = calculate_evm_metrics(
            bac=Decimal("1000000"),
            pv=Decimal("500000"),
            ev=Decimal("450000"),
            ac=Decimal("480000"),
        )
        assert "tcpi" in result
        assert result["tcpi"] > Decimal("1")

    def test_eac_calculation(self):
        result = calculate_evm_metrics(
            bac=Decimal("1000000"),
            pv=Decimal("500000"),
            ev=Decimal("450000"),
            ac=Decimal("480000"),
        )
        assert result["eac"] > Decimal("1000000")
        assert result["vac"] < Decimal("0")
