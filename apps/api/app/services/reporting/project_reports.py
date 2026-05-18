"""Unified project report generator.

Provides four high-impact reports:
  1. Monthly Cost Report — budget vs actuals by CSI division
  2. Schedule Performance Report — SPI trend, critical path, lookahead
  3. Safety Trend Report — incident/alert rates, TRIR, DART
  4. Subcontractor Performance Report — schedule, quality, RFI scorecards
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cash_flow import CashFlowSnapshot
from app.models.communication import RFI
from app.models.estimating import CostEstimate, EstimateLineItem
from app.models.evm import ChangeOrder, EVMSnapshot
from app.models.pay_application import (
    PayApplication,
    PayApplicationLineItem,
)
from app.models.project import Project
from app.models.quality import DefectReport
from app.models.safety_incident import SafetyAlert
from app.models.scheduling import ScheduleActivity
from app.models.subcontractor import SubcontractorProfile, SubcontractorSubmission

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TWO_PLACES = Decimal("0.01")
_FOUR_PLACES = Decimal("0.0001")


def _dec(val: Decimal | float | int | None) -> float:
    """Convert Decimal/None to float for JSON serialization."""
    if val is None:
        return 0.0
    return float(Decimal(str(val)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _month_range(year: int, month: int) -> tuple[date, date]:
    """Return (first_day, last_day) for a given month."""
    first = date(year, month, 1)
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return first, last


# ---------------------------------------------------------------------------
# 1. Monthly Cost Report
# ---------------------------------------------------------------------------


async def generate_monthly_cost_report(
    db: AsyncSession,
    project_id: uuid.UUID,
    month: int,
    year: int,
) -> dict:
    """Monthly Cost Report: budget vs actuals by CSI division, CO impacts,
    cost projections, cash flow summary.

    Returns a structured dict suitable for JSON serialization and PDF rendering.
    """
    first_day, last_day = _month_range(year, month)

    # 1) Budget: latest approved CostEstimate with line items by CSI
    budget_by_division: dict[str, float] = defaultdict(float)
    total_budget = 0.0

    est_stmt = (
        select(CostEstimate)
        .where(
            CostEstimate.project_id == project_id,
            CostEstimate.status.in_(["approved", "final", "active"]),
        )
        .order_by(CostEstimate.created_at.desc())
        .limit(1)
    )
    est_result = await db.execute(est_stmt)
    estimate = est_result.scalar_one_or_none()

    if estimate:
        total_budget = _dec(estimate.total_cost)
        li_stmt = select(EstimateLineItem).where(EstimateLineItem.estimate_id == estimate.id)
        li_result = await db.execute(li_stmt)
        for li in li_result.scalars().all():
            division = _csi_to_division(li.csi_code)
            budget_by_division[division] += _dec(li.total_cost)

    # 2) Actuals: PayApplication line items for period_to within the month
    actuals_by_division: dict[str, float] = defaultdict(float)
    total_actuals_this_period = 0.0
    total_actuals_cumulative = 0.0

    pa_stmt = select(PayApplication).where(
        PayApplication.project_id == project_id,
        PayApplication.period_to >= first_day,
        PayApplication.period_to <= last_day,
    )
    pa_result = await db.execute(pa_stmt)
    pay_apps = pa_result.scalars().all()

    for pa in pay_apps:
        for pa_li in pa.line_items or []:
            division = _item_number_to_division(pa_li.item_number)
            amount = _dec(pa_li.work_completed_this_period)
            actuals_by_division[division] += amount
            total_actuals_this_period += amount

    # Cumulative actuals (all pay apps up to end of month)
    cum_stmt = (
        select(func.coalesce(func.sum(PayApplicationLineItem.total_completed_and_stored), 0))
        .join(
            PayApplication,
            PayApplicationLineItem.pay_application_id == PayApplication.id,
        )
        .where(
            PayApplication.project_id == project_id,
            PayApplication.period_to <= last_day,
        )
    )
    cum_result = await db.execute(cum_stmt)
    total_actuals_cumulative = _dec(cum_result.scalar())

    # 3) Change orders (approved) impact
    co_stmt = select(ChangeOrder).where(
        ChangeOrder.project_id == project_id,
        ChangeOrder.status == "approved",
    )
    co_result = await db.execute(co_stmt)
    change_orders = co_result.scalars().all()

    co_total_cost = sum(_dec(co.cost_impact) for co in change_orders)
    co_total_schedule_days = sum(co.schedule_impact_days or 0 for co in change_orders)
    co_summary = [
        {
            "co_number": co.co_number,
            "title": co.title,
            "cost_impact": _dec(co.cost_impact),
            "schedule_impact_days": co.schedule_impact_days or 0,
            "status": co.status,
        }
        for co in change_orders
    ]

    # 4) Cost projection from latest EVM snapshot
    projection = {}
    evm_stmt = (
        select(EVMSnapshot)
        .where(EVMSnapshot.project_id == project_id)
        .order_by(EVMSnapshot.snapshot_date.desc())
        .limit(1)
    )
    evm_result = await db.execute(evm_stmt)
    evm = evm_result.scalar_one_or_none()
    if evm:
        projection = {
            "bac": _dec(evm.bac),
            "eac": _dec(evm.eac),
            "etc": _dec(evm.etc),
            "vac": _dec(evm.vac),
            "cpi": _dec(evm.cpi),
            "spi": _dec(evm.spi),
            "percent_complete": _dec(evm.percent_complete),
        }

    # 5) Cash flow snapshot if available
    cf_stmt = (
        select(CashFlowSnapshot)
        .where(
            CashFlowSnapshot.project_id == project_id,
            CashFlowSnapshot.snapshot_date <= last_day,
        )
        .order_by(CashFlowSnapshot.snapshot_date.desc())
        .limit(1)
    )
    cf_result = await db.execute(cf_stmt)
    cf_snapshot = cf_result.scalar_one_or_none()
    cash_flow_summary = cf_snapshot.forecast_data if cf_snapshot else None

    # 6) Build budget vs actual by division
    all_divisions = sorted(set(list(budget_by_division.keys()) + list(actuals_by_division.keys())))
    budget_vs_actual = []
    for div in all_divisions:
        budg = budget_by_division.get(div, 0.0)
        act = actuals_by_division.get(div, 0.0)
        budget_vs_actual.append(
            {
                "division": div,
                "budget": budg,
                "actual_this_period": act,
                "variance": round(budg - act, 2),
                "percent_spent": _safe_div(act, budg) * 100 if budg else 0.0,
            }
        )

    adjusted_budget = total_budget + co_total_cost

    return {
        "report_type": "monthly_cost",
        "project_id": str(project_id),
        "period": {"month": month, "year": year},
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "original_budget": total_budget,
            "approved_cos_total": co_total_cost,
            "adjusted_budget": adjusted_budget,
            "actuals_this_period": total_actuals_this_period,
            "actuals_cumulative": total_actuals_cumulative,
            "remaining_budget": round(adjusted_budget - total_actuals_cumulative, 2),
            "percent_spent": _safe_div(total_actuals_cumulative, adjusted_budget) * 100,
        },
        "budget_vs_actual_by_division": budget_vs_actual,
        "change_order_summary": {
            "count": len(change_orders),
            "total_cost_impact": co_total_cost,
            "total_schedule_impact_days": co_total_schedule_days,
            "items": co_summary,
        },
        "projection": projection,
        "cash_flow_summary": cash_flow_summary,
    }


# ---------------------------------------------------------------------------
# 2. Schedule Performance Report
# ---------------------------------------------------------------------------


async def generate_schedule_performance_report(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Schedule Performance Report: SPI trend, critical path activities,
    delay analysis, 2-week lookahead.
    """
    today = date.today()
    six_months_ago = today - timedelta(days=180)
    two_weeks_ahead = today + timedelta(days=14)

    # 1) SPI/CPI trend (last 6 months)
    evm_stmt = (
        select(EVMSnapshot)
        .where(
            EVMSnapshot.project_id == project_id,
            EVMSnapshot.snapshot_date >= six_months_ago,
        )
        .order_by(EVMSnapshot.snapshot_date.asc())
    )
    evm_result = await db.execute(evm_stmt)
    evm_snapshots = evm_result.scalars().all()

    spi_trend = [
        {
            "date": snap.snapshot_date.isoformat(),
            "spi": _dec(snap.spi),
            "cpi": _dec(snap.cpi),
            "percent_complete": _dec(snap.percent_complete),
        }
        for snap in evm_snapshots
    ]

    # Latest EVM for current status
    latest_evm = evm_snapshots[-1] if evm_snapshots else None
    current_spi = _dec(latest_evm.spi) if latest_evm else None
    current_cpi = _dec(latest_evm.cpi) if latest_evm else None

    # 2) Critical path activities
    critical_stmt = (
        select(ScheduleActivity)
        .where(
            ScheduleActivity.project_id == project_id,
            ScheduleActivity.is_critical.is_(True),
        )
        .order_by(ScheduleActivity.start_date.asc().nullslast())
    )
    critical_result = await db.execute(critical_stmt)
    critical_activities = [
        {
            "id": str(act.id),
            "name": act.name,
            "activity_code": act.activity_code,
            "duration_days": act.duration_days,
            "start_date": act.start_date.isoformat() if act.start_date else None,
            "finish_date": act.finish_date.isoformat() if act.finish_date else None,
            "total_float": act.total_float,
            "status": act.status,
            "pct_complete": _dec(act.pct_complete),
        }
        for act in critical_result.scalars().all()
    ]

    # 3) Delay analysis: activities behind schedule (actual vs planned)
    delay_stmt = (
        select(ScheduleActivity)
        .where(
            ScheduleActivity.project_id == project_id,
            ScheduleActivity.status != "completed",
            ScheduleActivity.finish_date < today,  # should have finished by now
        )
        .order_by(ScheduleActivity.finish_date.asc())
        .limit(20)
    )
    delay_result = await db.execute(delay_stmt)
    delayed_activities = []
    for act in delay_result.scalars().all():
        days_late = (today - act.finish_date).days if act.finish_date else 0
        delayed_activities.append(
            {
                "id": str(act.id),
                "name": act.name,
                "activity_code": act.activity_code,
                "planned_finish": act.finish_date.isoformat() if act.finish_date else None,
                "days_late": days_late,
                "pct_complete": _dec(act.pct_complete),
                "status": act.status,
            }
        )

    # 4) 2-week lookahead
    lookahead_stmt = (
        select(ScheduleActivity)
        .where(
            ScheduleActivity.project_id == project_id,
            ScheduleActivity.status != "completed",
            ScheduleActivity.start_date >= today,
            ScheduleActivity.start_date <= two_weeks_ahead,
        )
        .order_by(ScheduleActivity.start_date.asc())
    )
    lookahead_result = await db.execute(lookahead_stmt)
    lookahead = [
        {
            "id": str(act.id),
            "name": act.name,
            "activity_code": act.activity_code,
            "start_date": act.start_date.isoformat() if act.start_date else None,
            "finish_date": act.finish_date.isoformat() if act.finish_date else None,
            "duration_days": act.duration_days,
            "is_critical": act.is_critical,
        }
        for act in lookahead_result.scalars().all()
    ]

    # Determine overall schedule status
    if current_spi is not None:
        if current_spi >= 0.95:
            schedule_status = "on_track"
        elif current_spi >= 0.85:
            schedule_status = "at_risk"
        else:
            schedule_status = "behind"
    else:
        schedule_status = "unknown"

    return {
        "report_type": "schedule_performance",
        "project_id": str(project_id),
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "current_spi": current_spi,
            "current_cpi": current_cpi,
            "schedule_status": schedule_status,
            "critical_activity_count": len(critical_activities),
            "delayed_activity_count": len(delayed_activities),
            "lookahead_activity_count": len(lookahead),
        },
        "spi_trend": spi_trend,
        "critical_activities": critical_activities,
        "delayed_activities": delayed_activities,
        "two_week_lookahead": lookahead,
    }


# ---------------------------------------------------------------------------
# 3. Safety Trend Report
# ---------------------------------------------------------------------------


async def generate_safety_trend_report(
    db: AsyncSession,
    project_id: uuid.UUID,
    months: int = 6,
) -> dict:
    """Safety Trend Report: incident rates over time, near-miss trends,
    PPE compliance, top hazards.

    Calculates TRIR and DART equivalents from SafetyAlert records.
    """
    today = date.today()
    start_date = today - timedelta(days=months * 30)

    # 1) Safety alerts by month and type
    alerts_stmt = (
        select(SafetyAlert)
        .where(
            SafetyAlert.project_id == project_id,
            SafetyAlert.created_at >= datetime(start_date.year, start_date.month, start_date.day),
        )
        .order_by(SafetyAlert.created_at.asc())
    )
    alerts_result = await db.execute(alerts_stmt)
    alerts = alerts_result.scalars().all()

    # Aggregate by month
    monthly_data: dict[str, dict] = defaultdict(
        lambda: {
            "total_alerts": 0,
            "critical_alerts": 0,
            "ppe_violations": 0,
            "near_misses": 0,
            "false_positives": 0,
            "acknowledged": 0,
        }
    )

    alert_type_counts: dict[str, int] = defaultdict(int)
    priority_counts: dict[str, int] = defaultdict(int)

    for alert in alerts:
        month_key = alert.created_at.strftime("%Y-%m")
        monthly_data[month_key]["total_alerts"] += 1
        priority_counts[alert.priority] += 1
        alert_type_counts[alert.alert_type] += 1

        if alert.priority == "critical":
            monthly_data[month_key]["critical_alerts"] += 1
        if alert.alert_type in ("no_hard_hat", "no_vest", "ppe_violation", "missing_ppe"):
            monthly_data[month_key]["ppe_violations"] += 1
        if alert.alert_type in ("near_miss", "close_call"):
            monthly_data[month_key]["near_misses"] += 1
        if alert.is_false_positive:
            monthly_data[month_key]["false_positives"] += 1
        if alert.is_acknowledged:
            monthly_data[month_key]["acknowledged"] += 1

    # Build monthly trend
    monthly_trend = []
    for month_key in sorted(monthly_data.keys()):
        data = monthly_data[month_key]
        total = data["total_alerts"]
        monthly_trend.append(
            {
                "month": month_key,
                "total_alerts": total,
                "critical_alerts": data["critical_alerts"],
                "ppe_violations": data["ppe_violations"],
                "near_misses": data["near_misses"],
                "false_positive_rate": _safe_div(data["false_positives"], total),
                "acknowledgment_rate": _safe_div(data["acknowledged"], total),
            }
        )

    # 2) Top hazards (by alert_type)
    top_hazards = sorted(alert_type_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # 3) Overall statistics
    total_alerts = len(alerts)
    total_critical = sum(1 for a in alerts if a.priority == "critical")
    total_false_pos = sum(1 for a in alerts if a.is_false_positive)
    total_acknowledged = sum(1 for a in alerts if a.is_acknowledged)

    # TRIR approximation: (critical alerts * 200,000) / estimated work hours
    # Using a standard approximation of 2000 work hours per month as baseline
    estimated_work_hours = months * 2000 * 50  # 50 workers estimate
    trir = _safe_div(total_critical * 200_000, estimated_work_hours)

    return {
        "report_type": "safety_trend",
        "project_id": str(project_id),
        "generated_at": datetime.now(UTC).isoformat(),
        "period": {
            "months": months,
            "start_date": start_date.isoformat(),
            "end_date": today.isoformat(),
        },
        "summary": {
            "total_alerts": total_alerts,
            "critical_alerts": total_critical,
            "false_positive_rate": _safe_div(total_false_pos, total_alerts),
            "acknowledgment_rate": _safe_div(total_acknowledged, total_alerts),
            "estimated_trir": trir,
        },
        "monthly_trend": monthly_trend,
        "top_hazards": [{"type": h[0], "count": h[1]} for h in top_hazards],
        "priority_distribution": dict(priority_counts),
        "alert_type_distribution": dict(alert_type_counts),
    }


# ---------------------------------------------------------------------------
# 4. Subcontractor Performance Report
# ---------------------------------------------------------------------------


async def generate_subcontractor_performance_report(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Subcontractor Performance Report: schedule adherence, quality metrics,
    safety compliance, RFI responsiveness per subcontractor.
    """
    # 1) Get all subcontractor profiles for the project
    sub_stmt = select(SubcontractorProfile).where(
        SubcontractorProfile.project_id == project_id,
    )
    sub_result = await db.execute(sub_stmt)
    subs = sub_result.scalars().all()

    if not subs:
        return {
            "report_type": "subcontractor_performance",
            "project_id": str(project_id),
            "generated_at": datetime.now(UTC).isoformat(),
            "subcontractor_count": 0,
            "scorecards": [],
        }

    scorecards = []
    for sub in subs:
        scorecard = await _build_sub_scorecard(db, project_id, sub)
        scorecards.append(scorecard)

    # Sort by overall score descending
    scorecards.sort(key=lambda s: s["overall_score"], reverse=True)

    return {
        "report_type": "subcontractor_performance",
        "project_id": str(project_id),
        "generated_at": datetime.now(UTC).isoformat(),
        "subcontractor_count": len(scorecards),
        "scorecards": scorecards,
    }


async def _build_sub_scorecard(
    db: AsyncSession,
    project_id: uuid.UUID,
    sub: SubcontractorProfile,
) -> dict:
    """Build performance scorecard for a single subcontractor."""

    # Submissions (manpower consistency)
    sub_stmt = (
        select(SubcontractorSubmission)
        .where(SubcontractorSubmission.profile_id == sub.id)
        .order_by(SubcontractorSubmission.submission_date.desc())
    )
    sub_result = await db.execute(sub_stmt)
    submissions = sub_result.scalars().all()

    total_submissions = len(submissions)
    on_time_submissions = sum(1 for s in submissions if s.status in ("approved", "accepted"))
    submission_rate = _safe_div(on_time_submissions, total_submissions) if total_submissions else 0

    # Defects by trade (match sub's trade against defect_type)
    defect_stmt = (
        select(func.count())
        .select_from(DefectReport)
        .where(
            DefectReport.project_id == project_id,
            DefectReport.defect_type.ilike(f"%{sub.trade}%"),
        )
    )
    defect_result = await db.execute(defect_stmt)
    defect_count = defect_result.scalar() or 0

    # RFI response times (for RFIs assigned to the sub's user)
    rfi_stmt = select(RFI).where(
        RFI.project_id == project_id,
        RFI.assigned_to == sub.user_id,
    )
    rfi_result = await db.execute(rfi_stmt)
    rfis = rfi_result.scalars().all()

    rfi_count = len(rfis)
    rfi_answered = sum(1 for r in rfis if r.date_answered is not None)
    avg_response_days = 0.0
    if rfi_answered > 0:
        response_days = []
        for r in rfis:
            if r.date_answered and r.created_at:
                delta = r.date_answered - r.created_at
                response_days.append(delta.total_seconds() / 86400)
        if response_days:
            avg_response_days = round(sum(response_days) / len(response_days), 1)

    # Score components (each 0-100)
    submission_score = min(100, int(submission_rate * 100))
    quality_score = max(0, 100 - defect_count * 10)  # -10 per defect
    rfi_response_score = _rfi_responsiveness_score(avg_response_days, rfi_count)

    overall_score = int(submission_score * 0.30 + quality_score * 0.40 + rfi_response_score * 0.30)

    return {
        "subcontractor_id": str(sub.id),
        "company_name": sub.company_name,
        "trade": sub.trade,
        "status": sub.status,
        "overall_score": overall_score,
        "scores": {
            "submission_compliance": submission_score,
            "quality": quality_score,
            "rfi_responsiveness": rfi_response_score,
        },
        "metrics": {
            "total_submissions": total_submissions,
            "approved_submissions": on_time_submissions,
            "defect_count": defect_count,
            "rfi_count": rfi_count,
            "rfis_answered": rfi_answered,
            "avg_rfi_response_days": avg_response_days,
        },
    }


def _rfi_responsiveness_score(avg_days: float, count: int) -> int:
    """Score 0-100 based on average RFI response time."""
    if count == 0:
        return 100  # no RFIs = no issues
    if avg_days <= 2:
        return 100
    elif avg_days <= 5:
        return 80
    elif avg_days <= 10:
        return 60
    elif avg_days <= 20:
        return 40
    else:
        return 20


# ---------------------------------------------------------------------------
# Division helpers
# ---------------------------------------------------------------------------


def _csi_to_division(csi_code: str | None) -> str:
    """Extract CSI division (first 2 digits) from a code like '03 30 00'."""
    if not csi_code:
        return "Unclassified"
    cleaned = csi_code.replace(" ", "").replace("-", "")
    if len(cleaned) >= 2 and cleaned[:2].isdigit():
        div_num = int(cleaned[:2])
        return _DIVISION_NAMES.get(div_num, f"Division {div_num:02d}")
    return "Unclassified"


def _item_number_to_division(item_number: str | None) -> str:
    """Attempt to map SOV item number to a CSI division."""
    if not item_number:
        return "Unclassified"
    # Item numbers often start with the division number
    cleaned = item_number.replace(" ", "").replace("-", "").replace(".", "")
    if len(cleaned) >= 2 and cleaned[:2].isdigit():
        div_num = int(cleaned[:2])
        if div_num in _DIVISION_NAMES:
            return _DIVISION_NAMES[div_num]
    return "General"


# ---------------------------------------------------------------------------
# 5. Portfolio Summary Report (RP-05)
# ---------------------------------------------------------------------------


async def generate_portfolio_summary_report(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> dict:
    """Executive-level cross-project summary: project count by status,
    total contract value, aggregate EVM metrics (avg SPI/CPI), safety
    metrics (total incidents), and budget summary.
    """
    # 1) Get all projects for the org
    project_stmt = select(Project).where(Project.org_id == org_id)
    project_result = await db.execute(project_stmt)
    projects = project_result.scalars().all()

    project_ids = [p.id for p in projects]

    # 2) Projects by status
    status_counts: dict[str, int] = defaultdict(int)
    total_contract_value = 0.0
    for p in projects:
        status_counts[p.status or "unknown"] += 1
        if p.contract_value:
            total_contract_value += float(p.contract_value)

    projects_by_status = [{"status": s, "count": c} for s, c in sorted(status_counts.items())]

    # 3) Aggregate EVM metrics (latest snapshot per project)
    spi_values = []
    cpi_values = []
    if project_ids:
        for pid in project_ids:
            evm_stmt = (
                select(EVMSnapshot)
                .where(EVMSnapshot.project_id == pid)
                .order_by(EVMSnapshot.snapshot_date.desc())
                .limit(1)
            )
            evm_result = await db.execute(evm_stmt)
            snap = evm_result.scalar_one_or_none()
            if snap:
                if snap.spi is not None:
                    spi_values.append(float(snap.spi))
                if snap.cpi is not None:
                    cpi_values.append(float(snap.cpi))

    avg_spi = round(sum(spi_values) / len(spi_values), 4) if spi_values else None
    avg_cpi = round(sum(cpi_values) / len(cpi_values), 4) if cpi_values else None

    # 4) Safety metrics: total alerts across all projects
    total_incidents = 0
    total_critical = 0
    if project_ids:
        incident_stmt = select(func.count(SafetyAlert.id)).where(
            SafetyAlert.project_id.in_(project_ids)
        )
        incident_result = await db.execute(incident_stmt)
        total_incidents = incident_result.scalar() or 0

        critical_stmt = select(func.count(SafetyAlert.id)).where(
            SafetyAlert.project_id.in_(project_ids),
            SafetyAlert.priority == "critical",
        )
        critical_result = await db.execute(critical_stmt)
        total_critical = critical_result.scalar() or 0

    # 5) Budget summary from pay applications
    total_actuals = 0.0
    if project_ids:
        actuals_stmt = select(
            func.coalesce(func.sum(PayApplication.total_completed_and_stored), 0)
        ).where(PayApplication.project_id.in_(project_ids))
        actuals_result = await db.execute(actuals_stmt)
        total_actuals = float(actuals_result.scalar() or 0)

    return {
        "report_type": "portfolio_summary",
        "org_id": str(org_id),
        "generated_at": datetime.now(UTC).isoformat(),
        "project_count": len(projects),
        "projects_by_status": projects_by_status,
        "budget_summary": {
            "total_contract_value": round(total_contract_value, 2),
            "total_actuals": round(total_actuals, 2),
            "total_remaining": round(total_contract_value - total_actuals, 2),
        },
        "evm_summary": {
            "avg_spi": avg_spi,
            "avg_cpi": avg_cpi,
            "projects_with_evm": len(spi_values),
        },
        "safety_summary": {
            "total_incidents": total_incidents,
            "total_critical": total_critical,
        },
    }


# ---------------------------------------------------------------------------
# 6. OSHA Compliance Report (RP-06)
# ---------------------------------------------------------------------------


async def generate_osha_compliance_report(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Maps project activities to applicable OSHA 1926 sections.
    Shows compliance status per activity.
    """
    # Import the OSHA lookup function
    try:
        from scripts.ingest_osha_standards import get_applicable_osha_standards
    except ImportError:
        # Fallback: define a minimal stub if the script isn't importable
        def get_applicable_osha_standards(activity_type: str) -> list[dict]:
            return []

    # Get project activities
    activities_stmt = (
        select(ScheduleActivity)
        .where(ScheduleActivity.project_id == project_id)
        .order_by(ScheduleActivity.start_date.asc().nullslast())
    )
    activities_result = await db.execute(activities_stmt)
    activities = activities_result.scalars().all()

    # Map each activity to OSHA standards
    all_standards: list[dict] = []
    activity_names_analyzed: set[str] = set()

    for act in activities:
        # Try to match activity name to OSHA activity types
        name_lower = (act.name or "").lower()
        activity_names_analyzed.add(act.name or "unknown")

        # Check for common construction activity keywords
        _KEYWORD_MAP = {
            "excavat": "excavation",
            "trench": "trenching",
            "scaffold": "scaffolding",
            "crane": "crane_operations",
            "concrete": "concrete_construction",
            "steel": "steel_erection",
            "electrical": "electrical_work",
            "roof": "roofing",
            "demolition": "demolition",
            "weld": "welding",
            "ladder": "ladders",
            "confine": "confined_spaces",
            "paint": "ppe",
            "fall": "fall_protection",
        }

        for keyword, activity_type in _KEYWORD_MAP.items():
            if keyword in name_lower:
                standards = get_applicable_osha_standards(activity_type)
                for std in standards:
                    entry = {
                        "activity": act.name,
                        "standard": std.get("standard", ""),
                        "subpart": std.get("subpart", ""),
                        "topic": std.get("topic", ""),
                    }
                    if entry not in all_standards:
                        all_standards.append(entry)

    return {
        "report_type": "osha_compliance",
        "project_id": str(project_id),
        "generated_at": datetime.now(UTC).isoformat(),
        "activities_analyzed": len(activity_names_analyzed),
        "applicable_standards": all_standards,
        "total_standards": len(all_standards),
    }


# ---------------------------------------------------------------------------
# 7. Certified Payroll Report (RP-07)
# ---------------------------------------------------------------------------


async def generate_certified_payroll_report(
    db: AsyncSession,
    project_id: uuid.UUID,
    period_start: date,
    period_end: date,
) -> dict:
    """Formatted certified payroll: pulls from PayrollRecord, generates WH-347 data."""
    from app.models.payroll import PayrollRecord
    from app.services.compliance.payroll_engine import generate_wh347_data

    # Fetch payroll records for the period
    payroll_stmt = (
        select(PayrollRecord)
        .where(
            PayrollRecord.project_id == project_id,
            PayrollRecord.pay_period_start >= period_start,
            PayrollRecord.pay_period_end <= period_end,
        )
        .order_by(PayrollRecord.pay_period_start.asc())
    )
    payroll_result = await db.execute(payroll_stmt)
    records = payroll_result.scalars().all()

    # Convert records to dicts for the engine
    payroll_dicts = []
    for rec in records:
        payroll_dicts.append(
            {
                "worker_name": rec.worker_name,
                "trade": rec.trade,
                "classification": rec.classification,
                "hours_straight": float(rec.hours_straight) if rec.hours_straight else 0.0,
                "hours_overtime": float(rec.hours_overtime) if rec.hours_overtime else 0.0,
                "hours_other": float(rec.hours_other) if rec.hours_other else 0.0,
                "rate_straight": float(rec.rate_straight)
                if hasattr(rec, "rate_straight") and rec.rate_straight
                else 0.0,
                "rate_overtime": float(rec.rate_overtime)
                if hasattr(rec, "rate_overtime") and rec.rate_overtime
                else 0.0,
                "gross_pay": float(rec.gross_pay) if rec.gross_pay else 0.0,
            }
        )

    # Get project info for the WH-347 form
    project = await db.get(Project, project_id)
    project_info = {
        "name": project.name if project else "Unknown",
        "contract_number": getattr(project, "contract_number", "") or "",
        "location": getattr(project, "location", "") or "",
    }
    contractor_info = {
        "name": "",
        "address": "",
        "ein": "",
    }

    wh347 = generate_wh347_data(
        contractor_info=contractor_info,
        project_info=project_info,
        payroll_records=payroll_dicts,
        period_start=period_start,
        period_end=period_end,
    )

    # Convert WH347Report dataclass to dict for JSON serialization
    import dataclasses

    wh347_dict = dataclasses.asdict(wh347) if dataclasses.is_dataclass(wh347) else wh347

    return {
        "report_type": "certified_payroll",
        "project_id": str(project_id),
        "generated_at": datetime.now(UTC).isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "wh347_data": wh347_dict,
    }


# ---------------------------------------------------------------------------
# 8. EVM Report (RP-08)
# ---------------------------------------------------------------------------


async def generate_evm_report(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """EVM report with S-curve data, variance analysis, EAC projections."""
    # Get all EVM snapshots for trend data
    evm_stmt = (
        select(EVMSnapshot)
        .where(EVMSnapshot.project_id == project_id)
        .order_by(EVMSnapshot.snapshot_date.asc())
    )
    evm_result = await db.execute(evm_stmt)
    snapshots = evm_result.scalars().all()

    # Build trend data
    # EVMSnapshot columns: pv, ev, ac, sv, cv, spi, cpi, bac, eac, etc, vac, tcpi
    trend_data = []
    s_curve_data = []
    for snap in snapshots:
        ev = _dec(snap.ev)
        pv = _dec(snap.pv)
        ac = _dec(snap.ac)
        spi = _dec(snap.spi)
        cpi = _dec(snap.cpi)
        sv = _dec(snap.sv)
        cv = _dec(snap.cv)

        trend_data.append(
            {
                "date": snap.snapshot_date.isoformat(),
                "earned_value": ev,
                "planned_value": pv,
                "actual_cost": ac,
                "spi": spi,
                "cpi": cpi,
                "sv": sv,
                "cv": cv,
            }
        )

        s_curve_data.append(
            {
                "date": snap.snapshot_date.isoformat(),
                "planned": pv,
                "earned": ev,
                "actual": ac,
            }
        )

    # Current status from latest snapshot
    latest = snapshots[-1] if snapshots else None
    current_status: dict = {}
    projection: dict | None = None

    if latest:
        current_status = {
            "snapshot_date": latest.snapshot_date.isoformat(),
            "percent_complete": _dec(latest.percent_complete),
            "spi": _dec(latest.spi),
            "cpi": _dec(latest.cpi),
            "bac": _dec(latest.bac),
            "eac": _dec(latest.eac),
        }

        bac = _dec(latest.bac)
        eac = _dec(latest.eac)
        etc = _dec(latest.etc)
        vac = _dec(latest.vac)
        tcpi = _dec(latest.tcpi)

        projection = {
            "bac": bac,
            "eac": eac,
            "etc": etc,
            "vac": vac,
            "tcpi": tcpi,
        }

    return {
        "report_type": "evm",
        "project_id": str(project_id),
        "generated_at": datetime.now(UTC).isoformat(),
        "current_status": current_status,
        "trend_data": trend_data,
        "projection": projection,
        "s_curve_data": s_curve_data,
    }


# ---------------------------------------------------------------------------
# Division helpers
# ---------------------------------------------------------------------------


_DIVISION_NAMES: dict[int, str] = {
    0: "Div 00 - Procurement",
    1: "Div 01 - General Requirements",
    2: "Div 02 - Existing Conditions",
    3: "Div 03 - Concrete",
    4: "Div 04 - Masonry",
    5: "Div 05 - Metals",
    6: "Div 06 - Wood/Plastics/Composites",
    7: "Div 07 - Thermal & Moisture Protection",
    8: "Div 08 - Openings",
    9: "Div 09 - Finishes",
    10: "Div 10 - Specialties",
    11: "Div 11 - Equipment",
    12: "Div 12 - Furnishings",
    13: "Div 13 - Special Construction",
    14: "Div 14 - Conveying Equipment",
    21: "Div 21 - Fire Suppression",
    22: "Div 22 - Plumbing",
    23: "Div 23 - HVAC",
    25: "Div 25 - Integrated Automation",
    26: "Div 26 - Electrical",
    27: "Div 27 - Communications",
    28: "Div 28 - Electronic Safety & Security",
    31: "Div 31 - Earthwork",
    32: "Div 32 - Exterior Improvements",
    33: "Div 33 - Utilities",
}
