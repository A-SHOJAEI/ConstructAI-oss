"""API endpoints for predictive safety risk scoring."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.osha import DailyRiskScore
from app.models.productivity import DailyLog
from app.models.project import Project
from app.models.scheduling import ScheduleActivity
from app.models.user import User

router = APIRouter()


async def _get_project(
    project_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> Project:
    """Fetch and authorize project access."""
    await verify_project_access(project_id, current_user, db)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _project_to_dict(project: Project) -> dict:
    """Convert project ORM to dict for the risk engine."""
    return {
        "name": project.name,
        "type": project.type,
        "address": project.address,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "naics_code": project.metadata_.get("naics_code", ""),
    }


async def _get_today_activities(
    db: AsyncSession,
    project_id: uuid.UUID,
    target_date: date,
) -> list[dict]:
    """Get schedule activities active on a given date."""
    stmt = select(ScheduleActivity).where(
        ScheduleActivity.project_id == project_id,
        ScheduleActivity.start_date <= target_date,
        ScheduleActivity.finish_date >= target_date,
    )
    result = await db.execute(stmt)
    activities = result.scalars().all()
    return [
        {
            "name": a.name,
            "activity_code": a.activity_code,
            "is_critical": a.is_critical,
            "status": a.status,
        }
        for a in activities
    ]


async def _get_daily_log(
    db: AsyncSession,
    project_id: uuid.UUID,
    target_date: date,
) -> dict | None:
    """Get the daily log for a given date."""
    stmt = (
        select(DailyLog)
        .where(
            DailyLog.project_id == project_id,
            DailyLog.log_date == target_date,
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    log = result.scalar_one_or_none()
    if not log:
        return None
    return {
        "crew_count": log.crew_count,
        "manpower_by_trade": log.manpower_by_trade,
        "weather": log.weather,
    }


async def _get_weather(project: Project) -> list[dict] | None:
    """Fetch today's weather forecast for the project location."""
    try:
        from app.services.scheduling.weather_service import get_weather_forecast

        lat = float(project.metadata_.get("latitude", 0))
        lon = float(project.metadata_.get("longitude", 0))
        if lat == 0 and lon == 0:
            return None
        today = date.today()
        return await get_weather_forecast(lat, lon, today.isoformat(), today.isoformat())
    except Exception:
        return None


@router.get("/{project_id}/safety/risk-score")
async def get_risk_score(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get today's predictive safety risk assessment for a project.

    Returns a cached score if one exists for today, otherwise computes live.
    """
    project = await _get_project(project_id, current_user, db)
    today = date.today()

    # Check for cached score
    stmt = (
        select(DailyRiskScore)
        .where(
            DailyRiskScore.project_id == project_id,
            DailyRiskScore.score_date == today,
        )
        .order_by(DailyRiskScore.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    cached = result.scalar_one_or_none()

    if cached:
        return {
            "project_id": str(project_id),
            "score_date": cached.score_date.isoformat(),
            "overall_score": cached.overall_score,
            "overall_label": _score_label(cached.overall_score),
            "category_scores": cached.category_scores,
            "top_risks": cached.top_risks,
            "recommended_mitigations": cached.recommended_mitigations,
            "weather_factors": cached.weather_factors,
            "schedule_factors": cached.schedule_factors,
            "project_factors": cached.project_factors,
            "osha_factors": cached.osha_factors,
            "cached": True,
        }

    # Compute live
    from app.services.safety.predictive_risk import PredictiveRiskEngine, store_risk_score

    engine = PredictiveRiskEngine()
    project_dict = _project_to_dict(project)
    weather = await _get_weather(project)
    activities = await _get_today_activities(db, project_id, today)
    daily_log = await _get_daily_log(db, project_id, today)

    risk = await engine.calculate_daily_risk_score(
        db=db,
        project_id=str(project_id),
        project=project_dict,
        weather=weather,
        today_activities=activities,
        daily_log=daily_log,
    )

    # Persist
    await store_risk_score(db, risk)
    await db.commit()

    return {
        "project_id": str(project_id),
        "score_date": risk.score_date.isoformat(),
        "overall_score": risk.overall_score,
        "overall_label": _score_label(risk.overall_score),
        "category_scores": risk.category_scores,
        "top_risks": risk.top_risks,
        "recommended_mitigations": risk.recommended_mitigations,
        "weather_factors": risk.weather_factors,
        "schedule_factors": risk.schedule_factors,
        "project_factors": risk.project_factors,
        "osha_factors": risk.osha_factors,
        "cached": False,
    }


@router.get("/{project_id}/safety/briefing")
async def get_safety_briefing(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get today's safety briefing for the morning huddle.

    Returns cached briefing if available, generates one if not.
    """
    project = await _get_project(project_id, current_user, db)
    today = date.today()

    # Check for cached briefing
    stmt = (
        select(DailyRiskScore)
        .where(
            DailyRiskScore.project_id == project_id,
            DailyRiskScore.score_date == today,
            DailyRiskScore.safety_briefing.isnot(None),
        )
        .order_by(DailyRiskScore.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    cached = result.scalar_one_or_none()

    if cached and cached.safety_briefing:
        return {
            "project_id": str(project_id),
            "score_date": today.isoformat(),
            "overall_score": cached.overall_score,
            "briefing": cached.safety_briefing,
            "cached": True,
        }

    # Generate new briefing
    from app.services.safety.predictive_risk import PredictiveRiskEngine, store_risk_score

    engine = PredictiveRiskEngine()
    project_dict = _project_to_dict(project)
    weather = await _get_weather(project)
    activities = await _get_today_activities(db, project_id, today)
    daily_log = await _get_daily_log(db, project_id, today)

    risk = await engine.calculate_daily_risk_score(
        db=db,
        project_id=str(project_id),
        project=project_dict,
        weather=weather,
        today_activities=activities,
        daily_log=daily_log,
    )

    briefing = await engine.generate_safety_briefing(
        risk_result=risk,
        project=project_dict,
        weather=weather,
        today_activities=activities,
    )

    # Store briefing with risk score
    await store_risk_score(db, risk)
    # Update the just-inserted record with the briefing
    stmt2 = (
        select(DailyRiskScore)
        .where(
            DailyRiskScore.project_id == project_id,
            DailyRiskScore.score_date == today,
        )
        .order_by(DailyRiskScore.created_at.desc())
        .limit(1)
    )
    result2 = await db.execute(stmt2)
    record = result2.scalar_one_or_none()
    if record:
        record.safety_briefing = briefing
    await db.commit()

    return {
        "project_id": str(project_id),
        "score_date": today.isoformat(),
        "overall_score": risk.overall_score,
        "briefing": briefing,
        "cached": False,
    }


@router.get("/{project_id}/safety/trends")
async def get_risk_trends(
    project_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get risk score history for a project (for dashboard trend chart)."""
    await verify_project_access(project_id, current_user, db)

    since = date.today() - timedelta(days=days)
    stmt = (
        select(DailyRiskScore)
        .where(
            DailyRiskScore.project_id == project_id,
            DailyRiskScore.score_date >= since,
        )
        .order_by(DailyRiskScore.score_date.asc())
    )
    result = await db.execute(stmt)
    scores = result.scalars().all()

    trend_data: list[dict[str, Any]] = [
        {
            "date": s.score_date.isoformat(),
            "overall_score": s.overall_score,
            "category_scores": s.category_scores,
        }
        for s in scores
    ]

    # Compute summary stats
    if trend_data:
        overall_scores: list[float] = [
            float(s["overall_score"]) if s.get("overall_score") is not None else 0.0
            for s in trend_data
        ]
        avg_score = sum(overall_scores) / len(overall_scores)
        max_score = max(overall_scores)
        min_score = min(overall_scores)
        # Trend direction: compare last 7 days avg to prior 7 days
        recent = overall_scores[-7:] if len(overall_scores) >= 7 else overall_scores
        prior = overall_scores[-14:-7] if len(overall_scores) >= 14 else []
        if prior:
            recent_avg = sum(recent) / len(recent)
            prior_avg = sum(prior) / len(prior)
            if recent_avg > prior_avg + 5:
                trend = "increasing"
            elif recent_avg < prior_avg - 5:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"
    else:
        avg_score = 0
        max_score = 0
        min_score = 0
        trend = "no_data"

    return {
        "project_id": str(project_id),
        "period_days": days,
        "data_points": len(trend_data),
        "summary": {
            "average_score": round(avg_score, 1),
            "max_score": max_score,
            "min_score": min_score,
            "trend": trend,
        },
        "daily_scores": trend_data,
    }


def _score_label(score: int) -> str:
    """Convert 0-100 score to human label."""
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "elevated"
    if score >= 20:
        return "moderate"
    return "low"
