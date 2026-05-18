"""Tests for the productivity forecaster baseline-rate helpers.

The full forecaster runs time-series analysis when given historical
data; these tests pin the seed-data lookup helpers, the baseline-
forecast shape, and the cache management used by the loader.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.productivity.productivity_forecaster import (
    _baseline_forecast,
    _empty_forecast,
    _load_baseline_rates,
    clear_baseline_cache,
    get_baseline_rate,
    get_trade_summary,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Each test gets a fresh seed-load — module-level cache leaks
    otherwise."""
    clear_baseline_cache()
    yield
    clear_baseline_cache()


# =========================================================================
# _load_baseline_rates — seed file loading
# =========================================================================


def test_load_baseline_rates_returns_dict_indexed_by_trade():
    rates = _load_baseline_rates()
    assert isinstance(rates, dict)
    # Sitework is one of the canonical seeded trades:
    assert "sitework" in rates


def test_load_baseline_rates_each_entry_has_required_fields():
    """Pin the seed-data schema so a refactor doesn't silently drop a
    field that downstream code reads."""
    rates = _load_baseline_rates()
    for trade, entries in rates.items():
        for entry in entries:
            assert "activity_code" in entry, f"{trade} entry missing activity_code"
            assert "activity_name" in entry, f"{trade} entry missing activity_name"
            assert "trade" in entry
            assert "manhours_per_unit" in entry


def test_load_baseline_rates_cached():
    """Second call must hit the cache — same dict instance returned."""
    a = _load_baseline_rates()
    b = _load_baseline_rates()
    assert a is b


def test_clear_baseline_cache_forces_reload():
    a = _load_baseline_rates()
    clear_baseline_cache()
    b = _load_baseline_rates()
    # New dict instance after clear:
    assert a is not b


# =========================================================================
# get_baseline_rate
# =========================================================================


def test_get_baseline_rate_unknown_trade_returns_none():
    assert get_baseline_rate("alien_trade") is None


def test_get_baseline_rate_known_trade_returns_first_when_no_code():
    """Without activity_code, the helper returns the first entry for
    the trade."""
    rate = get_baseline_rate("sitework")
    assert rate is not None
    assert rate["trade"] == "sitework"


def test_get_baseline_rate_with_activity_code_filters():
    """Specific activity_code → returns the matching entry."""
    # SW-001 is documented in the seed:
    rate = get_baseline_rate("sitework", activity_code="SW-001")
    assert rate is not None
    assert rate["activity_code"] == "SW-001"


def test_get_baseline_rate_unknown_activity_code_falls_back_to_first():
    """If the activity_code isn't found, the helper returns the first
    entry rather than None — pin documented behavior."""
    rate = get_baseline_rate("sitework", activity_code="NEVER-EXISTS-999")
    assert rate is not None
    assert rate["trade"] == "sitework"


def test_get_baseline_rate_case_insensitive_trade():
    """Trade lookup must be case-insensitive — clients pass mixed case."""
    a = get_baseline_rate("sitework")
    b = get_baseline_rate("SITEWORK")
    assert a is not None
    assert b is not None
    assert a["activity_code"] == b["activity_code"]


# =========================================================================
# get_trade_summary
# =========================================================================


def test_get_trade_summary_unknown_trade_zero_count():
    out = get_trade_summary("alien_trade")
    assert out["activity_count"] == 0
    assert out["avg_manhours_per_unit"] == 0


def test_get_trade_summary_known_trade_has_activities():
    out = get_trade_summary("sitework")
    assert out["activity_count"] >= 1
    # Each activity in the summary must carry code/name/unit:
    for activity in out["activities"]:
        assert "code" in activity
        assert "name" in activity
        assert "unit" in activity


def test_get_trade_summary_avg_is_a_number():
    out = get_trade_summary("sitework")
    assert isinstance(out["avg_manhours_per_unit"], int | float)
    assert out["avg_manhours_per_unit"] >= 0


def test_get_trade_summary_carries_trade_name():
    out = get_trade_summary("sitework")
    assert out["trade"] == "sitework"


# =========================================================================
# _baseline_forecast — shape and content
# =========================================================================


def test_baseline_forecast_returns_required_keys():
    baseline = {
        "activity_code": "SW-001",
        "activity_name": "Excavation",
        "daily_output": 400,
        "unit": "CY",
        "manhours_per_unit": 0.06,
        "crew_size": 3,
        "crew_composition": {"foreman": 1},
    }
    out = _baseline_forecast("sitework", forecast_days=7, baseline=baseline)
    for key in (
        "project_id",
        "trade",
        "forecast_dates",
        "predicted_rates",
        "confidence_intervals",
        "trend",
        "baseline_rate",
    ):
        assert key in out


def test_baseline_forecast_predicted_rates_length_matches_days():
    baseline = {"daily_output": 400, "manhours_per_unit": 0.06}
    out = _baseline_forecast("sitework", forecast_days=14, baseline=baseline)
    assert len(out["forecast_dates"]) == 14
    assert len(out["predicted_rates"]) == 14
    assert len(out["confidence_intervals"]) == 14


def test_baseline_forecast_dates_start_tomorrow():
    """Forecast covers days 1..N from today (i.e. starts tomorrow)."""
    baseline = {"daily_output": 400, "manhours_per_unit": 0.06}
    out = _baseline_forecast("sitework", forecast_days=3, baseline=baseline)
    today = date.today()
    assert out["forecast_dates"][0] > today


def test_baseline_forecast_trend_is_baseline():
    baseline = {"daily_output": 400, "manhours_per_unit": 0.06}
    out = _baseline_forecast("sitework", forecast_days=3, baseline=baseline)
    assert out["trend"] == "baseline"


def test_baseline_forecast_predicted_rate_constant_one():
    """Documented behavior: baseline-mode forecast returns flat 1.0
    rates with ±30% confidence band (since there's no project-specific
    trend yet)."""
    baseline = {"daily_output": 400, "manhours_per_unit": 0.06}
    out = _baseline_forecast("sitework", forecast_days=5, baseline=baseline)
    assert all(r == 1.0 for r in out["predicted_rates"])
    for ci in out["confidence_intervals"]:
        assert ci["lower"] == 0.7
        assert ci["upper"] == 1.3


def test_baseline_forecast_propagates_baseline_metadata():
    baseline = {
        "activity_code": "CONC-001",
        "activity_name": "Concrete Slab Pour",
        "daily_output": 200,
        "unit": "CY",
        "manhours_per_unit": 0.5,
        "crew_size": 5,
        "crew_composition": {"finisher": 2, "laborer": 3},
    }
    out = _baseline_forecast("concrete", forecast_days=3, baseline=baseline)
    br = out["baseline_rate"]
    assert br["activity_code"] == "CONC-001"
    assert br["activity_name"] == "Concrete Slab Pour"
    assert br["daily_output"] == 200.0
    assert br["unit"] == "CY"
    assert br["manhours_per_unit"] == 0.5
    assert br["crew_size"] == 5.0
    assert br["crew_composition"] == {"finisher": 2, "laborer": 3}


# =========================================================================
# _empty_forecast — fallback for total-data-poverty
# =========================================================================


def test_empty_forecast_zero_length_arrays():
    out = _empty_forecast("unknown_trade", forecast_days=14)
    assert out["forecast_dates"] == []
    assert out["predicted_rates"] == []
    assert out["confidence_intervals"] == []


def test_empty_forecast_trend_insufficient_data():
    out = _empty_forecast("unknown_trade", forecast_days=14)
    assert out["trend"] == "insufficient_data"


def test_empty_forecast_carries_trade_name():
    out = _empty_forecast("alien_trade", forecast_days=7)
    assert out["trade"] == "alien_trade"
