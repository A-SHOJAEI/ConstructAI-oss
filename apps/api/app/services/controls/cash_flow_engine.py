"""Predictive cash flow engine: pure math functions for cash flow analysis.

All functions are pure (no DB access, no async) for easy testing.
Uses Decimal for all monetary calculations to avoid floating-point errors.
"""

from __future__ import annotations

import calendar
import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

logger = logging.getLogger(__name__)

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False
    logger.warning("numpy not installed; Monte Carlo cash flow simulation unavailable")

ZERO = Decimal("0")
HUNDRED = Decimal("100")
TWO_PLACES = Decimal("0.01")


def _round2(value: Decimal) -> Decimal:
    """Round to 2 decimal places (money)."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _month_start(d: date) -> date:
    """Return the first day of the month for the given date."""
    return date(d.year, d.month, 1)


def _next_month_start(d: date) -> date:
    """Return the first day of the next month."""
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _month_key(d: date) -> date:
    """Normalize a date to its month key (first of month)."""
    return date(d.year, d.month, 1)


def _days_in_month(d: date) -> int:
    """Return the number of days in the month containing date d."""
    return calendar.monthrange(d.year, d.month)[1]


def _months_between(start: date, end: date) -> list[date]:
    """Generate a list of month-start dates from start to end (inclusive)."""
    months: list[date] = []
    current = _month_start(start)
    end_month = _month_start(end)
    while current <= end_month:
        months.append(current)
        current = _next_month_start(current)
    return months


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MonthlyCashPoint:
    """Cash flow data for a single calendar month."""

    month: date
    planned_billings: Decimal = ZERO
    actual_billings: Decimal = ZERO
    expected_receipts: Decimal = ZERO
    actual_receipts: Decimal = ZERO
    net_cash_position: Decimal = ZERO
    cumulative_billed: Decimal = ZERO
    cumulative_received: Decimal = ZERO


@dataclass
class CashFlowForecast:
    """Complete cash flow forecast with projections and risk indicators."""

    monthly_projections: list[MonthlyCashPoint]
    total_contract_value: Decimal = ZERO
    total_billed: Decimal = ZERO
    total_received: Decimal = ZERO
    retainage_held: Decimal = ZERO
    months_remaining: int = 0
    risk_indicators: list[str] = field(default_factory=list)


@dataclass
class WaterfallStep:
    """A single step in the payment waterfall."""

    from_party: str
    to_party: str
    amount: Decimal
    expected_date: date
    description: str


@dataclass
class PaymentWaterfall:
    """Payment waterfall showing money flow from owner to subs."""

    steps: list[WaterfallStep]


@dataclass
class CashFlowConfidenceIntervals:
    """Monte Carlo-derived confidence intervals for cash positions."""

    p10: list[Decimal]
    p50: list[Decimal]
    p90: list[Decimal]
    worst_month_position: Decimal = ZERO
    months_negative: int = 0


@dataclass
class LienWaiverAnalysis:
    """Analysis of lien waiver coverage against pay applications."""

    coverage_pct: Decimal = ZERO
    missing_waivers: list[dict] = field(default_factory=list)
    upcoming_deadlines: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Planned cash curve
# ---------------------------------------------------------------------------


def compute_planned_cash_curve(
    sov_items: list[dict],
    schedule_activities: list[dict],
    project_start: date,
    project_end: date,
    distribution: str = "linear",
) -> list[MonthlyCashPoint]:
    """Distribute SOV values across months based on activity schedules.

    Each sov_item dict must have: scheduled_value, item_number (or csi_code).
    Each schedule_activity dict must have: early_start (date), early_finish (date),
    and optionally activity_code/wbs_code to match against SOV items.

    Activities spanning multiple months are pro-rated by calendar days.
    SOV items without a matching activity are spread evenly across the project.

    SV-10: The ``distribution`` parameter controls the billing shape:
      - "linear": Even distribution across the activity duration (default).
      - "s_curve": Logistic S-curve so early months have less billing and
        middle months have more — typical construction billing pattern.
    """
    months = _months_between(project_start, project_end)
    if not months:
        return []

    monthly_planned: dict[date, Decimal] = {m: ZERO for m in months}

    # Build activity lookup by activity_code for matching to SOV items
    activity_by_code: dict[str, dict] = {}
    for act in schedule_activities:
        code = act.get("activity_code") or act.get("wbs_code") or ""
        if code:
            activity_by_code[code] = act

    for sov in sov_items:
        scheduled_value = Decimal(str(sov.get("scheduled_value", 0)))
        if scheduled_value <= ZERO:
            continue

        # Try to find a matching activity
        item_code = sov.get("csi_code") or sov.get("item_number") or ""
        matched_activity = activity_by_code.get(item_code)

        if (
            matched_activity
            and matched_activity.get("early_start")
            and matched_activity.get("early_finish")
        ):
            act_start = matched_activity["early_start"]
            act_finish = matched_activity["early_finish"]
            if isinstance(act_start, str):
                act_start = date.fromisoformat(act_start)
            if isinstance(act_finish, str):
                act_finish = date.fromisoformat(act_finish)

            # Clamp to project bounds
            act_start = max(act_start, project_start)
            act_finish = min(act_finish, project_end)

            total_days = (act_finish - act_start).days + 1
            if total_days <= 0:
                total_days = 1

            # Distribute value across months the activity spans
            current = _month_start(act_start)
            while current <= _month_start(act_finish):
                month_end_day = date(current.year, current.month, _days_in_month(current))
                overlap_start = max(act_start, current)
                overlap_end = min(act_finish, month_end_day)
                overlap_days = (overlap_end - overlap_start).days + 1

                if overlap_days > 0 and current in monthly_planned:
                    fraction = Decimal(str(overlap_days)) / Decimal(str(total_days))
                    monthly_planned[current] += _round2(scheduled_value * fraction)

                current = _next_month_start(current)
        else:
            # No matching activity: spread evenly across all project months
            per_month = _round2(scheduled_value / Decimal(str(len(months))))
            remainder = scheduled_value - per_month * len(months)
            for i, m in enumerate(months):
                amount = per_month
                if i == len(months) - 1:
                    amount = per_month + remainder  # put rounding remainder in last month
                monthly_planned[m] += _round2(amount)

    # SV-10: Apply S-curve distribution if requested.
    # Uses a logistic function 1/(1+exp(-k*(t-midpoint))) to reshape the
    # cumulative billing so early months have less and middle months have more.
    if distribution == "s_curve" and len(months) > 2:
        total_planned = sum(monthly_planned.values())
        if total_planned > ZERO:
            n = len(months)
            midpoint = (n - 1) / 2.0
            k = 6.0 / max(n, 1)  # steepness — 6/n gives a good S shape

            # Compute raw S-curve cumulative values at each month boundary
            raw_cum = []
            for i in range(n):
                raw_cum.append(1.0 / (1.0 + math.exp(-k * (i - midpoint))))

            # Normalize so that the S-curve sums to total_planned
            raw_min = raw_cum[0]
            raw_max = raw_cum[-1]
            raw_range = raw_max - raw_min if raw_max != raw_min else 1.0

            s_cumulative = [
                Decimal(str((rc - raw_min) / raw_range)) * total_planned for rc in raw_cum
            ]

            # Derive monthly billings from cumulative differences
            s_monthly: list[Decimal] = []
            for i in range(n):
                if i == 0:
                    s_monthly.append(_round2(s_cumulative[i]))
                else:
                    s_monthly.append(_round2(s_cumulative[i] - s_cumulative[i - 1]))

            # Adjust rounding remainder into the last month
            s_total = sum(s_monthly)
            if s_total != total_planned:
                s_monthly[-1] += total_planned - s_total

            for i, m in enumerate(months):
                monthly_planned[m] = s_monthly[i]

    # Build result
    result: list[MonthlyCashPoint] = []
    cumulative = ZERO
    for m in months:
        planned = monthly_planned.get(m, ZERO)
        cumulative += planned
        result.append(
            MonthlyCashPoint(
                month=m,
                planned_billings=planned,
                cumulative_billed=cumulative,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Actual cash flow from pay applications
# ---------------------------------------------------------------------------


def compute_actual_cash_flow(
    pay_apps: list[dict],
    change_orders: list[dict],
    payment_lag_owner_days: int = 30,
) -> list[MonthlyCashPoint]:
    """Build monthly actuals from pay application history.

    Each pay_app dict must have: period_to (date), current_payment_due (Decimal),
    total_completed_and_stored (Decimal), status, and optionally paid_at (date).

    Each change_order dict must have: approved_date (date), cost_impact (Decimal).

    Returns list[MonthlyCashPoint] with actual billing and receipt values.
    """
    if not pay_apps:
        return []

    # Collect monthly billings
    monthly_data: dict[date, dict[str, Decimal]] = {}

    for pa in pay_apps:
        period_to = pa.get("period_to")
        if isinstance(period_to, str):
            period_to = date.fromisoformat(period_to)
        if period_to is None:
            continue

        billing_month = _month_key(period_to)
        if billing_month not in monthly_data:
            monthly_data[billing_month] = {
                "actual_billings": ZERO,
                "actual_receipts": ZERO,
                "expected_receipts": ZERO,
            }

        payment_due = Decimal(str(pa.get("current_payment_due", 0)))
        monthly_data[billing_month]["actual_billings"] += payment_due

        # Determine receipt: use paid_at if available, else estimate from lag
        paid_at = pa.get("paid_at")
        if paid_at and pa.get("status") in ("certified", "paid"):
            if isinstance(paid_at, str):
                paid_at = date.fromisoformat(paid_at)
            receipt_month = _month_key(paid_at)
        else:
            expected_receipt_date = period_to + timedelta(days=payment_lag_owner_days)
            receipt_month = _month_key(expected_receipt_date)

        if receipt_month not in monthly_data:
            monthly_data[receipt_month] = {
                "actual_billings": ZERO,
                "actual_receipts": ZERO,
                "expected_receipts": ZERO,
            }

        if pa.get("status") in ("certified", "paid"):
            monthly_data[receipt_month]["actual_receipts"] += payment_due
        else:
            monthly_data[receipt_month]["expected_receipts"] += payment_due

    # Incorporate change orders into billing projections
    for co in change_orders:
        approved_date = co.get("approved_date")
        if isinstance(approved_date, str):
            approved_date = date.fromisoformat(approved_date)
        if approved_date is None:
            continue

        co_month = _month_key(approved_date)
        cost_impact = Decimal(str(co.get("cost_impact", 0)))
        if co_month not in monthly_data:
            monthly_data[co_month] = {
                "actual_billings": ZERO,
                "actual_receipts": ZERO,
                "expected_receipts": ZERO,
            }
        monthly_data[co_month]["actual_billings"] += cost_impact

    # Build sorted result
    if not monthly_data:
        return []

    sorted_months = sorted(monthly_data.keys())
    result: list[MonthlyCashPoint] = []
    cumulative_billed = ZERO
    cumulative_received = ZERO

    for m in sorted_months:
        data = monthly_data[m]
        actual_billings = data["actual_billings"]
        actual_receipts = data["actual_receipts"]
        expected_receipts = data["expected_receipts"]

        cumulative_billed += actual_billings
        cumulative_received += actual_receipts

        net_position = cumulative_received - cumulative_billed

        result.append(
            MonthlyCashPoint(
                month=m,
                actual_billings=_round2(actual_billings),
                actual_receipts=_round2(actual_receipts),
                expected_receipts=_round2(expected_receipts),
                net_cash_position=_round2(net_position),
                cumulative_billed=_round2(cumulative_billed),
                cumulative_received=_round2(cumulative_received),
            )
        )

    return result


# ---------------------------------------------------------------------------
# Cash flow forecast
# ---------------------------------------------------------------------------


def forecast_cash_flow(
    planned_curve: list[MonthlyCashPoint],
    actual_curve: list[MonthlyCashPoint],
    remaining_months: int,
    retainage_pct: Decimal,
    payment_lag_owner_days: int = 30,
    inflation_rate_annual: Decimal = ZERO,
) -> CashFlowForecast:
    """Merge planned and actual curves, project future months.

    Uses earned value trending (CPI-adjusted) when historical data is available.
    Detects risk indicators and calculates retainage held.

    SV-11: When ``inflation_rate_annual`` > 0, applies monthly compounding
    inflation to projected billings: billing *= (1 + rate/12) ^ months_from_now.
    This only affects future projected months, not historical actuals.
    """
    if not planned_curve:
        return CashFlowForecast(monthly_projections=[], risk_indicators=["No planned curve data"])

    # Build lookup maps
    planned_map: dict[date, MonthlyCashPoint] = {p.month: p for p in planned_curve}
    actual_map: dict[date, MonthlyCashPoint] = {a.month: a for a in actual_curve}

    # Compute CPI from actuals (if enough data)
    total_planned_to_date = ZERO
    total_actual_billed = ZERO
    for a in actual_curve:
        if a.month in planned_map:
            total_planned_to_date += planned_map[a.month].planned_billings
        total_actual_billed += a.actual_billings

    cpi = Decimal("1.0")
    if total_planned_to_date > ZERO and total_actual_billed > ZERO:
        cpi = _round2(total_actual_billed / total_planned_to_date)
        # Clamp CPI to reasonable range
        cpi = max(Decimal("0.5"), min(cpi, Decimal("2.0")))

    # Determine all months to report on
    all_months: set[date] = set()
    all_months.update(planned_map.keys())
    all_months.update(actual_map.keys())

    # Add future projection months
    if planned_curve:
        last_planned = max(p.month for p in planned_curve)
        current = _next_month_start(last_planned)
        for _ in range(remaining_months):
            all_months.add(current)
            current = _next_month_start(current)

    sorted_months = sorted(all_months)

    # Determine the dividing line: months with actuals vs projected
    actual_months = set(actual_map.keys())
    today_month = _month_key(date.today())

    # Build merged projections
    projections: list[MonthlyCashPoint] = []
    cumulative_billed = ZERO
    cumulative_received = ZERO
    total_contract_value = ZERO
    risk_indicators: list[str] = []
    retainage_held = ZERO
    retainage_rate = retainage_pct / HUNDRED

    # Calculate total contract value from planned curve
    for p in planned_curve:
        total_contract_value += p.planned_billings

    # SV-11 / M-23: Compute monthly inflation multiplier. Clamp annual rate
    # to 25% — hyperinflation projections compound grotesquely (50%/yr →
    # ~8% monthly) and are almost always user error. Real US construction
    # material inflation peaks around 20%/yr; anything above is flagged.
    _MAX_ANNUAL_INFLATION = Decimal("0.25")
    if inflation_rate_annual > _MAX_ANNUAL_INFLATION:
        logger.warning(
            "cash_flow_engine: clamping annual inflation %s → %s (probable user error)",
            inflation_rate_annual,
            _MAX_ANNUAL_INFLATION,
        )
        inflation_rate_annual = _MAX_ANNUAL_INFLATION
    if inflation_rate_annual < ZERO:
        raise ValueError(f"inflation_rate_annual must be non-negative, got {inflation_rate_annual}")
    _monthly_inflation_rate = ZERO
    if inflation_rate_annual > ZERO:
        _monthly_inflation_rate = inflation_rate_annual / Decimal("12")

    # SV-09: Two-pass approach for proper payment lag modeling.
    # Pass 1: Compute billings per month and schedule deferred receipts.
    month_billings: dict[date, Decimal] = {}
    month_actual_receipts: dict[date, Decimal] = {}
    month_expected_receipts: dict[date, Decimal] = {m: ZERO for m in sorted_months}
    month_is_actual: dict[date, bool] = {}
    month_planned_ref: dict[date, MonthlyCashPoint | None] = {}
    month_actual_ref: dict[date, MonthlyCashPoint | None] = {}

    for m in sorted_months:
        has_actual = m in actual_months and m <= today_month
        planned = planned_map.get(m)
        actual = actual_map.get(m)

        month_is_actual[m] = has_actual
        month_planned_ref[m] = planned
        month_actual_ref[m] = actual

        if has_actual and actual is not None:
            billing = actual.actual_billings
            month_actual_receipts[m] = actual.actual_receipts
        elif planned is not None:
            billing = _round2(planned.planned_billings * cpi)
            month_actual_receipts[m] = ZERO
        else:
            if planned_curve:
                avg_planned = total_contract_value / Decimal(str(len(planned_curve)))
                billing = _round2(avg_planned * cpi)
            else:
                billing = ZERO
            month_actual_receipts[m] = ZERO

        # SV-11: Apply monthly inflation to projected (non-actual) billings
        if not has_actual and _monthly_inflation_rate > ZERO:
            # Count months from the first projected month
            months_from_today = (m.year - today_month.year) * 12 + (m.month - today_month.month)
            if months_from_today > 0:
                inflation_factor = (Decimal("1") + _monthly_inflation_rate) ** months_from_today
                billing = _round2(billing * inflation_factor)

        month_billings[m] = billing

        # SV-09: For projected months, compute the receipt and place it
        # in the correct future month based on payment lag.
        if not has_actual and billing > ZERO:
            # expected_receipt_date = billing_month_end + payment_lag_owner_days
            month_end = date(m.year, m.month, _days_in_month(m))
            expected_receipt_date = month_end + timedelta(days=payment_lag_owner_days)
            receipt_month = _month_key(expected_receipt_date)

            receipt_after_retainage = _round2(billing * (Decimal("1") - retainage_rate))

            # Ensure the receipt month is in our set; if beyond projection, add it
            if receipt_month not in month_expected_receipts:
                # Receipt falls beyond projection horizon — attribute to last month
                receipt_month = sorted_months[-1]

            month_expected_receipts[receipt_month] = (
                month_expected_receipts.get(receipt_month, ZERO) + receipt_after_retainage
            )

    # Pass 2: Build final projections using the deferred receipt schedule.
    for m in sorted_months:
        has_actual = month_is_actual[m]
        planned = month_planned_ref[m]
        actual = month_actual_ref[m]
        billing = month_billings[m]

        cumulative_billed += billing

        if has_actual:
            receipts_this_month = month_actual_receipts[m]
        else:
            receipts_this_month = month_expected_receipts.get(m, ZERO)

        cumulative_received += receipts_this_month

        retainage_this_month = _round2(billing * retainage_rate)
        retainage_held += retainage_this_month

        net_position = cumulative_received - cumulative_billed

        projections.append(
            MonthlyCashPoint(
                month=m,
                planned_billings=planned.planned_billings if planned else ZERO,
                actual_billings=actual.actual_billings if actual else ZERO,
                expected_receipts=receipts_this_month if not has_actual else ZERO,
                actual_receipts=month_actual_receipts.get(m, ZERO) if has_actual else ZERO,
                net_cash_position=_round2(net_position),
                cumulative_billed=_round2(cumulative_billed),
                cumulative_received=_round2(cumulative_received),
            )
        )

        # Risk detection
        if net_position < ZERO:
            month_str = m.strftime("%B %Y")
            indicator = f"Negative cash position in {month_str}"
            if indicator not in risk_indicators:
                risk_indicators.append(indicator)

    # Additional risk indicators
    if retainage_held > ZERO and total_contract_value > ZERO:
        retainage_pct_actual = _round2((retainage_held / total_contract_value) * HUNDRED)
        if retainage_pct_actual > Decimal("15"):
            risk_indicators.append(f"Retainage exceeds {retainage_pct_actual}% of contract value")

    if cpi < Decimal("0.9"):
        risk_indicators.append(f"Billing trend below plan (CPI={cpi:.2f})")

    return CashFlowForecast(
        monthly_projections=projections,
        total_contract_value=_round2(total_contract_value),
        total_billed=_round2(cumulative_billed),
        total_received=_round2(cumulative_received),
        retainage_held=_round2(retainage_held),
        months_remaining=remaining_months,
        risk_indicators=risk_indicators,
    )


# ---------------------------------------------------------------------------
# Payment waterfall
# ---------------------------------------------------------------------------


def model_payment_waterfall(
    billing_amount: Decimal,
    billing_date: date,
    retainage_pct: Decimal,
    payment_lag_owner_days: int = 30,
    payment_lag_sub_days: int = 45,
) -> PaymentWaterfall:
    """Model the payment waterfall from GC billing to sub payment.

    Waterfall:
    1. GC submits pay app to Owner
    2. Owner reviews (5 business days)
    3. Owner pays GC (payment_lag_owner_days from submission)
    4. GC withholds retainage
    5. GC pays subs (payment_lag_sub_days from GC receipt)
    """
    if billing_amount < ZERO:
        raise ValueError("billing_amount must be non-negative")
    if retainage_pct < ZERO or retainage_pct > HUNDRED:
        raise ValueError("retainage_pct must be between 0 and 100")

    retainage_rate = retainage_pct / HUNDRED
    retainage_amount = _round2(billing_amount * retainage_rate)
    net_to_gc = _round2(billing_amount - retainage_amount)

    # Step dates
    review_date = billing_date + timedelta(days=5)
    owner_pay_date = billing_date + timedelta(days=payment_lag_owner_days)
    sub_pay_date = owner_pay_date + timedelta(days=payment_lag_sub_days)

    steps: list[WaterfallStep] = [
        WaterfallStep(
            from_party="General Contractor",
            to_party="Owner",
            amount=billing_amount,
            expected_date=billing_date,
            description="GC submits pay application",
        ),
        WaterfallStep(
            from_party="Owner",
            to_party="Architect",
            amount=billing_amount,
            expected_date=review_date,
            description="Owner forwards to Architect for review",
        ),
        WaterfallStep(
            from_party="Owner",
            to_party="General Contractor",
            amount=net_to_gc,
            expected_date=owner_pay_date,
            description=f"Owner pays GC (less {retainage_pct}% retainage)",
        ),
    ]

    if retainage_amount > ZERO:
        steps.append(
            WaterfallStep(
                from_party="Owner",
                to_party="Retainage Account",
                amount=retainage_amount,
                expected_date=owner_pay_date,
                description=f"Retainage withheld ({retainage_pct}%)",
            )
        )

    steps.append(
        WaterfallStep(
            from_party="General Contractor",
            to_party="Subcontractors",
            amount=net_to_gc,
            expected_date=sub_pay_date,
            description="GC pays subcontractors",
        )
    )

    return PaymentWaterfall(steps=steps)


# ---------------------------------------------------------------------------
# Monte Carlo simulation for cash flow
# ---------------------------------------------------------------------------


def run_cash_flow_monte_carlo(
    forecast: CashFlowForecast,
    num_simulations: int = 5000,
    payment_lag_std_days: int = 15,
    billing_variance_pct: int = 10,
    seed: int | None = 42,
) -> CashFlowConfidenceIntervals:
    """Simulate variability in payment timing, billing amounts, and retainage.

    Returns p10/p50/p90 cash position curves with confidence intervals.
    Uses numpy for vectorized performance.
    """
    num_simulations = max(1, min(num_simulations, 100_000))

    if not _HAS_NUMPY or np is None:
        raise RuntimeError(
            "numpy is required for Monte Carlo simulation. Install with: pip install numpy"
        )

    projections = forecast.monthly_projections
    if not projections:
        return CashFlowConfidenceIntervals(
            p10=[],
            p50=[],
            p90=[],
            worst_month_position=ZERO,
            months_negative=0,
        )

    n_months = len(projections)
    rng = np.random.default_rng(seed=seed)

    # Extract planned billings as float array for vectorized operations
    planned_billings = np.array(
        [float(p.planned_billings or p.actual_billings) for p in projections]
    )

    # Simulate billing variability: normal distribution around planned
    billing_std = planned_billings * (billing_variance_pct / 100.0)
    # Shape: (num_simulations, n_months)
    simulated_billings = rng.normal(
        loc=planned_billings, scale=np.maximum(billing_std, 0.01), size=(num_simulations, n_months)
    )
    # Billing cannot be negative
    simulated_billings = np.maximum(simulated_billings, 0.0)

    # Simulate payment lag variability: shifts receipt timing
    # For each simulation, generate a lag offset per month
    lag_offsets = rng.normal(
        loc=0.0, scale=float(payment_lag_std_days), size=(num_simulations, n_months)
    )

    # Compute retainage rate
    retainage_rate = (
        float(forecast.retainage_held / forecast.total_billed)
        if forecast.total_billed > ZERO
        else 0.10
    )

    # Compute net cash position for each simulation
    # Receipts = billings * (1 - retainage_rate), shifted by lag
    simulated_receipts = simulated_billings * (1.0 - retainage_rate)

    # Cash position: cumulative receipts - cumulative billings
    # Lag effect: delay receipts by random amount (simplified as fraction reduction)
    # A positive lag means payment is late -> reduce that month's receipt, add to next month
    cash_positions = np.zeros((num_simulations, n_months))

    for sim in range(num_simulations):
        cumulative_billed = 0.0
        cumulative_received = 0.0
        pending_receipts = 0.0

        for m in range(n_months):
            cumulative_billed += simulated_billings[sim, m]

            # Lag effect: some of this month's expected receipts get pushed
            this_month_receipt = simulated_receipts[sim, m] + pending_receipts
            lag_days = lag_offsets[sim, m]

            if lag_days > 15:
                # Significant delay: push 50% to next month
                delayed_fraction = min(0.5, lag_days / 60.0)
                pending_receipts = this_month_receipt * delayed_fraction
                this_month_receipt *= 1.0 - delayed_fraction
            else:
                pending_receipts = 0.0

            cumulative_received += this_month_receipt
            cash_positions[sim, m] = cumulative_received - cumulative_billed

    # Compute percentiles
    p10_arr = np.percentile(cash_positions, 10, axis=0)
    p50_arr = np.percentile(cash_positions, 50, axis=0)
    p90_arr = np.percentile(cash_positions, 90, axis=0)

    p10_list = [_round2(Decimal(str(v))) for v in p10_arr]
    p50_list = [_round2(Decimal(str(v))) for v in p50_arr]
    p90_list = [_round2(Decimal(str(v))) for v in p90_arr]

    # Worst month position across all simulations at p10
    worst_month = _round2(Decimal(str(float(np.min(p10_arr)))))

    # Count months where p50 is negative
    months_negative = int(np.sum(p50_arr < 0))

    return CashFlowConfidenceIntervals(
        p10=p10_list,
        p50=p50_list,
        p90=p90_list,
        worst_month_position=worst_month,
        months_negative=months_negative,
    )


# ---------------------------------------------------------------------------
# Lien waiver evaluation
# ---------------------------------------------------------------------------


def evaluate_lien_waiver_coverage(
    waivers: list[dict],
    pay_apps: list[dict],
) -> LienWaiverAnalysis:
    """Evaluate lien waiver coverage against pay applications.

    Each waiver dict must have: vendor_name, through_date, status, amount, signed_date.
    Each pay_app dict must have: period_to, current_payment_due, application_number,
    contractor_info (with vendor list if available).

    Returns coverage percentage, missing waivers, and upcoming deadlines.
    """
    if not pay_apps:
        return LienWaiverAnalysis(coverage_pct=HUNDRED)

    # Filter out void waivers
    active_waivers = [w for w in waivers if w.get("status") != "void"]

    # Build set of (vendor_name_lower, through_date_month) for active waivers
    waiver_coverage: set[tuple[str, date]] = set()
    for w in active_waivers:
        vendor = (w.get("vendor_name") or "").lower().strip()
        through = w.get("through_date")
        if isinstance(through, str):
            through = date.fromisoformat(through)
        if vendor and through:
            waiver_coverage.add((vendor, _month_key(through)))

    # Determine required waivers from pay apps
    required_waivers: list[dict] = []
    for pa in pay_apps:
        period_to = pa.get("period_to")
        if isinstance(period_to, str):
            period_to = date.fromisoformat(period_to)
        if period_to is None:
            continue

        pa_month = _month_key(period_to)
        payment_due = Decimal(str(pa.get("current_payment_due", 0)))

        # Each pay app requires a waiver from the GC at minimum
        required_waivers.append(
            {
                "application_number": pa.get("application_number"),
                "period_to": period_to,
                "amount": payment_due,
                "vendor_name": (pa.get("contractor_info", {}).get("name") or "general_contractor")
                .lower()
                .strip(),
                "month": pa_month,
            }
        )

    if not required_waivers:
        return LienWaiverAnalysis(coverage_pct=HUNDRED)

    # Check coverage
    covered = 0
    missing: list[dict] = []
    for req in required_waivers:
        key = (req["vendor_name"], req["month"])
        if key in waiver_coverage:
            covered += 1
        else:
            missing.append(
                {
                    "application_number": req["application_number"],
                    "vendor_name": req["vendor_name"],
                    "period_to": str(req["period_to"]),
                    "amount": str(req["amount"]),
                }
            )

    coverage_pct = _round2(Decimal(str(covered)) / Decimal(str(len(required_waivers))) * HUNDRED)

    # Identify upcoming deadlines: pending waivers > 14 days old
    today = date.today()
    upcoming_deadlines: list[dict] = []
    for w in active_waivers:
        if w.get("status") != "pending":
            continue

        through = w.get("through_date")
        if isinstance(through, str):
            through = date.fromisoformat(through)
        if through is None:
            continue

        days_pending = (today - through).days
        if days_pending > 14:
            upcoming_deadlines.append(
                {
                    "vendor_name": w.get("vendor_name", ""),
                    "through_date": str(through),
                    "days_overdue": days_pending,
                    "amount": str(w.get("amount", 0)),
                    "waiver_type": w.get("waiver_type", ""),
                }
            )

    # Sort deadlines by days overdue descending
    upcoming_deadlines.sort(key=lambda d: d.get("days_overdue", 0), reverse=True)

    return LienWaiverAnalysis(
        coverage_pct=coverage_pct,
        missing_waivers=missing,
        upcoming_deadlines=upcoming_deadlines,
    )
