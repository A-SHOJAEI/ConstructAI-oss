"""Workforce analytics API endpoints.

Routes for labor aggregation, productivity metrics, labor forecasting,
overtime prediction, fatigue risk assessment, craft availability, and
workforce snapshots.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.models.workforce import WorkforceSnapshot
from app.schemas.workforce import (
    FatigueRequest,
    OvertimeRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Labor aggregation
# ---------------------------------------------------------------------------


@router.get("/{project_id}/workforce/analytics")
async def get_labor_analytics(
    project_id: uuid.UUID,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    current_user: User = Depends(require_permission("productivity", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated labor data for a project."""
    await verify_project_access(project_id, current_user, db)

    from app.services.productivity.workforce_analytics import aggregate_labor_data

    date_range = None
    if date_from and date_to:
        date_range = (date_from, date_to)
    elif date_from:
        date_range = (date_from, date.today())

    aggregate = await aggregate_labor_data(
        db=db,
        project_id=str(project_id),
        date_range=date_range,
    )

    return {
        "project_id": aggregate.project_id,
        "date_range": (
            [aggregate.date_range[0].isoformat(), aggregate.date_range[1].isoformat()]
            if aggregate.date_range
            else None
        ),
        "total_workers": aggregate.total_workers,
        "total_manhours": aggregate.total_manhours,
        "workers_by_trade": aggregate.workers_by_trade,
        "manhours_by_trade": aggregate.manhours_by_trade,
        "daily_avg_workers": aggregate.daily_avg_workers,
        "daily_avg_manhours": aggregate.daily_avg_manhours,
        "working_days": aggregate.working_days,
    }


# ---------------------------------------------------------------------------
# Productivity metrics
# ---------------------------------------------------------------------------


@router.get("/{project_id}/workforce/productivity")
async def get_productivity_metrics(
    project_id: uuid.UUID,
    trade: str | None = Query(default=None, description="Filter by trade"),
    current_user: User = Depends(require_permission("productivity", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get trade-level productivity metrics from crew records."""
    await verify_project_access(project_id, current_user, db)

    from app.models.productivity import CrewProductivity
    from app.services.productivity.workforce_analytics import calculate_productivity_metrics

    pid = project_id

    query = select(CrewProductivity).where(CrewProductivity.project_id == pid)
    if trade:
        query = query.where(CrewProductivity.trade == trade)
    query = query.order_by(CrewProductivity.work_date.asc())

    result = await db.execute(query)
    records = list(result.scalars().all())

    crew_dicts = [
        {
            "trade": r.trade,
            "actual_units": float(r.actual_units),
            "planned_units": float(r.planned_units),
            "crew_size": r.crew_size,
            "work_date": r.work_date.isoformat(),
            "unit_of_measure": r.unit_of_measure,
        }
        for r in records
    ]

    metrics = calculate_productivity_metrics(crew_dicts)

    return {
        "metrics": [
            {
                "trade": m.trade,
                "activity_type": m.activity_type,
                "avg_manhours_per_unit": m.avg_manhours_per_unit,
                "median_manhours_per_unit": m.median_manhours_per_unit,
                "std_dev": m.std_dev,
                "sample_count": m.sample_count,
                "trend": m.trend,
                "trend_slope": m.trend_slope,
                "unit_of_measure": m.unit_of_measure,
            }
            for m in metrics
        ],
        "total_trades": len(metrics),
    }


# ---------------------------------------------------------------------------
# Labor forecast
# ---------------------------------------------------------------------------


@router.get("/{project_id}/workforce/forecast")
async def get_labor_forecast(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("productivity", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Forecast remaining labor needs based on incomplete schedule activities."""
    await verify_project_access(project_id, current_user, db)

    from app.services.productivity.workforce_analytics import forecast_labor_needs

    forecast = await forecast_labor_needs(db=db, project_id=str(project_id))

    return {
        "project_id": forecast.project_id,
        "forecast_date": forecast.forecast_date,
        "remaining_activities": forecast.remaining_activities,
        "total_remaining_manhours": forecast.total_remaining_manhours,
        "by_trade": forecast.by_trade,
        "by_month": forecast.by_month,
        "estimated_completion_date": forecast.estimated_completion_date,
    }


# ---------------------------------------------------------------------------
# Overtime prediction
# ---------------------------------------------------------------------------


@router.post("/{project_id}/workforce/overtime")
async def predict_overtime_endpoint(
    project_id: uuid.UUID,
    request: OvertimeRequest,
    current_user: User = Depends(require_permission("productivity", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Predict overtime needs based on remaining work and available workforce."""
    await verify_project_access(project_id, current_user, db)

    from app.services.productivity.workforce_analytics import predict_overtime

    prediction = predict_overtime(
        remaining_activities=request.remaining_activities,
        available_workforce=request.available_workforce,
        schedule_compression_pct=request.schedule_compression_pct,
        avg_hourly_rate=request.avg_hourly_rate,
    )

    return {
        "project_id": str(project_id),
        "schedule_compression_pct": prediction.schedule_compression_pct,
        "total_remaining_manhours": prediction.total_remaining_manhours,
        "standard_hours_available": prediction.standard_hours_available,
        "predicted_overtime_hours": prediction.predicted_overtime_hours,
        "overtime_pct": prediction.overtime_pct,
        "estimated_overtime_cost": prediction.estimated_overtime_cost,
        "overtime_rate_multiplier": prediction.overtime_rate_multiplier,
        "risk_level": prediction.risk_level,
        "recommendation": prediction.recommendation,
    }


# ---------------------------------------------------------------------------
# Fatigue risk assessment
# ---------------------------------------------------------------------------


@router.post("/{project_id}/workforce/fatigue")
async def assess_fatigue_endpoint(
    project_id: uuid.UUID,
    request: FatigueRequest,
    current_user: User = Depends(require_permission("productivity", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Assess fatigue risk for workers based on hours worked."""
    await verify_project_access(project_id, current_user, db)

    from app.services.productivity.workforce_analytics import assess_fatigue_risk

    alerts = assess_fatigue_risk(
        worker_hours=request.worker_hours,
        threshold_daily=request.threshold_daily,
        threshold_weekly=request.threshold_weekly,
    )

    red_count = sum(1 for a in alerts if a.risk_level == "red")
    yellow_count = sum(1 for a in alerts if a.risk_level == "yellow")

    return {
        "alerts": [
            {
                "worker_id": a.worker_id,
                "trade": a.trade,
                "alert_type": a.alert_type,
                "hours_worked": a.hours_worked,
                "threshold": a.threshold,
                "excess_hours": a.excess_hours,
                "risk_level": a.risk_level,
                "recommendation": a.recommendation,
            }
            for a in alerts
        ],
        "total_alerts": len(alerts),
        "red_alerts": red_count,
        "yellow_alerts": yellow_count,
    }


# ---------------------------------------------------------------------------
# Craft availability
# ---------------------------------------------------------------------------


@router.get("/{project_id}/workforce/availability")
async def get_craft_availability_endpoint(
    project_id: uuid.UUID,
    trades: str | None = Query(default=None, description="Comma-separated trade list"),
    current_user: User = Depends(require_permission("productivity", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Analyze supply-demand gap by trade."""
    await verify_project_access(project_id, current_user, db)

    from app.services.productivity.workforce_analytics import get_craft_availability

    trade_list = [t.strip() for t in trades.split(",")] if trades else None

    availability = await get_craft_availability(
        db=db,
        project_id=str(project_id),
        trades=trade_list,
    )

    return {
        "project_id": availability.project_id,
        "forecast_date": availability.forecast_date,
        "trades": availability.trades,
        "total_demand_manhours": availability.total_demand_manhours,
        "total_supply_workers": availability.total_supply_workers,
        "overall_gap_pct": availability.overall_gap_pct,
    }


# ---------------------------------------------------------------------------
# Workforce snapshots
# ---------------------------------------------------------------------------


@router.post("/{project_id}/workforce/snapshot")
async def create_snapshot(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("productivity", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Create a point-in-time workforce snapshot from current daily log data."""
    await verify_project_access(project_id, current_user, db)

    from app.services.productivity.workforce_analytics import create_workforce_snapshot

    snapshot = await create_workforce_snapshot(db=db, project_id=str(project_id))
    await db.commit()

    return snapshot


@router.get("/{project_id}/workforce/snapshots")
async def list_workforce_snapshots(
    project_id: uuid.UUID,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_permission("productivity", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List workforce snapshots for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(WorkforceSnapshot)
        .where(WorkforceSnapshot.project_id == project_id)
        .order_by(WorkforceSnapshot.snapshot_date.desc())
    )
    if start_date:
        query = query.where(WorkforceSnapshot.snapshot_date >= start_date)
    if end_date:
        query = query.where(WorkforceSnapshot.snapshot_date <= end_date)

    result = await db.execute(query.offset(skip).limit(limit))
    snapshots = list(result.scalars().all())
    return {
        "data": [
            {
                "id": str(s.id),
                "project_id": str(s.project_id),
                "snapshot_date": s.snapshot_date.isoformat(),
                "total_workers": s.total_workers,
                "workers_by_trade": s.workers_by_trade,
                "total_manhours": float(s.total_manhours),
                "overtime_hours": float(s.overtime_hours),
                "overtime_pct": float(s.overtime_pct),
                "fatigue_flags": s.fatigue_flags,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in snapshots
        ],
        "count": len(snapshots),
    }


@router.get("/{project_id}/workforce/latest")
async def get_latest_workforce_snapshot(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("productivity", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the most recent workforce snapshot for a project."""
    await verify_project_access(project_id, current_user, db)

    result = await db.execute(
        select(WorkforceSnapshot)
        .where(WorkforceSnapshot.project_id == project_id)
        .order_by(WorkforceSnapshot.snapshot_date.desc())
        .limit(1)
    )
    snapshot = result.scalars().first()

    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No workforce snapshots found for this project",
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
# Portfolio view (org-wide)
# ---------------------------------------------------------------------------


@router.get("/portfolio/workforce")
async def get_portfolio_workforce(
    org_id: str = Query(..., description="Organization UUID"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    current_user: User = Depends(require_permission("productivity", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get portfolio-level workforce summary across all projects in an org."""
    if str(current_user.org_id) != str(org_id):
        raise HTTPException(status_code=404, detail="Organization not found")
    from app.services.productivity.workforce_analytics import aggregate_labor_data

    date_range = None
    if date_from and date_to:
        date_range = (date_from, date_to)

    aggregate = await aggregate_labor_data(
        db=db,
        org_id=org_id,
        date_range=date_range,
    )

    return {
        "org_id": org_id,
        "date_range": (
            [aggregate.date_range[0].isoformat(), aggregate.date_range[1].isoformat()]
            if aggregate.date_range
            else None
        ),
        "total_workers": aggregate.total_workers,
        "total_manhours": aggregate.total_manhours,
        "workers_by_trade": aggregate.workers_by_trade,
        "manhours_by_trade": aggregate.manhours_by_trade,
        "daily_avg_workers": aggregate.daily_avg_workers,
        "working_days": aggregate.working_days,
    }
