"""Tests for the pure helpers in services/procurement/price_forecaster.

The full forecaster fetches FRED + BLS data; these tests pin the
local pure logic: seasonal adjustment, ARIMA-fallback linear trend,
trend categorization, FRED series lookups.
"""

from __future__ import annotations

from app.services.procurement.price_forecaster import (
    _SEASONAL_FACTORS,
    FRED_SERIES_MAP,
    _apply_seasonal_adjustments,
    _determine_trend,
    _linear_trend_forecast,
)

# =========================================================================
# FRED_SERIES_MAP / _SEASONAL_FACTORS — invariants
# =========================================================================


def test_fred_series_map_canonical_categories():
    """Pin documented FRED series → category mappings so a refactor
    can't quietly drop a tracked material."""
    categories = {meta["category"] for meta in FRED_SERIES_MAP.values()}
    # Materials we explicitly forecast:
    expected_materials = {
        "structural_steel",
        "gypsum",
        "insulation",
        "plywood",
        "steel_pipe",
    }
    assert expected_materials.issubset(categories)
    # Plus macro indicators:
    assert "cpi_urban" in categories
    assert "construction_spending" in categories


def test_seasonal_factors_canonical_materials():
    """Materials with documented seasonal patterns. Concrete (warm-
    season construction) and asphalt (paving season) must both be
    present — these are the two strongest seasonal effects."""
    assert "concrete" in _SEASONAL_FACTORS
    assert "asphalt" in _SEASONAL_FACTORS
    assert "steel" in _SEASONAL_FACTORS
    assert "lumber" in _SEASONAL_FACTORS


def test_concrete_seasonal_peaks_in_summer():
    """Concrete demand peaks during the warm construction season —
    factor for July (month 7) should be > 1.0 (price uplift)."""
    july_factor = _SEASONAL_FACTORS["concrete"][7]
    assert july_factor > 1.0


# =========================================================================
# _apply_seasonal_adjustments
# =========================================================================


def test_seasonal_adjustment_applied_for_known_material():
    """A July forecast for concrete should be scaled by July's
    factor (1.10x baseline)."""
    forecasts = [
        {
            "date": "2026-07-15",
            "forecast_value": 100.0,
            "lower_bound": 95.0,
            "upper_bound": 105.0,
        }
    ]
    out = _apply_seasonal_adjustments(forecasts, "concrete")
    # July factor for concrete is 1.10
    assert out[0]["forecast_value"] == 110.0
    assert out[0]["lower_bound"] == 104.5
    assert out[0]["upper_bound"] == 115.5


def test_seasonal_adjustment_unknown_material_passes_through():
    """Material with no seasonal pattern → forecasts returned unchanged."""
    forecasts = [
        {
            "date": "2026-07-15",
            "forecast_value": 100.0,
            "lower_bound": 95.0,
            "upper_bound": 105.0,
        }
    ]
    out = _apply_seasonal_adjustments(forecasts, "unobtanium")
    assert out == forecasts


def test_seasonal_adjustment_substring_match():
    """``concrete_foundation`` should match the parent ``concrete`` band
    via substring containment (same pattern as cost_database)."""
    forecasts = [
        {
            "date": "2026-07-15",
            "forecast_value": 100.0,
            "lower_bound": 95.0,
            "upper_bound": 105.0,
        }
    ]
    out = _apply_seasonal_adjustments(forecasts, "concrete_foundation")
    # Should still apply concrete's July factor of 1.10
    assert out[0]["forecast_value"] == 110.0


def test_seasonal_adjustment_off_season_no_uplift():
    """Concrete in January (month 1) — not in concrete's seasonal
    pattern → factor defaults to 1.0 → unchanged."""
    forecasts = [
        {
            "date": "2026-01-15",
            "forecast_value": 100.0,
            "lower_bound": 95.0,
            "upper_bound": 105.0,
        }
    ]
    out = _apply_seasonal_adjustments(forecasts, "concrete")
    assert out[0]["forecast_value"] == 100.0  # 1.0x factor


def test_seasonal_adjustment_malformed_date_passes_through():
    """A forecast with a malformed date string should be appended
    unchanged — never crash on bad input."""
    forecasts = [
        {
            "date": "not-a-date",
            "forecast_value": 100.0,
            "lower_bound": 95.0,
            "upper_bound": 105.0,
        }
    ]
    out = _apply_seasonal_adjustments(forecasts, "concrete")
    assert out == forecasts


def test_seasonal_adjustment_case_insensitive():
    forecasts = [
        {
            "date": "2026-07-15",
            "forecast_value": 100.0,
            "lower_bound": 95.0,
            "upper_bound": 105.0,
        }
    ]
    out = _apply_seasonal_adjustments(forecasts, "CONCRETE")
    assert out[0]["forecast_value"] == 110.0


# =========================================================================
# _linear_trend_forecast
# =========================================================================


def test_linear_trend_empty_data_uses_zero_baseline():
    """Empty historical data — fallback path returns flat-zero forecasts."""
    forecasts, slope = _linear_trend_forecast([], horizon_months=3)
    assert len(forecasts) == 3
    assert slope == 0.0
    assert all(f["forecast_value"] == 0.0 for f in forecasts)


def test_linear_trend_single_data_point_uses_last_value():
    """One historical point — can't compute a slope, so the fallback
    extrapolates the last known value forward with ±5% bands."""
    forecasts, slope = _linear_trend_forecast(
        [{"date": "2026-01-15", "price_index": 100.0}],
        horizon_months=3,
    )
    assert slope == 0.0
    assert all(f["forecast_value"] == 100.0 for f in forecasts)
    # ±5% bands:
    assert forecasts[0]["lower_bound"] == 95.0
    assert forecasts[0]["upper_bound"] == 105.0


def test_linear_trend_rising_series_produces_positive_slope():
    historical = [
        {"date": "2026-01-15", "price_index": 100.0},
        {"date": "2026-02-15", "price_index": 102.0},
        {"date": "2026-03-15", "price_index": 104.0},
        {"date": "2026-04-15", "price_index": 106.0},
    ]
    forecasts, slope = _linear_trend_forecast(historical, horizon_months=2)
    assert slope == 2.0  # +2 per month exactly
    assert len(forecasts) == 2
    # First forecast should be 108 (next step in trend), bounds widen
    # with horizon
    assert forecasts[0]["forecast_value"] == 108.0


def test_linear_trend_falling_series_produces_negative_slope():
    historical = [
        {"date": "2026-01-15", "price_index": 200.0},
        {"date": "2026-02-15", "price_index": 195.0},
        {"date": "2026-03-15", "price_index": 190.0},
        {"date": "2026-04-15", "price_index": 185.0},
    ]
    _, slope = _linear_trend_forecast(historical, horizon_months=2)
    assert slope == -5.0


def test_linear_trend_flat_series_produces_zero_slope():
    historical = [
        {"date": "2026-01-15", "price_index": 100.0},
        {"date": "2026-02-15", "price_index": 100.0},
        {"date": "2026-03-15", "price_index": 100.0},
    ]
    _, slope = _linear_trend_forecast(historical, horizon_months=1)
    assert slope == 0.0


def test_linear_trend_confidence_widens_with_horizon():
    """Confidence band must widen as we extrapolate further from the
    last observation — the further out, the less certain the forecast."""
    historical = [
        {"date": "2026-01-15", "price_index": 100.0},
        {"date": "2026-02-15", "price_index": 102.0},
        {"date": "2026-03-15", "price_index": 105.0},
        {"date": "2026-04-15", "price_index": 103.0},
    ]
    forecasts, _ = _linear_trend_forecast(historical, horizon_months=12)
    # First-month band:
    band_first = forecasts[0]["upper_bound"] - forecasts[0]["lower_bound"]
    # Last-month band (12 months out):
    band_last = forecasts[-1]["upper_bound"] - forecasts[-1]["lower_bound"]
    assert band_last > band_first


# =========================================================================
# _determine_trend
# =========================================================================


def test_determine_trend_rising_above_threshold():
    """Slope > 0.5 → "rising"."""
    assert _determine_trend(1.0) == "rising"
    assert _determine_trend(0.6) == "rising"


def test_determine_trend_falling_below_threshold():
    """Slope < -0.5 → "falling"."""
    assert _determine_trend(-1.0) == "falling"
    assert _determine_trend(-0.6) == "falling"


def test_determine_trend_stable_near_zero():
    """|slope| ≤ 0.5 → "stable"."""
    assert _determine_trend(0.0) == "stable"
    assert _determine_trend(0.3) == "stable"
    assert _determine_trend(-0.3) == "stable"
    # Boundary — exactly 0.5 is stable (strict gt):
    assert _determine_trend(0.5) == "stable"
    assert _determine_trend(-0.5) == "stable"
