"""Workforce analytics and labor forecasting.

Aggregates labor data from daily logs and productivity records, calculates
trade-level productivity metrics, forecasts future labor needs based on
remaining schedule activities, predicts overtime, and assesses fatigue risk.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class LaborAggregate:
    """Aggregated labor data for a project or portfolio."""

    project_id: str | None
    date_range: tuple[date, date] | None
    total_workers: int
    total_manhours: float
    workers_by_trade: dict[str, int]
    manhours_by_trade: dict[str, float]
    daily_avg_workers: float
    daily_avg_manhours: float
    working_days: int


@dataclass
class TradeProductivityMetric:
    """Productivity metrics for a single trade."""

    trade: str
    activity_type: str | None
    avg_manhours_per_unit: float
    median_manhours_per_unit: float
    std_dev: float
    sample_count: int
    trend: str  # improving / declining / stable
    trend_slope: float
    unit_of_measure: str


@dataclass
class LaborForecast:
    """Forecasted labor needs for remaining work."""

    project_id: str
    forecast_date: str
    remaining_activities: int
    total_remaining_manhours: float
    by_trade: dict[str, float]
    by_month: dict[str, dict[str, float]]
    estimated_completion_date: str | None


@dataclass
class OvertimePrediction:
    """Overtime prediction based on schedule and workforce."""

    project_id: str
    schedule_compression_pct: float
    total_remaining_manhours: float
    standard_hours_available: float
    predicted_overtime_hours: float
    overtime_pct: float
    estimated_overtime_cost: float
    overtime_rate_multiplier: float
    risk_level: str  # low / moderate / high / critical
    recommendation: str


@dataclass
class FatigueAlert:
    """Fatigue risk alert for a worker or trade."""

    worker_id: str | None
    trade: str | None
    alert_type: str  # daily_excess / weekly_excess
    hours_worked: float
    threshold: float
    excess_hours: float
    risk_level: str  # yellow / red
    recommendation: str


@dataclass
class CraftAvailability:
    """Supply-demand gap analysis by trade."""

    project_id: str
    forecast_date: str
    trades: dict[str, dict[str, Any]]
    total_demand_manhours: float
    total_supply_workers: int
    overall_gap_pct: float


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STANDARD_HOURS_PER_DAY = 8.0
STANDARD_DAYS_PER_WEEK = 5.0
STANDARD_HOURS_PER_WEEK = STANDARD_HOURS_PER_DAY * STANDARD_DAYS_PER_WEEK
OVERTIME_RATE_MULTIPLIER = 1.5

# Default average hourly labor rate by trade (for overtime cost estimation)
_TRADE_HOURLY_RATES: dict[str, float] = {
    "carpenter": 38.50,
    "electrician": 42.00,
    "plumber": 44.00,
    "ironworker": 45.00,
    "laborer": 28.00,
    "operator": 40.00,
    "mason": 36.00,
    "painter": 32.00,
    "roofer": 35.00,
    "sheet_metal": 41.00,
    "pipefitter": 46.00,
    "welder": 43.00,
    "concrete_finisher": 34.00,
    "glazier": 39.00,
    "insulator": 37.00,
    "default": 36.00,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def aggregate_labor_data(
    db: AsyncSession,
    project_id: str | None = None,
    org_id: str | None = None,
    date_range: tuple[date, date] | None = None,
) -> LaborAggregate:
    """Aggregate labor data from daily logs and productivity records.

    Supports single-project and portfolio (org-wide) aggregation.

    Parameters
    ----------
    db : AsyncSession
        Database session.
    project_id : str | None
        Single project UUID. If None, aggregates across org.
    org_id : str | None
        Organization UUID for portfolio-level aggregation.
    date_range : tuple[date, date] | None
        Optional (start_date, end_date) filter.
    """
    from app.models.productivity import DailyLog

    # Build query filters
    filters = []
    if project_id:
        import uuid

        pid = uuid.UUID(project_id) if isinstance(project_id, str) else project_id
        filters.append(DailyLog.project_id == pid)

    if org_id:
        # Filter by projects belonging to the organization
        from app.models.project import Project

        filters.append(DailyLog.project_id.in_(select(Project.id).where(Project.org_id == org_id)))

    if date_range:
        filters.append(DailyLog.log_date >= date_range[0])
        filters.append(DailyLog.log_date <= date_range[1])

    # Query daily logs
    stmt = select(DailyLog).where(*filters).order_by(DailyLog.log_date.asc())
    result = await db.execute(stmt)
    logs = list(result.scalars().all())

    if not logs:
        return LaborAggregate(
            project_id=project_id,
            date_range=date_range,
            total_workers=0,
            total_manhours=0.0,
            workers_by_trade={},
            manhours_by_trade={},
            daily_avg_workers=0.0,
            daily_avg_manhours=0.0,
            working_days=0,
        )

    total_workers = 0
    total_manhours = 0.0
    workers_by_trade: dict[str, int] = {}
    manhours_by_trade: dict[str, float] = {}
    dates_seen: set[date] = set()

    for log in logs:
        crew_count = log.crew_count or 0
        work_hours = float(log.work_hours or 0)

        total_workers += crew_count
        total_manhours += work_hours * crew_count
        dates_seen.add(log.log_date)

        # Parse manpower_by_trade JSONB
        for entry in log.manpower_by_trade or []:
            trade = entry.get("trade", "unknown")
            headcount = int(entry.get("headcount", 0))
            hours = float(entry.get("hours", 0))

            workers_by_trade[trade] = workers_by_trade.get(trade, 0) + headcount
            manhours_by_trade[trade] = manhours_by_trade.get(trade, 0.0) + hours

    working_days = len(dates_seen)
    daily_avg_workers = total_workers / working_days if working_days > 0 else 0.0
    daily_avg_manhours = total_manhours / working_days if working_days > 0 else 0.0

    # IG-12: Supplement labor data with ambient field data (AmbientDailySnapshot).
    # Ambient data may provide higher headcounts or more detailed trade breakdown
    # from badge readers, IoT sensors, or computer vision.
    data_sources = ["daily_logs"]
    try:
        from app.models.ambient_field import AmbientDailySnapshot

        ambient_filters = []
        if project_id:
            ambient_filters.append(AmbientDailySnapshot.project_id == pid)
        if date_range:
            ambient_filters.append(AmbientDailySnapshot.snapshot_date >= date_range[0])
            ambient_filters.append(AmbientDailySnapshot.snapshot_date <= date_range[1])

        if ambient_filters:
            ambient_stmt = select(AmbientDailySnapshot).where(*ambient_filters)
            ambient_result = await db.execute(ambient_stmt)
            ambient_snapshots = list(ambient_result.scalars().all())

            if ambient_snapshots:
                data_sources.append("ambient_field")
                for snap in ambient_snapshots:
                    ws = snap.workforce_summary or {}
                    ambient_headcount = int(ws.get("total_workers", 0))
                    ambient_trades = ws.get("workers_by_trade", {})

                    # If ambient provides higher headcount for this date, supplement
                    snap_date = snap.snapshot_date
                    if snap_date not in dates_seen:
                        # Ambient data for a date with no daily log -- add it
                        dates_seen.add(snap_date)
                        total_workers += ambient_headcount
                        total_manhours += ambient_headcount * STANDARD_HOURS_PER_DAY
                        for trade, count in ambient_trades.items():
                            workers_by_trade[trade] = workers_by_trade.get(trade, 0) + int(count)
                    else:
                        # Date already has daily log data -- supplement trade details
                        # only if ambient provides trades the daily log does not
                        for trade, count in ambient_trades.items():
                            if trade not in workers_by_trade:
                                workers_by_trade[trade] = int(count)

                # Recompute averages
                working_days = len(dates_seen)
                daily_avg_workers = total_workers / working_days if working_days > 0 else 0.0
                daily_avg_manhours = total_manhours / working_days if working_days > 0 else 0.0

                logger.info(
                    "Supplemented labor data with %d ambient snapshots for project %s",
                    len(ambient_snapshots),
                    project_id,
                )
    except Exception:
        logger.warning(
            "Failed to supplement labor data with ambient field data for project %s",
            project_id,
            exc_info=True,
        )

    result_aggregate = LaborAggregate(
        project_id=project_id,
        date_range=date_range,
        total_workers=total_workers,
        total_manhours=round(total_manhours, 2),
        workers_by_trade=workers_by_trade,
        manhours_by_trade={k: round(v, 2) for k, v in manhours_by_trade.items()},
        daily_avg_workers=round(daily_avg_workers, 1),
        daily_avg_manhours=round(daily_avg_manhours, 1),
        working_days=working_days,
    )

    # Attach data_sources as an extra attribute (does not break existing callers)
    result_aggregate.data_sources = data_sources  # type: ignore[attr-defined]

    return result_aggregate


def calculate_productivity_metrics(
    crew_records: list[dict],
) -> list[TradeProductivityMetric]:
    """Calculate productivity metrics by trade from crew productivity records.

    Parameters
    ----------
    crew_records : list[dict]
        Each dict: trade, actual_units, planned_units, crew_size, work_date,
        unit_of_measure, and optionally activity_type.

    Returns
    -------
    List of TradeProductivityMetric, one per trade-activity combination.
    """
    # Group by trade (and optionally activity_type)
    grouped: dict[tuple[str, str | None], list[dict]] = {}
    for rec in crew_records:
        trade = rec.get("trade", "unknown")
        activity_type = rec.get("activity_type")
        key = (trade, activity_type)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(rec)

    results: list[TradeProductivityMetric] = []

    for (trade, activity_type), records in grouped.items():
        # Calculate manhours per unit for each record
        mh_per_unit_values: list[float] = []
        for rec in records:
            actual_units = float(rec.get("actual_units", 0))
            crew_size = int(rec.get("crew_size", 1))
            work_hours = float(rec.get("work_hours", STANDARD_HOURS_PER_DAY))

            if actual_units > 0:
                total_manhours = crew_size * work_hours
                mh_per_unit = total_manhours / actual_units
                mh_per_unit_values.append(mh_per_unit)

        if not mh_per_unit_values:
            continue

        avg_mh = statistics.mean(mh_per_unit_values)
        median_mh = statistics.median(mh_per_unit_values)
        std_dev = statistics.stdev(mh_per_unit_values) if len(mh_per_unit_values) > 1 else 0.0

        # Calculate trend using simple linear regression
        trend = "stable"
        trend_slope = 0.0
        if len(mh_per_unit_values) >= 3:
            n = len(mh_per_unit_values)
            x_vals = list(range(n))
            x_mean = statistics.mean(x_vals)
            y_mean = avg_mh

            numerator = sum(
                (x - x_mean) * (y - y_mean)
                for x, y in zip(x_vals, mh_per_unit_values, strict=False)
            )
            denominator = sum((x - x_mean) ** 2 for x in x_vals)

            if denominator > 0:
                trend_slope = numerator / denominator
                # Negative slope = fewer manhours per unit = improving productivity
                relative_slope = trend_slope / avg_mh if avg_mh > 0 else 0
                if relative_slope < -0.02:
                    trend = "improving"
                elif relative_slope > 0.02:
                    trend = "declining"

        unit_of_measure = records[0].get("unit_of_measure", "")

        results.append(
            TradeProductivityMetric(
                trade=trade,
                activity_type=activity_type,
                avg_manhours_per_unit=round(avg_mh, 4),
                median_manhours_per_unit=round(median_mh, 4),
                std_dev=round(std_dev, 4),
                sample_count=len(mh_per_unit_values),
                trend=trend,
                trend_slope=round(trend_slope, 6),
                unit_of_measure=unit_of_measure,
            )
        )

    return results


async def forecast_labor_needs(
    db: AsyncSession,
    project_id: str,
) -> LaborForecast:
    """Forecast remaining labor needs based on incomplete schedule activities.

    For each remaining activity, estimates manhours from:
      1. activity.resource_assignments (if specified)
      2. CostItem.manhours_per_unit (if matching CSI code)
      3. ProductivityRate baseline data
      4. Fallback: duration_days * 8h * estimated_crew_size

    Groups by trade and month for planning.
    """
    import uuid

    from app.models.estimating import CostItem
    from app.models.scheduling import ScheduleActivity

    pid = uuid.UUID(project_id) if isinstance(project_id, str) else project_id

    # Fetch incomplete activities
    stmt = (
        select(ScheduleActivity)
        .where(
            ScheduleActivity.project_id == pid,
            ScheduleActivity.status.in_(["not_started", "in_progress"]),
        )
        .order_by(ScheduleActivity.start_date.asc())
    )
    result = await db.execute(stmt)
    activities = list(result.scalars().all())

    if not activities:
        return LaborForecast(
            project_id=project_id,
            forecast_date=date.today().isoformat(),
            remaining_activities=0,
            total_remaining_manhours=0.0,
            by_trade={},
            by_month={},
            estimated_completion_date=None,
        )

    # SV-28: Pre-fetch CostItem manhours_per_unit for all activity CSI codes.
    # This avoids N+1 queries in the activity loop.
    csi_codes = set()
    for act in activities:
        code = getattr(act, "activity_code", None) or getattr(act, "wbs_code", None)
        if code:
            csi_codes.add(code)

    costitem_mhpu: dict[str, float] = {}
    if csi_codes:
        try:
            ci_stmt = select(CostItem.csi_code, CostItem.manhours_per_unit).where(
                CostItem.csi_code.in_(list(csi_codes)),
                CostItem.manhours_per_unit.isnot(None),
            )
            ci_result = await db.execute(ci_stmt)
            for row in ci_result.all():
                if row.csi_code and row.manhours_per_unit:
                    costitem_mhpu[row.csi_code] = float(row.manhours_per_unit)
            if costitem_mhpu:
                logger.info(
                    "SV-28: Found CostItem manhours_per_unit for %d/%d CSI codes",
                    len(costitem_mhpu),
                    len(csi_codes),
                )
        except Exception:
            logger.warning(
                "SV-28: CostItem manhours_per_unit lookup failed",
                exc_info=True,
            )

    # Inject CostItem data into activity metadata for _estimate_activity_manhours
    for act in activities:
        code = getattr(act, "activity_code", None) or getattr(act, "wbs_code", None)
        if code and code in costitem_mhpu:
            meta = dict(act.metadata_ or {})
            meta["_costitem_manhours_per_unit"] = costitem_mhpu[code]
            act.metadata_ = meta

    by_trade: dict[str, float] = {}
    by_month: dict[str, dict[str, float]] = {}
    total_remaining_mh = 0.0
    latest_finish: date | None = None

    for activity in activities:
        manhours = _estimate_activity_manhours(activity)
        trade = _infer_trade_from_activity(activity)

        # Remaining work (adjust for percent complete)
        pct = float(activity.pct_complete or 0) / 100.0
        remaining_mh = manhours * (1.0 - pct)

        by_trade[trade] = by_trade.get(trade, 0.0) + remaining_mh
        total_remaining_mh += remaining_mh

        # Group by month
        activity_start = activity.start_date or date.today()
        month_key = activity_start.strftime("%Y-%m")
        if month_key not in by_month:
            by_month[month_key] = {}
        by_month[month_key][trade] = by_month[month_key].get(trade, 0.0) + remaining_mh

        # Track latest finish date
        finish = activity.finish_date or (activity_start + timedelta(days=activity.duration_days))
        if latest_finish is None or finish > latest_finish:
            latest_finish = finish

    return LaborForecast(
        project_id=project_id,
        forecast_date=date.today().isoformat(),
        remaining_activities=len(activities),
        total_remaining_manhours=round(total_remaining_mh, 2),
        by_trade={k: round(v, 2) for k, v in sorted(by_trade.items())},
        by_month={k: {t: round(h, 2) for t, h in v.items()} for k, v in sorted(by_month.items())},
        estimated_completion_date=latest_finish.isoformat() if latest_finish else None,
    )


def predict_overtime(
    remaining_activities: list[dict],
    available_workforce: dict[str, int],
    schedule_compression_pct: float = 0.0,
    avg_hourly_rate: float | None = None,
) -> OvertimePrediction:
    """Predict overtime needs based on remaining work and available workforce.

    Parameters
    ----------
    remaining_activities : list[dict]
        Each dict: trade, remaining_manhours, duration_days.
    available_workforce : dict[str, int]
        Workers available by trade.
    schedule_compression_pct : float
        Percentage to compress schedule (0-50). Shortens durations proportionally.
    avg_hourly_rate : float | None
        Average hourly rate for cost estimation. Auto-calculated from trades if None.

    Returns
    -------
    OvertimePrediction with predicted overtime hours and cost.
    """
    if not remaining_activities:
        return OvertimePrediction(
            project_id="",
            schedule_compression_pct=schedule_compression_pct,
            total_remaining_manhours=0.0,
            standard_hours_available=0.0,
            predicted_overtime_hours=0.0,
            overtime_pct=0.0,
            estimated_overtime_cost=0.0,
            overtime_rate_multiplier=OVERTIME_RATE_MULTIPLIER,
            risk_level="low",
            recommendation="No remaining activities.",
        )

    compression_factor = 1.0 - (min(schedule_compression_pct, 50.0) / 100.0)

    total_remaining_mh = 0.0
    total_standard_hours = 0.0

    for activity in remaining_activities:
        remaining_mh = float(activity.get("remaining_manhours", 0))
        duration_days = float(activity.get("duration_days", 1))
        trade = activity.get("trade", "default")
        workers = available_workforce.get(trade, 1)

        total_remaining_mh += remaining_mh

        # Standard hours = workers * standard_hours_per_day * compressed_duration
        compressed_duration = duration_days * compression_factor
        standard_hours = workers * STANDARD_HOURS_PER_DAY * compressed_duration
        total_standard_hours += standard_hours

    # Overtime = remaining manhours - available standard hours
    overtime_hours = max(0.0, total_remaining_mh - total_standard_hours)
    overtime_pct = (overtime_hours / total_remaining_mh * 100) if total_remaining_mh > 0 else 0.0

    # Calculate overtime cost
    if avg_hourly_rate is None:
        # Weighted average from trade rates
        total_workers = sum(available_workforce.values()) or 1
        weighted_rate = (
            sum(
                _TRADE_HOURLY_RATES.get(trade.lower(), _TRADE_HOURLY_RATES["default"]) * count
                for trade, count in available_workforce.items()
            )
            / total_workers
        )
        avg_hourly_rate = weighted_rate

    overtime_cost = overtime_hours * avg_hourly_rate * OVERTIME_RATE_MULTIPLIER

    # Risk level
    if overtime_pct >= 30:
        risk_level = "critical"
        recommendation = (
            f"Predicted {overtime_pct:.0f}% overtime. Consider adding crews, "
            "reducing scope, or extending schedule."
        )
    elif overtime_pct >= 20:
        risk_level = "high"
        recommendation = (
            f"Predicted {overtime_pct:.0f}% overtime. Plan for extended shifts "
            "and monitor fatigue risk."
        )
    elif overtime_pct >= 10:
        risk_level = "moderate"
        recommendation = (
            f"Predicted {overtime_pct:.0f}% overtime. Schedule may require "
            "occasional extended days."
        )
    else:
        risk_level = "low"
        recommendation = "Schedule is achievable within standard working hours."

    return OvertimePrediction(
        project_id="",
        schedule_compression_pct=schedule_compression_pct,
        total_remaining_manhours=round(total_remaining_mh, 2),
        standard_hours_available=round(total_standard_hours, 2),
        predicted_overtime_hours=round(overtime_hours, 2),
        overtime_pct=round(overtime_pct, 2),
        estimated_overtime_cost=round(overtime_cost, 2),
        overtime_rate_multiplier=OVERTIME_RATE_MULTIPLIER,
        risk_level=risk_level,
        recommendation=recommendation,
    )


def assess_fatigue_risk(
    worker_hours: list[dict],
    threshold_daily: float = 10.0,
    threshold_weekly: float = 50.0,
) -> list[FatigueAlert]:
    """Assess fatigue risk for workers based on hours worked.

    Parameters
    ----------
    worker_hours : list[dict]
        Each dict: worker_id (optional), trade, date, hours.
    threshold_daily : float
        Daily hours threshold triggering a fatigue alert (default 10h).
    threshold_weekly : float
        Weekly hours threshold triggering a fatigue alert (default 50h).

    Returns
    -------
    List of FatigueAlert for workers exceeding thresholds.
    """
    alerts: list[FatigueAlert] = []

    # Group by worker_id or trade
    by_worker: dict[str, list[dict]] = {}
    for entry in worker_hours:
        worker_key = entry.get("worker_id") or entry.get("trade", "unknown")
        if worker_key not in by_worker:
            by_worker[worker_key] = []
        by_worker[worker_key].append(entry)

    for worker_key, records in by_worker.items():
        worker_id = None
        trade = None

        # Determine if this is a worker_id or trade
        if records and records[0].get("worker_id"):
            worker_id = worker_key
            trade = records[0].get("trade")
        else:
            trade = worker_key

        # Check daily hours
        for rec in records:
            hours = float(rec.get("hours", 0))
            if hours > threshold_daily:
                excess = hours - threshold_daily
                risk_level = "red" if hours > threshold_daily + 2 else "yellow"
                alerts.append(
                    FatigueAlert(
                        worker_id=worker_id,
                        trade=trade,
                        alert_type="daily_excess",
                        hours_worked=round(hours, 2),
                        threshold=threshold_daily,
                        excess_hours=round(excess, 2),
                        risk_level=risk_level,
                        recommendation=_fatigue_recommendation(risk_level, "daily", hours),
                    )
                )

        # Check weekly hours (group by ISO week)
        weekly: dict[str, float] = {}
        for rec in records:
            rec_date = rec.get("date")
            if isinstance(rec_date, str):
                rec_date = date.fromisoformat(rec_date)
            if isinstance(rec_date, date):
                week_key = f"{rec_date.isocalendar()[0]}-W{rec_date.isocalendar()[1]:02d}"
            else:
                week_key = "unknown"
            weekly[week_key] = weekly.get(week_key, 0.0) + float(rec.get("hours", 0))

        for week_key, week_hours in weekly.items():
            if week_hours > threshold_weekly:
                excess = week_hours - threshold_weekly
                risk_level = "red" if week_hours > threshold_weekly + 10 else "yellow"
                alerts.append(
                    FatigueAlert(
                        worker_id=worker_id,
                        trade=trade,
                        alert_type="weekly_excess",
                        hours_worked=round(week_hours, 2),
                        threshold=threshold_weekly,
                        excess_hours=round(excess, 2),
                        risk_level=risk_level,
                        recommendation=_fatigue_recommendation(risk_level, "weekly", week_hours),
                    )
                )

    return alerts


async def get_craft_availability(
    db: AsyncSession,
    project_id: str,
    trades: list[str] | None = None,
    horizon_weeks: int = 4,
) -> CraftAvailability:
    """Analyze supply-demand gap by trade.

    Demand = manhours from labor forecast.
    Supply = workers from latest workforce snapshot.
    Gap = demand that cannot be met with current workforce.

    SV-29: The ``horizon_weeks`` parameter (default 4) controls the lookahead
    window for gap calculation, replacing the previous hardcoded 4-week value.
    """
    import uuid

    from app.models.workforce import WorkforceSnapshot

    pid = uuid.UUID(project_id) if isinstance(project_id, str) else project_id

    # Get demand from labor forecast
    forecast = await forecast_labor_needs(db, project_id)

    # Get supply from latest snapshot
    stmt = (
        select(WorkforceSnapshot)
        .where(WorkforceSnapshot.project_id == pid)
        .order_by(WorkforceSnapshot.snapshot_date.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    snapshot = result.scalars().first()

    supply_by_trade: dict[str, int] = {}
    if snapshot:
        supply_by_trade = dict(snapshot.workers_by_trade or {})

    # Build trade-level analysis
    all_trades = set(forecast.by_trade.keys()) | set(supply_by_trade.keys())
    if trades:
        all_trades = all_trades & set(trades)

    trade_analysis: dict[str, dict[str, Any]] = {}
    total_demand = 0.0
    total_supply = 0

    for trade in sorted(all_trades):
        demand_mh = forecast.by_trade.get(trade, 0.0)
        supply_workers = supply_by_trade.get(trade, 0)

        # Estimate weekly capacity: workers * standard_hours_per_week
        weekly_capacity = supply_workers * STANDARD_HOURS_PER_WEEK
        # Estimate weeks of demand
        weeks_of_demand = demand_mh / weekly_capacity if weekly_capacity > 0 else float("inf")

        gap_mh = max(0.0, demand_mh - weekly_capacity * horizon_weeks)
        gap_workers = (
            int(gap_mh / (STANDARD_HOURS_PER_WEEK * horizon_weeks) + 0.5) if gap_mh > 0 else 0
        )

        trade_analysis[trade] = {
            "demand_manhours": round(demand_mh, 2),
            "supply_workers": supply_workers,
            "weekly_capacity_hours": round(weekly_capacity, 2),
            "weeks_of_demand": round(weeks_of_demand, 1)
            if weeks_of_demand != float("inf")
            else None,
            "gap_manhours": round(gap_mh, 2),
            "gap_workers_needed": gap_workers,
            "status": (
                "adequate"
                if gap_workers == 0
                else "shortage"
                if gap_workers <= 3
                else "critical_shortage"
            ),
        }

        total_demand += demand_mh
        total_supply += supply_workers

    # Overall gap percentage
    total_capacity = total_supply * STANDARD_HOURS_PER_WEEK * horizon_weeks
    overall_gap_pct = (
        ((total_demand - total_capacity) / total_demand * 100)
        if total_demand > 0 and total_demand > total_capacity
        else 0.0
    )

    return CraftAvailability(
        project_id=project_id,
        forecast_date=date.today().isoformat(),
        trades=trade_analysis,
        total_demand_manhours=round(total_demand, 2),
        total_supply_workers=total_supply,
        overall_gap_pct=round(max(0.0, overall_gap_pct), 2),
    )


async def create_workforce_snapshot(
    db: AsyncSession,
    project_id: str,
) -> dict:
    """Create a point-in-time workforce snapshot from today's data.

    Aggregates today's daily log data into a WorkforceSnapshot record.
    """
    import uuid

    from app.models.productivity import DailyLog
    from app.models.workforce import WorkforceSnapshot

    pid = uuid.UUID(project_id) if isinstance(project_id, str) else project_id
    today = date.today()

    # Fetch today's daily logs for the project
    stmt = select(DailyLog).where(
        DailyLog.project_id == pid,
        DailyLog.log_date == today,
    )
    result = await db.execute(stmt)
    logs = list(result.scalars().all())

    total_workers = 0
    workers_by_trade: dict[str, int] = {}
    total_manhours = 0.0
    overtime_hours = 0.0

    for log in logs:
        crew_count = log.crew_count or 0
        work_hours = float(log.work_hours or 0)
        total_workers += crew_count

        day_manhours = crew_count * work_hours
        total_manhours += day_manhours

        # Calculate overtime (hours beyond 8h standard per worker)
        if work_hours > STANDARD_HOURS_PER_DAY:
            overtime_hours += crew_count * (work_hours - STANDARD_HOURS_PER_DAY)

        for entry in log.manpower_by_trade or []:
            trade = entry.get("trade", "unknown")
            headcount = int(entry.get("headcount", 0))
            workers_by_trade[trade] = workers_by_trade.get(trade, 0) + headcount

    overtime_pct = (overtime_hours / total_manhours * 100) if total_manhours > 0 else 0.0

    # Assess fatigue from recent data (last 7 days)
    week_start = today - timedelta(days=6)
    recent_stmt = select(DailyLog).where(
        DailyLog.project_id == pid,
        DailyLog.log_date >= week_start,
        DailyLog.log_date <= today,
    )
    recent_result = await db.execute(recent_stmt)
    recent_logs = list(recent_result.scalars().all())

    # Build worker_hours from recent logs for fatigue assessment
    worker_hours_data: list[dict] = []
    for log in recent_logs:
        for entry in log.manpower_by_trade or []:
            trade = entry.get("trade", "unknown")
            hours = float(entry.get("hours", 0))
            if hours > 0:
                worker_hours_data.append(
                    {
                        "trade": trade,
                        "date": log.log_date.isoformat(),
                        "hours": hours,
                    }
                )

    fatigue_alerts = assess_fatigue_risk(worker_hours_data)
    fatigue_flags = [
        {
            "trade": a.trade,
            "alert_type": a.alert_type,
            "hours_worked": a.hours_worked,
            "risk_level": a.risk_level,
        }
        for a in fatigue_alerts
    ]

    # Create snapshot
    snapshot = WorkforceSnapshot(
        project_id=pid,
        snapshot_date=today,
        total_workers=total_workers,
        workers_by_trade=workers_by_trade,
        total_manhours=Decimal(str(round(total_manhours, 2))),
        overtime_hours=Decimal(str(round(overtime_hours, 2))),
        overtime_pct=Decimal(str(round(overtime_pct, 2))),
        fatigue_flags=fatigue_flags,
    )
    db.add(snapshot)
    await db.flush()
    await db.refresh(snapshot)

    logger.info(
        "Workforce snapshot created for project %s: %d workers, %.1f manhours, %.1f%% OT",
        project_id,
        total_workers,
        total_manhours,
        overtime_pct,
    )

    return {
        "id": str(snapshot.id),
        "project_id": str(snapshot.project_id),
        "snapshot_date": snapshot.snapshot_date.isoformat(),
        "total_workers": snapshot.total_workers,
        "workers_by_trade": snapshot.workers_by_trade,
        "total_manhours": float(snapshot.total_manhours),
        "overtime_hours": float(snapshot.overtime_hours),
        "overtime_pct": float(snapshot.overtime_pct),
        "fatigue_flags": snapshot.fatigue_flags,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


# ---------------------------------------------------------------------------
# SV-30: Worker-level tracking from BadgeEvent data
# ---------------------------------------------------------------------------


@dataclass
class WorkerHoursDetail:
    """Per-worker hours summary from badge event data."""

    worker_id: str
    worker_name: str | None
    trade: str | None
    total_hours: float
    days_worked: int
    daily_hours: dict[str, float]  # date_iso -> hours


async def get_worker_hours_detail(
    db: AsyncSession,
    project_id: str,
    date_range: tuple[date, date],
) -> list[WorkerHoursDetail]:
    """Return per-worker hours from BadgeEvent data, grouped by worker_id and trade.

    Computes hours from check_in/check_out pairs per day. This enables
    individual worker fatigue tracking and trade-level utilization analysis.

    Parameters
    ----------
    db : AsyncSession
        Database session.
    project_id : str
        Project UUID string.
    date_range : tuple[date, date]
        (start_date, end_date) inclusive filter.

    Returns
    -------
    List of WorkerHoursDetail, one per worker.
    """
    import uuid

    from app.models.ambient_field import BadgeEvent

    pid = uuid.UUID(project_id) if isinstance(project_id, str) else project_id

    stmt = (
        select(BadgeEvent)
        .where(
            BadgeEvent.project_id == pid,
            func.date(BadgeEvent.timestamp) >= date_range[0],
            func.date(BadgeEvent.timestamp) <= date_range[1],
        )
        .order_by(BadgeEvent.worker_id, BadgeEvent.timestamp)
    )
    result = await db.execute(stmt)
    events = list(result.scalars().all())

    if not events:
        return []

    # Group events by worker_id
    worker_events: dict[str, list] = {}
    worker_meta: dict[str, dict] = {}  # worker_id -> {name, trade}
    for evt in events:
        wid = evt.worker_id
        if wid not in worker_events:
            worker_events[wid] = []
            worker_meta[wid] = {
                "name": evt.worker_name,
                "trade": evt.trade,
            }
        worker_events[wid].append(evt)

    results: list[WorkerHoursDetail] = []
    for wid, evts in worker_events.items():
        # Pair check_in/check_out events per day to compute hours
        daily_hours: dict[str, float] = {}

        # Group by date
        by_date: dict[str, list] = {}
        for evt in evts:
            day_key = evt.timestamp.date().isoformat()
            if day_key not in by_date:
                by_date[day_key] = []
            by_date[day_key].append(evt)

        for day_key, day_evts in by_date.items():
            check_ins = [e for e in day_evts if e.event_type == "check_in"]
            check_outs = [e for e in day_evts if e.event_type == "check_out"]

            day_total = 0.0
            if check_ins and check_outs:
                # Pair first check_in with last check_out
                first_in = min(e.timestamp for e in check_ins)
                last_out = max(e.timestamp for e in check_outs)
                if last_out > first_in:
                    day_total = (last_out - first_in).total_seconds() / 3600.0

                    # Subtract break time if break events exist
                    break_starts = [e for e in day_evts if e.event_type == "break_start"]
                    break_ends = [e for e in day_evts if e.event_type == "break_end"]
                    if break_starts and break_ends:
                        for bs, be in zip(
                            sorted(break_starts, key=lambda e: e.timestamp),
                            sorted(break_ends, key=lambda e: e.timestamp),
                            strict=False,
                        ):
                            if be.timestamp > bs.timestamp:
                                day_total -= (be.timestamp - bs.timestamp).total_seconds() / 3600.0
            elif check_ins and not check_outs:
                # No check-out recorded: assume standard day
                day_total = STANDARD_HOURS_PER_DAY

            daily_hours[day_key] = round(max(0.0, day_total), 2)

        total_hours = sum(daily_hours.values())
        meta = worker_meta[wid]

        results.append(
            WorkerHoursDetail(
                worker_id=wid,
                worker_name=meta.get("name"),
                trade=meta.get("trade"),
                total_hours=round(total_hours, 2),
                days_worked=len([h for h in daily_hours.values() if h > 0]),
                daily_hours=daily_hours,
            )
        )

    # Sort by total hours descending
    results.sort(key=lambda r: r.total_hours, reverse=True)

    logger.info(
        "Worker hours detail: %d workers, %.1f total hours for project %s (%s to %s)",
        len(results),
        sum(r.total_hours for r in results),
        project_id,
        date_range[0],
        date_range[1],
    )
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _estimate_activity_manhours(activity, db: AsyncSession | None = None) -> float:
    """Estimate manhours for a schedule activity.

    Uses resource_assignments if present, then checks CostItem for
    manhours_per_unit (SV-28), otherwise falls back to
    duration * crew_size * standard_hours.
    """
    # Check resource_assignments JSONB
    assignments = activity.resource_assignments or []
    if assignments:
        total_mh = 0.0
        for assign in assignments:
            units = float(assign.get("units", 1))
            hours = float(assign.get("hours", STANDARD_HOURS_PER_DAY))
            duration = float(assign.get("duration_days", activity.duration_days or 1))
            total_mh += units * hours * duration
        if total_mh > 0:
            return total_mh

    # SV-28: Check CostItem for manhours_per_unit via the activity's CSI code.
    # This is a synchronous helper, so we store the CostItem lookup result
    # in the activity metadata cache to avoid repeated lookups.
    meta = activity.metadata_ or {}
    csi_code = getattr(activity, "activity_code", None) or getattr(activity, "wbs_code", None) or ""

    if csi_code:
        cached_mhpu = meta.get("_costitem_manhours_per_unit")
        if cached_mhpu is not None:
            # Use cached value from a prior async lookup
            duration = activity.duration_days or 1
            # manhours_per_unit * estimated daily output * duration
            return float(cached_mhpu) * duration

    # Fallback: estimate from duration and typical crew size
    duration = activity.duration_days or 1
    # Estimate crew size from activity metadata or default
    crew_size = float(meta.get("crew_size", 4))

    return duration * crew_size * STANDARD_HOURS_PER_DAY


def _infer_trade_from_activity(activity) -> str:
    """Infer the primary trade from an activity name and metadata."""
    name = (activity.name or "").lower()
    meta = activity.metadata_ or {}

    # Check metadata first
    if meta.get("trade"):
        return meta["trade"]

    # Check resource_assignments for trade info
    assignments = activity.resource_assignments or []
    for assign in assignments:
        if assign.get("trade"):
            return assign["trade"]

    # Infer from activity name keywords
    trade_keywords: dict[str, list[str]] = {
        "electrician": ["electrical", "wiring", "conduit", "panel", "switchgear", "lighting"],
        "plumber": ["plumbing", "piping", "sanitary", "water line", "fixture"],
        "carpenter": ["framing", "carpentry", "formwork", "blocking", "trim", "cabinet"],
        "ironworker": ["steel erection", "rebar", "structural steel", "iron", "reinforcing"],
        "laborer": ["excavat", "backfill", "grade", "cleanup", "demolition"],
        "mason": ["masonry", "brick", "block", "cmu", "mortar"],
        "operator": ["crane", "excavator", "loader", "dozer", "grading"],
        "concrete_finisher": ["concrete", "pour", "slab", "flatwork", "finish"],
        "painter": ["paint", "coating", "stain", "finish"],
        "roofer": ["roofing", "membrane", "flashing", "shingle"],
        "sheet_metal": ["ductwork", "hvac", "sheet metal", "mechanical"],
        "pipefitter": ["pipe", "steam", "process piping"],
        "glazier": ["glazing", "curtain wall", "window", "storefront"],
        "insulator": ["insulation", "fireproofing"],
    }

    for trade, keywords in trade_keywords.items():
        for keyword in keywords:
            if keyword in name:
                return trade

    return "general"


def _fatigue_recommendation(risk_level: str, period: str, hours: float) -> str:
    """Generate a fatigue mitigation recommendation."""
    if risk_level == "red":
        if period == "daily":
            return (
                f"Worker logged {hours:.1f}h in a single day. "
                "Mandatory rest period required. Review work schedule immediately."
            )
        return (
            f"Worker logged {hours:.1f}h this week. "
            "High fatigue risk — schedule mandatory day off and review workload."
        )
    else:
        if period == "daily":
            return (
                f"Worker approaching fatigue threshold ({hours:.1f}h today). "
                "Monitor for signs of fatigue and ensure adequate breaks."
            )
        return (
            f"Worker at {hours:.1f}h this week. "
            "Consider reducing hours next week and monitoring performance."
        )
