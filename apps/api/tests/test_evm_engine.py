"""Tests for the Earned Value Management (EVM) calculation engine.

Pin documented EVM formulas, the M-22 'is_provisional' flag for
synthetic SPI/CPI when PV=0/AC=0, the Earned Schedule interpolation,
and the BAC validation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.controls.evm_engine import (
    _compute_earned_schedule,
    calculate_evm_metrics,
)

# =========================================================================
# calculate_evm_metrics — input validation
# =========================================================================


def test_bac_zero_raises():
    """[invariant] BAC must be positive (cannot divide-by-zero in EAC)."""
    with pytest.raises(ValueError, match="BAC must be positive"):
        calculate_evm_metrics(
            bac=Decimal("0"),
            pv=Decimal("100"),
            ev=Decimal("90"),
            ac=Decimal("110"),
        )


def test_bac_negative_raises():
    with pytest.raises(ValueError, match="BAC must be positive"):
        calculate_evm_metrics(
            bac=Decimal("-1"),
            pv=Decimal("100"),
            ev=Decimal("90"),
            ac=Decimal("110"),
        )


def test_negative_pv_raises():
    """[invariant] PV cannot be negative."""
    with pytest.raises(ValueError, match="non-negative"):
        calculate_evm_metrics(
            bac=Decimal("1000"),
            pv=Decimal("-10"),
            ev=Decimal("0"),
            ac=Decimal("0"),
        )


def test_negative_ev_raises():
    with pytest.raises(ValueError, match="non-negative"):
        calculate_evm_metrics(
            bac=Decimal("1000"),
            pv=Decimal("10"),
            ev=Decimal("-1"),
            ac=Decimal("0"),
        )


def test_negative_ac_raises():
    with pytest.raises(ValueError, match="non-negative"):
        calculate_evm_metrics(
            bac=Decimal("1000"),
            pv=Decimal("10"),
            ev=Decimal("0"),
            ac=Decimal("-1"),
        )


# =========================================================================
# Standard EVM formulas
# =========================================================================


def test_on_track_baseline():
    """Project running exactly on plan: SPI=1, CPI=1, EAC=BAC."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("500"),
        ev=Decimal("500"),
        ac=Decimal("500"),
    )
    assert out["spi"] == Decimal("1.0000")
    assert out["cpi"] == Decimal("1.0000")
    assert out["eac"] == Decimal("1000.00")
    assert out["sv"] == Decimal("0.00")
    assert out["cv"] == Decimal("0.00")
    assert out["is_valid"] is True


def test_under_budget_ahead_of_schedule():
    """EV > PV (ahead) AND EV > AC (under budget) -> SPI > 1, CPI > 1."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("400"),
        ev=Decimal("500"),
        ac=Decimal("450"),
    )
    # SPI = 500/400 = 1.25
    assert out["spi"] == Decimal("1.2500")
    # CPI = 500/450 ≈ 1.1111
    assert out["cpi"] == Decimal("1.1111")
    # EAC = 1000 / 1.1111 ≈ 900
    assert out["eac"] is not None and out["eac"] < Decimal("1000")


def test_over_budget_behind_schedule():
    """EV < PV (behind) AND EV < AC (over budget) -> SPI < 1, CPI < 1."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("500"),
        ev=Decimal("400"),
        ac=Decimal("550"),
    )
    assert out["spi"] == Decimal("0.8000")
    # CPI = 400/550 ≈ 0.7273
    assert out["cpi"] == Decimal("0.7273")
    assert out["sv"] == Decimal("-100.00")  # 400 - 500
    assert out["cv"] == Decimal("-150.00")  # 400 - 550


def test_eac_formula_bac_over_cpi():
    """[contract] Pin EAC = BAC / CPI (CPI method, the default)."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("500"),
        ev=Decimal("400"),
        ac=Decimal("500"),
    )
    # CPI = 0.8, EAC = 1000 / 0.8 = 1250
    assert out["eac"] == Decimal("1250.00")
    # ETC = EAC - AC = 750
    assert out["etc"] == Decimal("750.00")
    # VAC = BAC - EAC = -250
    assert out["vac"] == Decimal("-250.00")


def test_percent_complete_formula():
    """[contract] percent_complete = EV / BAC * 100."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("500"),
        ev=Decimal("450"),
        ac=Decimal("500"),
    )
    assert out["percent_complete"] == Decimal("45.00")


# =========================================================================
# M-22 — provisional SPI/CPI when PV=0/AC=0
# =========================================================================


def test_pv_zero_spi_provisional_one():
    """[M-22] PV=0 -> SPI defaults to 1.0 (project not started yet)
    + warning + is_valid=False. Pin: refactor must NOT crash on PV=0
    AND must NOT silently report SPI=1 as 'on track'."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("0"),
        ev=Decimal("0"),
        ac=Decimal("0"),
    )
    assert out["spi"] == Decimal("1.0000")
    assert out["is_valid"] is False
    assert any("SPI" in w and "synthetic" in w for w in out["warnings"])


def test_ac_zero_cpi_provisional_one():
    """[M-22] AC=0 -> CPI=1 + warning + is_valid=False."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("100"),
        ev=Decimal("100"),
        ac=Decimal("0"),
    )
    assert out["cpi"] == Decimal("1.0000")
    assert out["is_valid"] is False
    assert any("CPI" in w and "synthetic" in w for w in out["warnings"])


def test_both_pv_and_ac_zero():
    """Both PV=0 and AC=0 -> both warnings, is_valid=False."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("0"),
        ev=Decimal("0"),
        ac=Decimal("0"),
    )
    assert len(out["warnings"]) == 2
    assert out["is_valid"] is False


def test_real_data_is_valid_true():
    """When PV>0 and AC>0, is_valid=True (no provisional fallback)."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("100"),
        ev=Decimal("50"),
        ac=Decimal("80"),
    )
    assert out["is_valid"] is True
    assert out["warnings"] == []


# =========================================================================
# TCPI variants
# =========================================================================


def test_tcpi_bac_formula():
    """[contract] TCPI_BAC = (BAC - EV) / (BAC - AC)."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("400"),
        ev=Decimal("400"),
        ac=Decimal("500"),
    )
    # remaining_work = 600, remaining_budget = 500 -> 1.2
    assert out["tcpi_bac"] == Decimal("1.2000")


def test_tcpi_eac_formula():
    """TCPI_EAC = (BAC - EV) / (EAC - AC)."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("400"),
        ev=Decimal("400"),
        ac=Decimal("500"),
    )
    # CPI = 0.8, EAC = 1250, EAC - AC = 750
    # TCPI_EAC = 600 / 750 = 0.8
    assert out["tcpi_eac"] == Decimal("0.8000")


# =========================================================================
# IEAC — composite method
# =========================================================================


def test_ieac_composite_formula():
    """[contract] IEAC = AC + (BAC - EV) / (SPI * CPI)."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("500"),
        ev=Decimal("400"),
        ac=Decimal("500"),
    )
    # SPI = 0.8, CPI = 0.8, composite = 0.64
    # IEAC = 500 + 600 / 0.64 = 500 + 937.5 = 1437.5
    assert out["ieac"] == Decimal("1437.50")


# =========================================================================
# Earned Schedule
# =========================================================================


def test_es_via_pv_curve_interpolation():
    """[contract] When pv_curve provided, ES interpolates between
    discrete periods. Pin the linear-interpolation formula."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("500"),
        ev=Decimal("450"),
        ac=Decimal("400"),
        current_period=5,
        pv_curve=[Decimal("100"), Decimal("250"), Decimal("400"), Decimal("600"), Decimal("800")],
    )
    # EV=450 between PV[2]=400 (period 3) and PV[3]=600 (period 4)
    # ES = 3 + (450-400)/(600-400) = 3.25
    assert out["es"] == Decimal("3.2500")
    # SV(t) = ES - AT = 3.25 - 5 = -1.75 (behind schedule by 1.75 periods)
    assert out["sv_t"] == Decimal("-1.7500")
    # SPI(t) = ES/AT = 3.25/5 = 0.65
    assert out["spi_t"] == Decimal("0.6500")


def test_es_no_curve_estimated_from_spi():
    """[fallback] No pv_curve but planned_duration provided -> estimate
    ES = (planned_duration * EV/BAC) * SPI."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("500"),
        ev=Decimal("400"),
        ac=Decimal("400"),
        planned_duration=10,
        current_period=5,
    )
    # SPI = 400/500 = 0.8
    # estimated_at = 10 * 400/1000 = 4
    # es = 4 * 0.8 = 3.2
    assert out["es"] == Decimal("3.2000")


def test_es_skipped_without_planned_duration_or_curve():
    """No planned_duration AND no pv_curve -> es=None."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("500"),
        ev=Decimal("400"),
        ac=Decimal("400"),
    )
    assert out["es"] is None
    assert out["sv_t"] is None
    assert out["spi_t"] is None


# =========================================================================
# _compute_earned_schedule — direct
# =========================================================================


def test_earned_schedule_empty_curve_returns_none():
    assert _compute_earned_schedule(Decimal("100"), []) is None


def test_earned_schedule_ev_at_or_above_final_returns_n():
    """EV >= last PV value -> ES = full duration n."""
    assert _compute_earned_schedule(
        Decimal("1000"),
        [Decimal("100"), Decimal("250"), Decimal("400")],
    ) == Decimal("3")


def test_earned_schedule_ev_below_first_period_uses_zero_baseline():
    """[contract] When EV is below pv_curve[0], at=0 -> pv_t=0
    baseline, fraction = ev / pv_curve[0]."""
    out = _compute_earned_schedule(
        Decimal("50"),
        [Decimal("100"), Decimal("200"), Decimal("300")],
    )
    # at=0, pv_t=0, pv_t1=100, fraction=50/100=0.5 -> ES=0.5
    assert out == Decimal("0.5")


# =========================================================================
# Output rounding
# =========================================================================


def test_output_rounding_pinned_decimals():
    """[contract] sv/cv/eac/etc/vac rounded to 2 decimals;
    spi/cpi/tcpi rounded to 4 decimals; percent_complete to 2."""
    out = calculate_evm_metrics(
        bac=Decimal("1000"),
        pv=Decimal("333.333"),
        ev=Decimal("300.6789"),
        ac=Decimal("280.111"),
    )
    # All values round-tripped:
    for key in ("sv", "cv", "eac", "etc", "vac", "percent_complete"):
        if out[key] is not None:
            # Max 2 decimal places:
            assert abs(out[key] - round(out[key], 2)) < Decimal("0.001"), f"{key}: {out[key]}"
    for key in ("spi", "cpi", "tcpi", "tcpi_bac", "tcpi_eac"):
        if out[key] is not None:
            # Max 4 decimal places:
            assert abs(out[key] - round(out[key], 4)) < Decimal("0.0001"), f"{key}: {out[key]}"
