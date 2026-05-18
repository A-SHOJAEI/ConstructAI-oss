"""Tests for the pure helpers in services/controls/eac_forecaster.

EAC (Estimate at Completion) is the canonical EVM forecast that
drives schedule/cost variance reporting. Pin the standard-deviation
helper, the data-aware confidence-interval logic, the phase-aware
margin defaults, and the trend-weighted EAC formula.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.controls.eac_forecaster import (
    _compute_confidence_interval,
    _compute_std_dev,
    _compute_trend_weighted_eac,
    _phase_aware_margin,
)

# =========================================================================
# _compute_std_dev
# =========================================================================


def test_std_dev_empty_list_zero():
    assert _compute_std_dev([]) == Decimal("0")


def test_std_dev_single_value_zero():
    """Need at least 2 values for variance — single value returns 0."""
    assert _compute_std_dev([Decimal("1.0")]) == Decimal("0")


def test_std_dev_constant_values_zero():
    """Constant series — variance = 0, std = 0."""
    values = [Decimal("0.95")] * 5
    assert _compute_std_dev(values) == Decimal("0")


def test_std_dev_known_values():
    """[1, 2, 3, 4, 5] → mean=3, sample variance = 10/4 = 2.5,
    std ≈ 1.5811."""
    values = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5")]
    out = _compute_std_dev(values)
    assert Decimal("1.58") < out < Decimal("1.59")


def test_std_dev_uses_sample_not_population():
    """Pin: divide by (n-1), not n. For [1, 2] sample variance = 0.5,
    std ≈ 0.707; population would give 0.5."""
    values = [Decimal("1"), Decimal("2")]
    out = _compute_std_dev(values)
    assert Decimal("0.70") < out < Decimal("0.71")


# =========================================================================
# _phase_aware_margin
# =========================================================================


def test_phase_margin_early_25_pct():
    """EV/BAC < 20% → ±25% margin."""
    margin = _phase_aware_margin(
        eac=Decimal("100"),
        bac=Decimal("100"),
        ev=Decimal("10"),  # 10% complete → "early"
    )
    assert margin == Decimal("25.00")


def test_phase_margin_mid_15_pct():
    """20% ≤ EV/BAC ≤ 70% → ±15% margin."""
    margin = _phase_aware_margin(
        eac=Decimal("100"),
        bac=Decimal("100"),
        ev=Decimal("50"),
    )
    assert margin == Decimal("15.00")


def test_phase_margin_late_8_pct():
    """EV/BAC > 70% → ±8% margin."""
    margin = _phase_aware_margin(
        eac=Decimal("100"),
        bac=Decimal("100"),
        ev=Decimal("85"),
    )
    assert margin == Decimal("8.00")


def test_phase_margin_zero_bac_safe():
    """Defensive: BAC=0 must not crash. Completion% defaults to 0,
    landing in "early" band."""
    margin = _phase_aware_margin(eac=Decimal("100"), bac=Decimal("0"), ev=Decimal("0"))
    assert margin == Decimal("25.00")


def test_phase_margin_boundary_at_20_pct_is_mid():
    """At exactly 20%, the "<" check fails → mid (15%)."""
    margin = _phase_aware_margin(eac=Decimal("100"), bac=Decimal("100"), ev=Decimal("20"))
    assert margin == Decimal("15.00")


def test_phase_margin_boundary_at_70_pct_is_mid():
    """At exactly 70%, the "<=" check passes → mid (15%)."""
    margin = _phase_aware_margin(eac=Decimal("100"), bac=Decimal("100"), ev=Decimal("70"))
    assert margin == Decimal("15.00")


# =========================================================================
# _compute_confidence_interval
# =========================================================================


def test_confidence_interval_no_history_uses_phase_margin():
    """Empty history, no benchmark → falls back to phase-aware margin
    (early phase = ±25%)."""
    low, high = _compute_confidence_interval(
        eac=Decimal("100"),
        bac=Decimal("100"),
        ev=Decimal("10"),  # early phase
        historical_cpi_values=None,
        project_type=None,
    )
    assert low == Decimal("75.00")
    assert high == Decimal("125.00")


def test_confidence_interval_5_plus_snapshots_uses_stats():
    """≥ 5 historical CPI values → statistical CI from project data."""
    cpi_values = [
        Decimal("0.95"),
        Decimal("0.96"),
        Decimal("0.94"),
        Decimal("0.97"),
        Decimal("0.95"),
    ]
    low, high = _compute_confidence_interval(
        eac=Decimal("105.26"),
        bac=Decimal("100"),
        ev=Decimal("50"),
        historical_cpi_values=cpi_values,
    )
    # CI must be symmetric around eac:
    eac = Decimal("105.26")
    margin_low = eac - low
    margin_high = high - eac
    assert abs(margin_low - margin_high) < Decimal("0.01")


def test_confidence_interval_2_to_4_snapshots_uses_stats():
    """2-4 values → still uses stats (smaller n, wider CI)."""
    cpi_values = [Decimal("0.9"), Decimal("0.95"), Decimal("1.0")]
    low, high = _compute_confidence_interval(
        eac=Decimal("105"),
        bac=Decimal("100"),
        ev=Decimal("50"),
        historical_cpi_values=cpi_values,
    )
    assert low < Decimal("105")
    assert high > Decimal("105")


def test_confidence_interval_constant_cpi_zero_margin():
    """When CPI is constant, std=0 → margin=0 → low == high == eac."""
    cpi_values = [Decimal("0.95")] * 10
    low, high = _compute_confidence_interval(
        eac=Decimal("100"),
        bac=Decimal("100"),
        ev=Decimal("50"),
        historical_cpi_values=cpi_values,
    )
    assert low == high == Decimal("100")


# =========================================================================
# _compute_trend_weighted_eac
# =========================================================================


def test_trend_weighted_eac_three_plus_values():
    """3 values [0.9, 0.95, 1.0] (oldest → newest), weights [0.2, 0.3, 0.5]:
    weighted_cpi = 0.18 + 0.285 + 0.5 = 0.965
    EAC = 100 / 0.965 ≈ 103.63."""
    cpi_values = [Decimal("0.9"), Decimal("0.95"), Decimal("1.0")]
    out = _compute_trend_weighted_eac(
        bac=Decimal("100"),
        historical_cpi_values=cpi_values,
    )
    assert Decimal("103") < out < Decimal("104")


def test_trend_weighted_eac_takes_last_three_only():
    """When > 3 values present, only the last 3 contribute."""
    # Add older outliers — they should NOT affect the result:
    cpi_values = [Decimal("10")] * 5 + [Decimal("0.9"), Decimal("0.95"), Decimal("1.0")]
    out = _compute_trend_weighted_eac(
        bac=Decimal("100"),
        historical_cpi_values=cpi_values,
    )
    # Same result as just the trailing 3:
    assert Decimal("103") < out < Decimal("104")


def test_trend_weighted_eac_two_values_uses_2_weights():
    """2 values use weights [0.4, 0.6] (newest weighted heavier)."""
    cpi_values = [Decimal("0.5"), Decimal("1.0")]
    out = _compute_trend_weighted_eac(
        bac=Decimal("100"),
        historical_cpi_values=cpi_values,
    )
    # weighted = 0.4×0.5 + 0.6×1.0 = 0.8 → EAC = 100/0.8 = 125
    assert out == Decimal("125")


def test_trend_weighted_eac_single_value():
    """Single CPI = single value with weight 1.0 → EAC = BAC/CPI."""
    out = _compute_trend_weighted_eac(
        bac=Decimal("100"),
        historical_cpi_values=[Decimal("0.8")],
    )
    assert out == Decimal("125")


def test_trend_weighted_eac_no_history_uses_simple_cpi():
    """No history → fall back to BAC/CPI."""
    out = _compute_trend_weighted_eac(
        bac=Decimal("100"),
        historical_cpi_values=None,
        cpi=Decimal("0.5"),
    )
    assert out == Decimal("200")


def test_trend_weighted_eac_no_history_no_cpi_returns_bac():
    """No data at all → return BAC unchanged (best estimate)."""
    out = _compute_trend_weighted_eac(
        bac=Decimal("100"),
        historical_cpi_values=None,
        cpi=None,
    )
    assert out == Decimal("100")


def test_trend_weighted_eac_zero_cpi_returns_bac():
    """[security/safety] Zero CPI must NOT cause division by zero."""
    out = _compute_trend_weighted_eac(
        bac=Decimal("100"),
        historical_cpi_values=None,
        cpi=Decimal("0"),
    )
    assert out == Decimal("100")


def test_trend_weighted_eac_empty_history_uses_cpi():
    """Empty list (vs None) — must fall back through the same path."""
    out = _compute_trend_weighted_eac(
        bac=Decimal("100"),
        historical_cpi_values=[],
        cpi=Decimal("0.5"),
    )
    assert out == Decimal("200")


def test_trend_weighted_eac_baseline_one():
    """All-1.0 CPI history → weighted CPI = 1.0 → EAC = BAC."""
    out = _compute_trend_weighted_eac(
        bac=Decimal("100"),
        historical_cpi_values=[Decimal("1"), Decimal("1"), Decimal("1")],
    )
    assert out == Decimal("100")


def test_trend_weighted_eac_newest_gets_more_weight():
    """Compare two histories — same first two values, different last:
    one ending high, one ending low. The "high last" should produce a
    smaller EAC (better forecast) due to the 0.5 weight on newest."""
    bac = Decimal("100")
    out_high = _compute_trend_weighted_eac(
        bac=bac,
        historical_cpi_values=[Decimal("0.5"), Decimal("0.7"), Decimal("1.2")],
    )
    out_low = _compute_trend_weighted_eac(
        bac=bac,
        historical_cpi_values=[Decimal("0.5"), Decimal("0.7"), Decimal("0.5")],
    )
    # The history ending with 1.2 (productive) → smaller EAC (closer to BAC)
    # than the one ending with 0.5 (still slow).
    assert out_high < out_low
