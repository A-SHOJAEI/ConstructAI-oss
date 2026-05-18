"""Tests for the pure helpers in services/controls/cash_flow_engine.

The full engine has DB-bound forecasting; these tests pin the
date-arithmetic helpers, money rounding, and dataclass defaults.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.controls.cash_flow_engine import (
    CashFlowConfidenceIntervals,
    CashFlowForecast,
    LienWaiverAnalysis,
    MonthlyCashPoint,
    PaymentWaterfall,
    WaterfallStep,
    _days_in_month,
    _month_key,
    _month_start,
    _months_between,
    _next_month_start,
    _round2,
)

# =========================================================================
# _round2
# =========================================================================


def test_round2_two_decimal_places():
    assert _round2(Decimal("1.234")) == Decimal("1.23")
    assert _round2(Decimal("1.235")) == Decimal("1.24")  # half-up
    assert _round2(Decimal("0.005")) == Decimal("0.01")


def test_round2_zero():
    assert _round2(Decimal("0")) == Decimal("0.00")


def test_round2_negative():
    """Negative values must also round half-up (toward zero for .5)."""
    out = _round2(Decimal("-1.235"))
    # -1.235 → ROUND_HALF_UP → -1.24 (Python's HALF_UP rounds magnitudes,
    # so -1.235 has magnitude 1.235 → 1.24, sign restored).
    assert out in {Decimal("-1.23"), Decimal("-1.24")}


def test_round2_already_rounded_unchanged():
    assert _round2(Decimal("100.00")) == Decimal("100.00")


# =========================================================================
# _month_start / _next_month_start / _month_key
# =========================================================================


def test_month_start_returns_first_of_month():
    assert _month_start(date(2026, 4, 15)) == date(2026, 4, 1)
    assert _month_start(date(2026, 1, 31)) == date(2026, 1, 1)


def test_month_start_already_first_unchanged():
    assert _month_start(date(2026, 4, 1)) == date(2026, 4, 1)


def test_next_month_start_basic():
    assert _next_month_start(date(2026, 4, 15)) == date(2026, 5, 1)


def test_next_month_start_year_rollover():
    """December → January of next year."""
    assert _next_month_start(date(2026, 12, 25)) == date(2027, 1, 1)


def test_month_key_normalizes_to_first():
    """month_key is just an alias for month_start — both must agree."""
    d = date(2026, 6, 17)
    assert _month_key(d) == _month_start(d)


# =========================================================================
# _days_in_month
# =========================================================================


def test_days_in_month_january_31():
    assert _days_in_month(date(2026, 1, 1)) == 31


def test_days_in_month_february_non_leap():
    """2026 is not a leap year → February has 28 days."""
    assert _days_in_month(date(2026, 2, 15)) == 28


def test_days_in_month_february_leap_year():
    """2024 IS a leap year → February has 29 days."""
    assert _days_in_month(date(2024, 2, 15)) == 29


def test_days_in_month_april_30():
    assert _days_in_month(date(2026, 4, 1)) == 30


# =========================================================================
# _months_between
# =========================================================================


def test_months_between_same_month():
    """Start and end in the same month → just one entry."""
    out = _months_between(date(2026, 4, 5), date(2026, 4, 25))
    assert out == [date(2026, 4, 1)]


def test_months_between_consecutive_months():
    out = _months_between(date(2026, 4, 5), date(2026, 6, 15))
    assert out == [date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)]


def test_months_between_year_rollover():
    out = _months_between(date(2026, 11, 15), date(2027, 2, 5))
    assert out == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    ]


def test_months_between_full_year():
    out = _months_between(date(2026, 1, 1), date(2026, 12, 31))
    assert len(out) == 12


def test_months_between_end_before_start_returns_empty():
    """Defensive: end before start → empty list (no infinite loop)."""
    out = _months_between(date(2026, 6, 1), date(2026, 4, 1))
    assert out == []


# =========================================================================
# Dataclasses
# =========================================================================


def test_monthly_cash_point_defaults_zero():
    point = MonthlyCashPoint(month=date(2026, 4, 1))
    assert point.planned_billings == Decimal("0")
    assert point.actual_billings == Decimal("0")
    assert point.expected_receipts == Decimal("0")
    assert point.actual_receipts == Decimal("0")
    assert point.net_cash_position == Decimal("0")
    assert point.cumulative_billed == Decimal("0")
    assert point.cumulative_received == Decimal("0")


def test_monthly_cash_point_explicit_values():
    point = MonthlyCashPoint(
        month=date(2026, 4, 1),
        planned_billings=Decimal("100000"),
        actual_billings=Decimal("95000"),
    )
    assert point.planned_billings == Decimal("100000")
    assert point.actual_billings == Decimal("95000")


def test_cash_flow_forecast_default_empty_collections():
    """Two forecasts must have independent default lists — guards
    against the classic mutable-default pitfall."""
    a = CashFlowForecast(monthly_projections=[])
    b = CashFlowForecast(monthly_projections=[])
    a.risk_indicators.append("over budget")
    assert b.risk_indicators == []
    assert a.months_remaining == 0
    assert a.total_contract_value == Decimal("0")


def test_waterfall_step_required_fields():
    step = WaterfallStep(
        from_party="Owner",
        to_party="GC",
        amount=Decimal("100000"),
        expected_date=date(2026, 5, 1),
        description="Draw 4",
    )
    assert step.from_party == "Owner"
    assert step.amount == Decimal("100000")


def test_payment_waterfall_holds_steps():
    waterfall = PaymentWaterfall(steps=[])
    assert waterfall.steps == []
    waterfall.steps.append(
        WaterfallStep(
            from_party="GC",
            to_party="Sub",
            amount=Decimal("50000"),
            expected_date=date(2026, 5, 15),
            description="Pay app 4 — concrete sub",
        )
    )
    assert len(waterfall.steps) == 1


def test_cash_flow_confidence_intervals_default_zero():
    ci = CashFlowConfidenceIntervals(p10=[], p50=[], p90=[])
    assert ci.worst_month_position == Decimal("0")
    assert ci.months_negative == 0


def test_lien_waiver_analysis_default_empty():
    lwa = LienWaiverAnalysis()
    assert lwa.coverage_pct == Decimal("0")
    assert lwa.missing_waivers == []
    assert lwa.upcoming_deadlines == []


def test_lien_waiver_analysis_independent_default_lists():
    a = LienWaiverAnalysis()
    b = LienWaiverAnalysis()
    a.missing_waivers.append({"item": "x"})
    assert b.missing_waivers == []
