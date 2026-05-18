"""Tests for the pure helpers in services/productivity/workforce_analytics.

Pin the synchronous compute helpers — productivity-metric calculation
(trend detection from manhours/unit) and overtime prediction (risk-
band recommendations).
"""

from __future__ import annotations

from app.services.productivity.workforce_analytics import (
    OVERTIME_RATE_MULTIPLIER,
    STANDARD_HOURS_PER_DAY,
    OvertimePrediction,
    TradeProductivityMetric,
    calculate_productivity_metrics,
    predict_overtime,
)

# =========================================================================
# Constants
# =========================================================================


def test_standard_hours_per_day_is_eight():
    """Pin the canonical 8h workday — refactor should not silently
    change this without explicit policy update."""
    assert STANDARD_HOURS_PER_DAY == 8.0


def test_overtime_rate_multiplier_is_one_point_five():
    """1.5x is the standard US overtime rate (FLSA). Pin it."""
    assert OVERTIME_RATE_MULTIPLIER == 1.5


# =========================================================================
# calculate_productivity_metrics
# =========================================================================


def test_productivity_metrics_empty_input():
    assert calculate_productivity_metrics([]) == []


def test_productivity_metrics_groups_by_trade_and_activity():
    """Records with same trade but different activity_type → separate
    metrics."""
    records = [
        {
            "trade": "concrete",
            "activity_type": "slab",
            "actual_units": 100,
            "crew_size": 4,
            "work_hours": 8,
            "unit_of_measure": "CY",
        },
        {
            "trade": "concrete",
            "activity_type": "wall",
            "actual_units": 50,
            "crew_size": 4,
            "work_hours": 8,
            "unit_of_measure": "CY",
        },
        {
            "trade": "steel",
            "actual_units": 20,
            "crew_size": 3,
            "work_hours": 8,
            "unit_of_measure": "TN",
        },
    ]
    metrics = calculate_productivity_metrics(records)
    assert len(metrics) == 3
    trades = {m.trade for m in metrics}
    assert trades == {"concrete", "steel"}


def test_productivity_metrics_skips_zero_actual_units():
    """Records with actual_units=0 produce no manhours/unit value;
    if all records skip, the trade gets no metric."""
    records = [
        {"trade": "concrete", "actual_units": 0, "crew_size": 4, "work_hours": 8},
        {"trade": "concrete", "actual_units": 0, "crew_size": 4, "work_hours": 8},
    ]
    assert calculate_productivity_metrics(records) == []


def test_productivity_metrics_avg_calculation():
    """Crew of 4 × 8 hours = 32 manhours. 100 units → 0.32 mh/unit."""
    records = [
        {"trade": "concrete", "actual_units": 100, "crew_size": 4, "work_hours": 8},
    ]
    metrics = calculate_productivity_metrics(records)
    assert metrics[0].avg_manhours_per_unit == 0.32


def test_productivity_metrics_default_work_hours():
    """If work_hours is missing, defaults to STANDARD_HOURS_PER_DAY (8)."""
    records = [
        {"trade": "concrete", "actual_units": 100, "crew_size": 4},
    ]
    metrics = calculate_productivity_metrics(records)
    assert metrics[0].avg_manhours_per_unit == 0.32  # 4×8/100


def test_productivity_metrics_trend_improving_with_decreasing_mh():
    """Decreasing manhours/unit over time = improving productivity."""
    records = [
        {"trade": "concrete", "actual_units": 50, "crew_size": 4, "work_hours": 8},  # 0.64
        {"trade": "concrete", "actual_units": 80, "crew_size": 4, "work_hours": 8},  # 0.40
        {"trade": "concrete", "actual_units": 100, "crew_size": 4, "work_hours": 8},  # 0.32
        {"trade": "concrete", "actual_units": 130, "crew_size": 4, "work_hours": 8},  # 0.246
    ]
    metrics = calculate_productivity_metrics(records)
    assert metrics[0].trend == "improving"
    assert metrics[0].trend_slope < 0


def test_productivity_metrics_trend_declining_with_increasing_mh():
    """Increasing manhours/unit = declining productivity."""
    records = [
        {"trade": "concrete", "actual_units": 130, "crew_size": 4, "work_hours": 8},
        {"trade": "concrete", "actual_units": 100, "crew_size": 4, "work_hours": 8},
        {"trade": "concrete", "actual_units": 80, "crew_size": 4, "work_hours": 8},
        {"trade": "concrete", "actual_units": 50, "crew_size": 4, "work_hours": 8},
    ]
    metrics = calculate_productivity_metrics(records)
    assert metrics[0].trend == "declining"
    assert metrics[0].trend_slope > 0


def test_productivity_metrics_trend_stable_with_few_records():
    """< 3 records: not enough data for trend, defaults to "stable"."""
    records = [
        {"trade": "concrete", "actual_units": 100, "crew_size": 4, "work_hours": 8},
        {"trade": "concrete", "actual_units": 100, "crew_size": 4, "work_hours": 8},
    ]
    metrics = calculate_productivity_metrics(records)
    assert metrics[0].trend == "stable"


def test_productivity_metrics_unit_of_measure_propagated():
    records = [
        {
            "trade": "concrete",
            "actual_units": 100,
            "crew_size": 4,
            "work_hours": 8,
            "unit_of_measure": "CY",
        },
    ]
    metrics = calculate_productivity_metrics(records)
    assert metrics[0].unit_of_measure == "CY"


def test_productivity_metrics_default_trade_unknown():
    records = [
        {"actual_units": 100, "crew_size": 4, "work_hours": 8},  # no trade
    ]
    metrics = calculate_productivity_metrics(records)
    assert metrics[0].trade == "unknown"


def test_productivity_metrics_returns_dataclass():
    records = [
        {"trade": "concrete", "actual_units": 100, "crew_size": 4, "work_hours": 8},
    ]
    metrics = calculate_productivity_metrics(records)
    assert isinstance(metrics[0], TradeProductivityMetric)


# =========================================================================
# predict_overtime
# =========================================================================


def test_predict_overtime_empty_returns_low_risk():
    out = predict_overtime(remaining_activities=[], available_workforce={})
    assert isinstance(out, OvertimePrediction)
    assert out.predicted_overtime_hours == 0.0
    assert out.overtime_pct == 0.0
    assert out.risk_level == "low"
    assert "no remaining" in out.recommendation.lower()


def test_predict_overtime_within_capacity_low_risk():
    """1000 manhours over 30 days with 5 workers × 8h × 30 = 1200
    standard hours available → no overtime needed."""
    activities = [
        {"trade": "concrete", "remaining_manhours": 1000, "duration_days": 30},
    ]
    workforce = {"concrete": 5}
    out = predict_overtime(activities, workforce)
    assert out.predicted_overtime_hours == 0.0
    assert out.risk_level == "low"


def test_predict_overtime_critical_risk_high_overtime():
    """Way too much remaining work for the available crew → critical."""
    activities = [
        {"trade": "concrete", "remaining_manhours": 5000, "duration_days": 30},
    ]
    workforce = {"concrete": 5}  # 5 × 8 × 30 = 1200 standard hrs
    out = predict_overtime(activities, workforce)
    # 5000 - 1200 = 3800 overtime hours / 5000 = 76% overtime
    assert out.predicted_overtime_hours > 3000
    assert out.overtime_pct >= 30
    assert out.risk_level == "critical"


def test_predict_overtime_high_risk_band():
    """20% ≤ overtime < 30% → high."""
    activities = [
        {"trade": "concrete", "remaining_manhours": 1500, "duration_days": 30},
    ]
    workforce = {"concrete": 5}  # 1200 standard hrs → 300 overtime / 1500 = 20%
    out = predict_overtime(activities, workforce)
    assert out.overtime_pct >= 20
    assert out.risk_level in ("high", "critical")


def test_predict_overtime_moderate_risk_band():
    """10% ≤ overtime < 20% → moderate."""
    activities = [
        {"trade": "concrete", "remaining_manhours": 1350, "duration_days": 30},
    ]
    workforce = {"concrete": 5}  # 1200 std → 150 overtime / 1350 = 11%
    out = predict_overtime(activities, workforce)
    assert 10 <= out.overtime_pct < 20
    assert out.risk_level == "moderate"


def test_predict_overtime_schedule_compression_increases_overtime():
    """Compressing the schedule by 30% reduces standard hours available
    → more overtime needed."""
    activities = [
        {"trade": "concrete", "remaining_manhours": 1000, "duration_days": 30},
    ]
    workforce = {"concrete": 5}
    no_compress = predict_overtime(activities, workforce, schedule_compression_pct=0.0)
    compressed = predict_overtime(activities, workforce, schedule_compression_pct=30.0)
    assert compressed.predicted_overtime_hours >= no_compress.predicted_overtime_hours


def test_predict_overtime_compression_capped_at_50_pct():
    """Compression > 50% should be clamped to 50% — pin the
    documented cap so massive crash schedules don't get arithmetic
    errors."""
    activities = [
        {"trade": "concrete", "remaining_manhours": 1000, "duration_days": 30},
    ]
    workforce = {"concrete": 5}
    out_99 = predict_overtime(activities, workforce, schedule_compression_pct=99.0)
    out_50 = predict_overtime(activities, workforce, schedule_compression_pct=50.0)
    # Both clamped to same effective value — overtime predictions match:
    assert out_99.predicted_overtime_hours == out_50.predicted_overtime_hours


def test_predict_overtime_explicit_hourly_rate_used():
    """If avg_hourly_rate provided, it's used directly (not the trade
    table) for cost estimation."""
    activities = [
        {"trade": "concrete", "remaining_manhours": 5000, "duration_days": 30},
    ]
    workforce = {"concrete": 5}
    out = predict_overtime(activities, workforce, avg_hourly_rate=100.0)
    expected_cost = out.predicted_overtime_hours * 100.0 * OVERTIME_RATE_MULTIPLIER
    assert out.estimated_overtime_cost == round(expected_cost, 2)


def test_predict_overtime_total_remaining_correct():
    activities = [
        {"trade": "a", "remaining_manhours": 100, "duration_days": 5},
        {"trade": "b", "remaining_manhours": 200, "duration_days": 5},
    ]
    workforce = {"a": 2, "b": 3}
    out = predict_overtime(activities, workforce)
    assert out.total_remaining_manhours == 300.0


def test_predict_overtime_overtime_rate_multiplier_propagated():
    activities = [
        {"trade": "a", "remaining_manhours": 100, "duration_days": 1},
    ]
    workforce = {"a": 1}  # only 8 standard hours → 92 overtime
    out = predict_overtime(activities, workforce, avg_hourly_rate=50.0)
    assert out.overtime_rate_multiplier == OVERTIME_RATE_MULTIPLIER


def test_predict_overtime_returns_dataclass():
    out = predict_overtime([], {})
    assert isinstance(out, OvertimePrediction)
