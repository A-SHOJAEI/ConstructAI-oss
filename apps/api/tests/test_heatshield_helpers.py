"""Tests for the heatshield service pure helpers.

Pin the OSHA-aligned heat threshold logic, simplified WBGT
approximation, break-schedule generation, and worker
acclimatization tracking.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.services.products.heatshield.service import (
    ABSENCE_RESET_DAYS,
    ACCLIMATIZATION_DAYS,
    THRESHOLD_HIGH_HEAT_F,
    THRESHOLD_INITIAL_F,
    advance_acclimatization,
    calculate_threshold,
    calculate_wbgt,
    check_acclimatization_reset,
    generate_break_schedule,
)

# =========================================================================
# Constants — pin OSHA-aligned values
# =========================================================================


def test_thresholds_canonical_values():
    """Pin the documented threshold values:
    80°F initial, 90°F high heat (NIOSH/OSHA-aligned)."""
    assert THRESHOLD_INITIAL_F == 80.0
    assert THRESHOLD_HIGH_HEAT_F == 90.0


def test_acclimatization_canonical_values():
    """Pin documented acclimatization period (14 days) + absence reset
    (7 days) — OSHA Heat Illness Prevention guidance."""
    assert ACCLIMATIZATION_DAYS == 14
    assert ABSENCE_RESET_DAYS == 7


# =========================================================================
# calculate_threshold
# =========================================================================


def test_threshold_below_initial_is_normal():
    assert calculate_threshold(75.0) == "normal"


def test_threshold_at_initial_boundary_is_initial():
    """At exactly 80°F, ≥ comparison passes → "initial"."""
    assert calculate_threshold(80.0) == "initial"


def test_threshold_between_initial_and_high():
    assert calculate_threshold(85.0) == "initial"


def test_threshold_at_high_heat_boundary():
    """At exactly 90°F, ≥ check passes → "high_heat"."""
    assert calculate_threshold(90.0) == "high_heat"


def test_threshold_above_high_heat():
    assert calculate_threshold(105.0) == "high_heat"


def test_threshold_uses_custom_config_when_provided():
    """Custom config overrides — refactor must not silently fall
    back to defaults when config is supplied."""
    config = SimpleNamespace(threshold_initial_f=70.0, threshold_high_heat_f=85.0)
    assert calculate_threshold(72.0, config=config) == "initial"
    assert calculate_threshold(86.0, config=config) == "high_heat"
    assert calculate_threshold(65.0, config=config) == "normal"


# =========================================================================
# calculate_wbgt
# =========================================================================


def test_wbgt_returns_float():
    out = calculate_wbgt(temp_f=85.0, humidity_pct=50.0)
    assert isinstance(out, float)


def test_wbgt_higher_humidity_increases_index():
    """Holding temp constant, higher humidity → higher WBGT (the
    whole point of WBGT vs dry-bulb temperature)."""
    dry = calculate_wbgt(temp_f=90.0, humidity_pct=20.0)
    humid = calculate_wbgt(temp_f=90.0, humidity_pct=80.0)
    assert humid > dry


def test_wbgt_clamps_humidity_to_100():
    """Defensive: a sensor reporting 110% RH (data error) must not
    crash; must produce a finite result."""
    out = calculate_wbgt(temp_f=85.0, humidity_pct=110.0)
    assert isinstance(out, float)
    # Should match the 100% case:
    expected = calculate_wbgt(temp_f=85.0, humidity_pct=100.0)
    assert out == expected


def test_wbgt_clamps_humidity_to_0():
    """Negative humidity (data error) clamped to 0."""
    out = calculate_wbgt(temp_f=85.0, humidity_pct=-10.0)
    expected = calculate_wbgt(temp_f=85.0, humidity_pct=0.0)
    assert out == expected


def test_wbgt_higher_wind_reduces_globe_temp():
    """Wind speed > 5 mph reduces globe temp via cooling — pin the
    monotonic effect."""
    no_wind = calculate_wbgt(temp_f=95.0, humidity_pct=50.0, wind_speed_mph=2.0)
    with_wind = calculate_wbgt(temp_f=95.0, humidity_pct=50.0, wind_speed_mph=15.0)
    assert with_wind <= no_wind


def test_wbgt_wind_correction_capped():
    """Wind correction is capped at 3°C — extreme wind shouldn't
    drive WBGT to absurd lows."""
    moderate = calculate_wbgt(temp_f=95.0, humidity_pct=50.0, wind_speed_mph=15.0)
    extreme = calculate_wbgt(temp_f=95.0, humidity_pct=50.0, wind_speed_mph=100.0)
    # Extreme wind should not drop WBGT by more than ~3°C * 9/5 ≈ 5.4°F:
    assert moderate - extreme < 6.0


def test_wbgt_rounded_to_one_decimal():
    """Result rounded to 1dp for clean reporting."""
    out = calculate_wbgt(temp_f=85.0, humidity_pct=60.0)
    assert round(out, 1) == out


# =========================================================================
# generate_break_schedule
# =========================================================================


def test_break_schedule_normal_4hour_intervals():
    """Normal threshold: every 4 hours, 10-minute breaks. From 7:00
    over a 10-hour day → breaks at 11:00, 15:00."""
    out = generate_break_schedule("07:00", "normal")
    assert len(out) == 2
    assert out[0].duration_minutes == 10
    assert out[0].scheduled_time == "11:00"
    assert out[1].scheduled_time == "15:00"


def test_break_schedule_initial_2hour_intervals():
    """Initial heat: every 2 hours, 15-minute breaks."""
    out = generate_break_schedule("07:00", "initial")
    # 10-hour day, breaks at 9, 11, 13, 15, 17 — 5 breaks (last < 17:00).
    assert len(out) >= 4
    for item in out:
        assert item.duration_minutes == 15
        assert item.threshold_level == "initial"


def test_break_schedule_high_heat_1hour_intervals():
    """High heat: every 1 hour, 15-minute breaks."""
    out = generate_break_schedule("07:00", "high_heat")
    # Breaks at 8, 9, ..., 16 — should be 9.
    assert len(out) >= 8
    for item in out:
        assert item.duration_minutes == 15
        assert item.threshold_level == "high_heat"


def test_break_schedule_invalid_start_time_defaults_to_7am():
    """Garbage start time → defaults to 7:00."""
    out = generate_break_schedule("invalid", "normal")
    assert out[0].scheduled_time == "11:00"


def test_break_schedule_explicit_start_minute():
    out = generate_break_schedule("06:30", "normal")
    assert out[0].scheduled_time == "10:30"


def test_break_schedule_status_default_scheduled():
    out = generate_break_schedule("07:00", "normal")
    for item in out:
        assert item.status == "scheduled"


# =========================================================================
# check_acclimatization_reset
# =========================================================================


def _worker(
    *,
    last_work_date=None,
    acclimatization_day: int = 1,
    status: str = "acclimatizing",
    start_date=None,
):
    """Build a fake worker with the fields the helper needs."""
    return SimpleNamespace(
        last_work_date=last_work_date,
        acclimatization_day=acclimatization_day,
        status=status,
        start_date=start_date,
    )


def test_check_reset_no_last_work_date_returns_false():
    """Brand-new worker: no last_work_date → no reset."""
    worker = _worker(last_work_date=None)
    today = date(2026, 4, 26)
    assert check_acclimatization_reset(worker, today) is False


def test_check_reset_short_absence_no_reset():
    """6-day absence (< 7) → no reset."""
    worker = _worker(last_work_date=date(2026, 4, 20))
    today = date(2026, 4, 26)
    # 6 days < ABSENCE_RESET_DAYS (7)
    assert check_acclimatization_reset(worker, today) is False


def test_check_reset_at_threshold_resets():
    """7-day absence → reset acclimatization."""
    worker = _worker(last_work_date=date(2026, 4, 19))  # 7 days ago
    today = date(2026, 4, 26)
    assert check_acclimatization_reset(worker, today) is True
    assert worker.acclimatization_day == 1
    assert worker.status == "reset"
    assert worker.start_date == today


def test_check_reset_long_absence_resets():
    worker = _worker(last_work_date=date(2026, 1, 1))
    today = date(2026, 4, 26)
    assert check_acclimatization_reset(worker, today) is True


def test_check_reset_already_reset_no_double_reset():
    """Worker already in "reset" status — don't re-reset."""
    worker = _worker(last_work_date=date(2026, 4, 1), status="reset", acclimatization_day=5)
    today = date(2026, 4, 26)
    out = check_acclimatization_reset(worker, today)
    # Already reset → returns False (didn't change state):
    assert out is False
    assert worker.acclimatization_day == 5  # unchanged


# =========================================================================
# advance_acclimatization
# =========================================================================


def test_advance_acclimatization_increments():
    worker = _worker(acclimatization_day=5)
    advance_acclimatization(worker)
    assert worker.acclimatization_day == 6


def test_advance_acclimatization_caps_at_14():
    """Once acclimatized (day 14), don't keep incrementing."""
    worker = _worker(acclimatization_day=14)
    advance_acclimatization(worker)
    assert worker.acclimatization_day == 14


def test_advance_acclimatization_marks_acclimatized_at_14():
    worker = _worker(acclimatization_day=13, status="acclimatizing")
    advance_acclimatization(worker)
    assert worker.acclimatization_day == 14
    assert worker.status == "acclimatized"


def test_advance_acclimatization_below_14_keeps_acclimatizing_status():
    worker = _worker(acclimatization_day=5, status="acclimatizing")
    advance_acclimatization(worker)
    assert worker.acclimatization_day == 6
    assert worker.status == "acclimatizing"
