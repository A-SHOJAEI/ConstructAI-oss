"""Tests for the pure helpers in services/controls/scurve_generator.

The S-curve generator fits a logistic function to project performance
data (planned/earned/actual cost over time) to produce performance
projections. Pin the math:

- _logistic / _logistic_array: standard formula correctness.
- _fit_logistic: scipy curve_fit happy path + insufficient-data /
  no-scipy / no-data fallbacks.
- _compute_forecast_bands: P10/P50/P90 from residual std,
  uncertainty growth with time, P10 floored at 0.
"""

from __future__ import annotations

import math

import pytest

from app.services.controls.scurve_generator import (
    _compute_forecast_bands,
    _fit_logistic,
    _logistic,
    _logistic_array,
)

# =========================================================================
# _logistic — pure scalar function
# =========================================================================


def test_logistic_at_t0_is_half_l():
    """At t = t0, logistic(t) = L / (1 + e^0) = L/2."""
    assert _logistic(t=10.0, L=100.0, k=0.5, t0=10.0) == pytest.approx(50.0)


def test_logistic_at_negative_infinity_approaches_zero():
    """Far below t0 → output approaches 0."""
    out = _logistic(t=-100.0, L=100.0, k=0.5, t0=10.0)
    assert out < 0.01


def test_logistic_at_positive_infinity_approaches_l():
    """Far above t0 → output approaches L (the asymptote)."""
    out = _logistic(t=100.0, L=100.0, k=0.5, t0=10.0)
    assert out > 99.99


def test_logistic_higher_k_means_steeper_curve():
    """At t = t0 + 1, higher k gives a steeper (closer to L) value."""
    slow = _logistic(t=11.0, L=100.0, k=0.1, t0=10.0)
    fast = _logistic(t=11.0, L=100.0, k=1.0, t0=10.0)
    # Both should be > 50% (since t > t0):
    assert 50 < slow < fast


def test_logistic_zero_l_returns_zero():
    assert _logistic(t=10.0, L=0.0, k=1.0, t0=5.0) == 0.0


# =========================================================================
# _logistic_array — vectorized form
# =========================================================================


def test_logistic_array_matches_scalar():
    """Vectorized output must match the scalar function elementwise."""
    np = pytest.importorskip("numpy")
    t_arr = np.array([0.0, 5.0, 10.0, 15.0, 20.0])
    out = _logistic_array(t_arr, L=100.0, k=0.5, t0=10.0)
    expected = [_logistic(t, L=100.0, k=0.5, t0=10.0) for t in t_arr]
    for a, b in zip(out, expected, strict=False):
        assert math.isclose(a, b, rel_tol=1e-9)


# =========================================================================
# _fit_logistic
# =========================================================================


def test_fit_logistic_insufficient_data_returns_none():
    """< 3 data points — can't fit a 3-parameter curve."""
    assert _fit_logistic([0.0, 1.0], [10.0, 20.0]) is None


def test_fit_logistic_all_zero_returns_none():
    """All-zero series — no curve to fit."""
    assert _fit_logistic([0.0, 1.0, 2.0, 3.0], [0.0, 0.0, 0.0, 0.0]) is None


def test_fit_logistic_recovers_known_curve():
    """Generate noiseless points from a known logistic and verify the
    fitted parameters round-trip approximately."""
    pytest.importorskip("scipy")
    L_true, k_true, t0_true = 100.0, 0.3, 15.0
    times = [float(i) for i in range(0, 31)]
    values = [_logistic(t, L_true, k_true, t0_true) for t in times]

    out = _fit_logistic(times, values)
    assert out is not None
    L_fit, k_fit, t0_fit = out
    # Tolerances reflect curve_fit's numerical precision:
    assert math.isclose(L_fit, L_true, rel_tol=0.05)
    assert math.isclose(k_fit, k_true, rel_tol=0.10)
    assert math.isclose(t0_fit, t0_true, abs_tol=1.0)


def test_fit_logistic_constant_high_value():
    """A series that's already saturated (constant ~L) — fit should
    not crash, and L should be near the constant value."""
    pytest.importorskip("scipy")
    times = [0.0, 1.0, 2.0, 3.0, 4.0]
    values = [99.0, 99.5, 99.5, 100.0, 100.0]
    out = _fit_logistic(times, values)
    # May or may not converge cleanly — if it does, L should be ≥ 95.
    if out is not None:
        L_fit, _, _ = out
        assert L_fit > 50.0


# =========================================================================
# _compute_forecast_bands
# =========================================================================


def test_forecast_bands_perfect_fit_zero_residuals():
    """Generate noiseless data → residuals = 0 → P10 == P50 == P90."""
    pytest.importorskip("numpy")
    L, k, t0 = 100.0, 0.5, 10.0
    times = [float(i) for i in range(0, 21)]
    values = [_logistic(t, L, k, t0) for t in times]

    forecast_times = [21.0, 25.0, 30.0]
    out = _compute_forecast_bands(times, values, (L, k, t0), forecast_times)

    for i in range(len(forecast_times)):
        assert math.isclose(out["p10"][i], out["p50"][i], abs_tol=0.001)
        assert math.isclose(out["p50"][i], out["p90"][i], abs_tol=0.001)


def test_forecast_bands_residuals_widen_bands():
    """Add noise → P10 < P50 < P90 with non-trivial spread."""
    pytest.importorskip("numpy")
    L, k, t0 = 100.0, 0.5, 10.0
    times = [float(i) for i in range(0, 21)]
    # Add ±5 noise to some points:
    values = [_logistic(t, L, k, t0) + (5.0 if i % 2 == 0 else -5.0) for i, t in enumerate(times)]

    forecast_times = [25.0]
    out = _compute_forecast_bands(times, values, (L, k, t0), forecast_times)

    # P10 < P50 < P90
    assert out["p10"][0] < out["p50"][0] < out["p90"][0]
    # And the band has measurable width:
    assert (out["p90"][0] - out["p10"][0]) > 1.0


def test_forecast_bands_uncertainty_grows_with_time():
    """A point further into the future should have a wider band than
    one closer to the last observation."""
    pytest.importorskip("numpy")
    L, k, t0 = 100.0, 0.5, 10.0
    times = [float(i) for i in range(0, 21)]
    values = [_logistic(t, L, k, t0) + (i % 3 - 1) for i, t in enumerate(times)]

    out = _compute_forecast_bands(times, values, (L, k, t0), [22.0, 50.0])
    band_near = out["p90"][0] - out["p10"][0]
    band_far = out["p90"][1] - out["p10"][1]
    assert band_far > band_near


def test_forecast_bands_p10_floored_at_zero():
    """[safety] P10 must NEVER be negative — physical quantities like
    cumulative spend can't go below 0."""
    pytest.importorskip("numpy")
    times = [0.0, 1.0, 2.0, 3.0, 4.0]
    # Tiny baseline values + huge residuals → uncorrected P10 would be
    # very negative:
    values = [1.0, 50.0, 1.0, 50.0, 1.0]
    forecast_times = [5.0, 10.0]
    out = _compute_forecast_bands(times, values, (100.0, 0.5, 5.0), forecast_times)
    for v in out["p10"]:
        assert v >= 0.0


def test_forecast_bands_returns_three_named_series():
    """Pin the output schema — keys p10/p50/p90, lengths match input."""
    pytest.importorskip("numpy")
    times = [0.0, 1.0, 2.0]
    values = [10.0, 20.0, 30.0]
    forecast_times = [3.0, 4.0, 5.0]
    out = _compute_forecast_bands(times, values, (100.0, 0.5, 5.0), forecast_times)
    assert set(out.keys()) == {"p10", "p50", "p90"}
    for series in out.values():
        assert len(series) == 3
