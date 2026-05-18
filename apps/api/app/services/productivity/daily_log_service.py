"""Daily log field-data-capture service.

Provides: create / update / submit / approve workflow, weather
auto-population from the Phase-1 weather service, copy-previous-day
template, weekly summary aggregation, and CSV export.

Status workflow:  draft → submitted → approved
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.productivity import DailyLog

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status machine
# ---------------------------------------------------------------------------

VALID_STATUSES = {"draft", "submitted", "approved"}

VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"submitted"},
    "submitted": {"approved", "draft"},  # can return to draft
    "approved": set(),  # terminal
}

# ---------------------------------------------------------------------------
# Create / Read / Update
# ---------------------------------------------------------------------------


async def create_daily_log(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: dict[str, Any],
    created_by: uuid.UUID | None = None,
) -> DailyLog:
    """Create a new daily log in draft status."""
    log = DailyLog(
        project_id=project_id,
        log_date=data["log_date"],
        status="draft",
        weather=data.get("weather", {}),
        crew_count=data.get("crew_count", 0),
        work_hours=data.get("work_hours", Decimal("0")),
        work_narrative=data.get("work_narrative"),
        manpower_by_trade=_serialize_list(data.get("manpower_by_trade", [])),
        equipment_entries=_serialize_list(data.get("equipment_entries", [])),
        deliveries=_serialize_list(data.get("deliveries", [])),
        visitors=_serialize_list(data.get("visitors", [])),
        photos=_serialize_list(data.get("photos", [])),
        activities_completed=data.get("activities_completed", []),
        delays=data.get("delays", []),
        notes=data.get("notes"),
        location_lat=data.get("location_lat"),
        location_lon=data.get("location_lon"),
        safety_incidents=data.get("safety_incidents"),
        safety_topic_discussed=data.get("safety_topic_discussed"),
        weather_delay_hours=data.get("weather_delay_hours"),
        created_by=created_by,
    )
    db.add(log)
    await db.flush()
    await db.refresh(log)
    return log


async def update_daily_log(
    db: AsyncSession,
    log_id: uuid.UUID,
    project_id: uuid.UUID,
    data: dict[str, Any],
) -> DailyLog:
    """Update a daily log.  Only draft logs can be edited."""
    log = await _get_log(db, log_id, project_id)

    if log.status != "draft":
        raise ValueError("Only draft logs can be edited.")

    updatable = {
        "weather",
        "crew_count",
        "work_hours",
        "work_narrative",
        "manpower_by_trade",
        "equipment_entries",
        "deliveries",
        "visitors",
        "photos",
        "activities_completed",
        "delays",
        "notes",
        "location_lat",
        "location_lon",
        "safety_incidents",
        "safety_topic_discussed",
        "weather_delay_hours",
    }
    list_fields = {
        "manpower_by_trade",
        "equipment_entries",
        "deliveries",
        "visitors",
        "photos",
    }

    for key, val in data.items():
        if key in updatable and val is not None:
            if key in list_fields:
                setattr(log, key, _serialize_list(val))
            else:
                setattr(log, key, val)

    await db.flush()
    await db.refresh(log)
    return log


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


async def submit_daily_log(
    db: AsyncSession,
    log_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
) -> DailyLog:
    """Transition draft → submitted."""
    log = await _get_log(db, log_id, project_id)
    _validate_transition(log.status, "submitted")
    log.status = "submitted"
    log.submitted_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(log)
    return log


async def approve_daily_log(
    db: AsyncSession,
    log_id: uuid.UUID,
    project_id: uuid.UUID,
    approver_id: uuid.UUID,
) -> DailyLog:
    """Transition submitted → approved."""
    log = await _get_log(db, log_id, project_id)
    _validate_transition(log.status, "approved")
    log.status = "approved"
    log.approved_by = approver_id
    log.approved_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(log)
    return log


async def reject_to_draft(
    db: AsyncSession,
    log_id: uuid.UUID,
    project_id: uuid.UUID,
) -> DailyLog:
    """Return a submitted log back to draft for corrections."""
    log = await _get_log(db, log_id, project_id)
    _validate_transition(log.status, "draft")
    log.status = "draft"
    log.submitted_at = None
    await db.flush()
    await db.refresh(log)
    return log


# ---------------------------------------------------------------------------
# Weather auto-populate
# ---------------------------------------------------------------------------


async def auto_populate_weather(
    project_lat: float,
    project_lon: float,
    log_date: date,
) -> dict:
    """Fetch weather for the given date and location using the Phase-1
    weather service and return a JSONB-ready dict.

    Returns an empty dict on failure (non-blocking).
    """
    try:
        from app.services.scheduling.weather_service import get_weather_forecast

        date_str = log_date.isoformat()
        forecasts = await get_weather_forecast(
            latitude=project_lat,
            longitude=project_lon,
            start_date=date_str,
            end_date=date_str,
        )
        if forecasts:
            day = forecasts[0]
            return {
                "temperature_high": day.get("temperature_max"),
                "temperature_low": day.get("temperature_min"),
                "precipitation_mm": day.get("precipitation_mm"),
                "wind_speed_max": day.get("wind_speed_max"),
                "humidity": day.get("humidity"),
                "conditions": day.get("weather_code", ""),
                "source": "auto",
                "fetched_at": datetime.now(UTC).isoformat(),
            }
    except Exception:
        logger.warning("Weather auto-populate failed for %s", log_date, exc_info=True)
    return {}


# ---------------------------------------------------------------------------
# Copy previous day
# ---------------------------------------------------------------------------


async def copy_previous_day(
    db: AsyncSession,
    project_id: uuid.UUID,
    target_date: date,
    created_by: uuid.UUID | None = None,
) -> DailyLog:
    """Create a new draft log by copying the previous day's log as a template."""
    prev_date = target_date - timedelta(days=1)
    result = await db.execute(
        select(DailyLog)
        .where(
            DailyLog.project_id == project_id,
            DailyLog.log_date == prev_date,
        )
        .limit(1)
    )
    prev = result.scalars().first()
    if prev is None:
        raise ValueError(f"No daily log found for {prev_date}")

    log = DailyLog(
        project_id=project_id,
        log_date=target_date,
        status="draft",
        weather={},  # weather should be re-fetched for new date
        crew_count=prev.crew_count,
        work_hours=Decimal("0"),
        work_narrative=None,
        manpower_by_trade=prev.manpower_by_trade,
        equipment_entries=prev.equipment_entries,
        deliveries=[],  # deliveries are day-specific
        visitors=[],
        photos=[],
        activities_completed=[],
        delays=[],
        notes=None,
        location_lat=prev.location_lat,
        location_lon=prev.location_lon,
        created_by=created_by,
    )
    db.add(log)
    await db.flush()
    await db.refresh(log)
    return log


# ---------------------------------------------------------------------------
# List / detail / weekly summary
# ---------------------------------------------------------------------------


async def get_daily_log_detail(
    db: AsyncSession,
    log_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict:
    """Return a single daily log as a dict."""
    log = await _get_log(db, log_id, project_id)
    return _log_to_dict(log)


async def list_daily_logs(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    """List daily logs with optional filters."""
    query = (
        select(DailyLog).where(DailyLog.project_id == project_id).order_by(DailyLog.log_date.desc())
    )
    if status:
        query = query.where(DailyLog.status == status)
    if date_from:
        query = query.where(DailyLog.log_date >= date_from)
    if date_to:
        query = query.where(DailyLog.log_date <= date_to)

    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
            cursor_obj = await db.get(DailyLog, cursor_uuid)
            if cursor_obj:
                query = query.where(DailyLog.log_date < cursor_obj.log_date)
        except ValueError:
            pass

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return {
        "data": [_log_to_dict(i) for i in items],
        "meta": {"cursor": next_cursor, "has_more": has_more},
    }


async def get_weekly_summary(
    db: AsyncSession,
    project_id: uuid.UUID,
    week_start: date,
) -> dict:
    """Aggregate daily logs for a 7-day window starting at week_start."""
    week_end = week_start + timedelta(days=6)

    result = await db.execute(
        select(DailyLog)
        .where(
            DailyLog.project_id == project_id,
            DailyLog.log_date >= week_start,
            DailyLog.log_date <= week_end,
        )
        .order_by(DailyLog.log_date.asc())
    )
    logs = list(result.scalars().all())

    total_crew = sum(l.crew_count or 0 for l in logs)
    total_hours = sum(l.work_hours or Decimal("0") for l in logs)

    # Aggregate manpower by trade
    manpower: dict[str, dict[str, Any]] = {}
    for log in logs:
        for entry in log.manpower_by_trade or []:
            trade = entry.get("trade", "unknown")
            if trade not in manpower:
                manpower[trade] = {"headcount": 0, "hours": Decimal("0")}
            manpower[trade]["headcount"] += entry.get("headcount", 0)
            manpower[trade]["hours"] += Decimal(str(entry.get("hours", 0)))

    weather_summary = [
        {"date": l.log_date.isoformat(), "conditions": (l.weather or {}).get("conditions", "")}
        for l in logs
    ]

    delay_summary: list[dict] = []
    for log in logs:
        for d in log.delays or []:
            delay_summary.append({**d, "log_date": log.log_date.isoformat()})

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "total_logs": len(logs),
        "total_crew_count": total_crew,
        "total_work_hours": total_hours,
        "manpower_summary": manpower,
        "weather_summary": weather_summary,
        "delay_summary": delay_summary,
    }


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------


def export_daily_logs_csv(logs: list[DailyLog]) -> bytes:
    """Export daily logs to CSV bytes."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Date",
            "Status",
            "Crew Count",
            "Work Hours",
            "Work Narrative",
            "Weather Conditions",
            "Weather Delay Hrs",
            "Safety Topic",
            "Safety Incidents",
            "Delays",
            "Notes",
        ]
    )
    for log in logs:
        conditions = (log.weather or {}).get("conditions", "")
        delay_descs = "; ".join(d.get("description", "") for d in (log.delays or []))
        writer.writerow(
            [
                log.log_date.isoformat(),
                log.status,
                log.crew_count,
                log.work_hours,
                log.work_narrative or "",
                conditions,
                float(log.weather_delay_hours) if log.weather_delay_hours else "",
                log.safety_topic_discussed or "",
                log.safety_incidents or "",
                delay_descs,
                log.notes or "",
            ]
        )
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_log(db: AsyncSession, log_id: uuid.UUID, project_id: uuid.UUID) -> DailyLog:
    result = await db.execute(
        select(DailyLog).where(
            DailyLog.id == log_id,
            DailyLog.project_id == project_id,
        )
    )
    log = result.scalars().first()
    if log is None:
        raise ValueError("Daily log not found")
    return log


def _validate_transition(current: str, target: str) -> None:
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(f"Cannot transition from '{current}' to '{target}'")


def _serialize_list(items: list) -> list[dict]:
    """Convert pydantic models or dicts to plain dicts for JSONB."""
    out = []
    for item in items:
        if hasattr(item, "model_dump"):
            out.append(item.model_dump())
        elif isinstance(item, dict):
            out.append(item)
        else:
            out.append(dict(item))
    return out


def _log_to_dict(log: DailyLog) -> dict:
    return {
        "id": str(log.id),
        "project_id": str(log.project_id),
        "log_date": log.log_date.isoformat(),
        "status": log.status,
        "weather": log.weather,
        "crew_count": log.crew_count,
        "work_hours": log.work_hours,
        "work_narrative": log.work_narrative,
        "manpower_by_trade": log.manpower_by_trade,
        "equipment_entries": log.equipment_entries,
        "deliveries": log.deliveries,
        "visitors": log.visitors,
        "photos": log.photos,
        "activities_completed": log.activities_completed,
        "delays": log.delays,
        "notes": log.notes,
        "location_lat": float(log.location_lat) if log.location_lat else None,
        "location_lon": float(log.location_lon) if log.location_lon else None,
        "safety_incidents": log.safety_incidents,
        "safety_topic_discussed": log.safety_topic_discussed,
        "weather_delay_hours": float(log.weather_delay_hours) if log.weather_delay_hours else None,
        "approved_by": str(log.approved_by) if log.approved_by else None,
        "approved_at": log.approved_at.isoformat() if log.approved_at else None,
        "submitted_at": log.submitted_at.isoformat() if log.submitted_at else None,
        "created_by": str(log.created_by) if log.created_by else None,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "updated_at": log.updated_at.isoformat() if log.updated_at else None,
    }
