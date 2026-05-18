"""Automated daily report generator.

Aggregates data from multiple sources (weather, safety, workforce, equipment,
deliveries, schedule, quality) and generates a narrative report via LLM in
the voice of a construction superintendent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

# Valid report statuses
VALID_STATUSES = {"draft", "reviewed", "approved"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DailyDataAggregate:
    """Aggregated data from all sources for a single report date."""

    project_id: str
    report_date: date
    weather: dict = field(default_factory=dict)
    safety_alerts: list[dict] = field(default_factory=list)
    workforce: dict = field(default_factory=dict)
    equipment: list[dict] = field(default_factory=list)
    deliveries: list[dict] = field(default_factory=list)
    schedule_activities: list[dict] = field(default_factory=list)
    quality: dict = field(default_factory=dict)
    daily_log: dict | None = None


# ---------------------------------------------------------------------------
# Data aggregation
# ---------------------------------------------------------------------------


async def aggregate_daily_data(
    db: AsyncSession,
    project_id: uuid.UUID,
    report_date: date,
) -> DailyDataAggregate:
    """Fetch data from ALL sources for the given date.

    Each source is wrapped in a savepoint so a SQL error in one fetch
    (e.g. missing table on this deployment) doesn't poison the transaction
    and abort all the others.
    """
    aggregate = DailyDataAggregate(
        project_id=str(project_id),
        report_date=report_date,
    )

    # Real async sessions support savepoints; AsyncMock in unit tests does
    # not. Detect once and pick the right wrapper.
    use_savepoints = hasattr(db, "begin_nested") and not isinstance(db, type(None))
    try:
        # Probe: is begin_nested a real async-context-manager factory?
        probe = db.begin_nested()
        if not hasattr(probe, "__aenter__"):
            use_savepoints = False
        # Don't actually enter; we'll call begin_nested again per fetch.
        if hasattr(probe, "close"):
            probe.close()
    except Exception:
        use_savepoints = False

    async def _safe(label: str, coro_factory) -> object:
        """Run *coro_factory()* under a savepoint; on failure rollback the
        savepoint and return whatever the factory's default is.

        Skips the savepoint wrapper when the session doesn't support it
        (e.g., AsyncMock in unit tests) and just runs the coroutine
        directly with try/except.
        """
        if use_savepoints:
            try:
                async with db.begin_nested():
                    return await coro_factory()
            except Exception as exc:
                logger.warning("%s fetch failed (savepoint rolled back): %s", label, exc)
                return None
        # Mocked session path
        try:
            return await coro_factory()
        except Exception as exc:
            logger.warning("%s fetch failed: %s", label, exc)
            return None

    aggregate.daily_log = await _safe(
        "daily_log", lambda: _fetch_daily_log(db, project_id, report_date)
    )
    aggregate.weather = (
        await _safe(
            "weather",
            lambda: _fetch_weather(db, project_id, report_date, aggregate.daily_log),
        )
        or {}
    )
    aggregate.safety_alerts = (
        await _safe("safety_alerts", lambda: _fetch_safety_alerts(db, project_id, report_date))
        or []
    )
    aggregate.workforce = (
        await _safe(
            "workforce",
            lambda: _fetch_workforce(db, project_id, report_date, aggregate.daily_log),
        )
        or {}
    )
    aggregate.equipment = (
        await _safe("equipment", lambda: _fetch_equipment(db, project_id, report_date)) or []
    )
    aggregate.deliveries = (
        await _safe(
            "deliveries",
            lambda: _fetch_deliveries(db, project_id, report_date, aggregate.daily_log),
        )
        or []
    )
    aggregate.schedule_activities = (
        await _safe(
            "schedule_activities",
            lambda: _fetch_schedule_activities(db, project_id, report_date),
        )
        or []
    )
    aggregate.quality = (
        await _safe("quality", lambda: _fetch_quality(db, project_id, report_date)) or {}
    )

    return aggregate


# ---------------------------------------------------------------------------
# Narrative generation
# ---------------------------------------------------------------------------


async def generate_daily_narrative(aggregate: DailyDataAggregate) -> str:
    """Generate a Markdown narrative report from aggregated data via LLM.

    Uses construction superintendent voice.  Falls back to a template-based
    narrative if the LLM is unavailable.
    """
    # Build prompt sections
    weather_text = _format_weather(aggregate.weather)
    workforce_text = _format_workforce(aggregate.workforce)
    equipment_text = _format_equipment(aggregate.equipment)
    deliveries_text = _format_deliveries(aggregate.deliveries)
    activities_text = _format_activities(aggregate.schedule_activities)
    safety_text = _format_safety(aggregate.safety_alerts, aggregate.daily_log)
    quality_text = _format_quality(aggregate.quality)

    prompt_messages = [
        {
            "role": "system",
            "content": (
                "You are an experienced construction superintendent writing a daily "
                "report for the project record. Write in professional first-person "
                "construction industry voice. Be factual, specific, and concise. "
                "Use Markdown formatting. Include all data provided; do not invent "
                "information not in the data. If a section has no data, note "
                "'No activity recorded' for that section."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Generate a construction daily report for "
                f"<user_data>{sanitize_for_prompt(aggregate.project_id, max_length=100)}</user_data> "
                f"on {aggregate.report_date.isoformat()}.\n\n"
                f"## Weather Conditions\n{weather_text}\n\n"
                f"## Workforce on Site\n{workforce_text}\n\n"
                f"## Equipment Active\n{equipment_text}\n\n"
                f"## Deliveries Received\n{deliveries_text}\n\n"
                f"## Activities Worked On\n{activities_text}\n\n"
                f"## Safety\n{safety_text}\n\n"
                f"## Quality Observations\n{quality_text}\n\n"
                f"Format the report with these sections:\n"
                f"1. **Daily Report Header** (project, date, weather summary)\n"
                f"2. **Weather Conditions** (temperature, precipitation, impact)\n"
                f"3. **Workforce** (total headcount, breakdown by trade)\n"
                f"4. **Equipment** (equipment on site, hours used)\n"
                f"5. **Deliveries** (materials received)\n"
                f"6. **Work Performed** (activities in progress, percent complete)\n"
                f"7. **Safety** (incidents, alerts, toolbox topic)\n"
                f"8. **Quality** (inspections, defects noted)\n"
                f"9. **Issues & Concerns** (delays, problems, needs)\n"
            ),
        },
    ]

    try:
        from app.services.reliability.llm_gateway import get_llm_gateway

        gateway = await get_llm_gateway()
        result = await gateway.complete(
            messages=prompt_messages,
            agent_name="daily_report_generator",
            task_class="summarization",
            max_tokens=2048,
        )
        narrative = result.get("content", "")
        if narrative:
            return narrative
    except Exception as exc:
        logger.warning("LLM narrative generation failed: %s; using template", exc)

    # Template fallback
    return _template_narrative(aggregate)


# ---------------------------------------------------------------------------
# Report lifecycle
# ---------------------------------------------------------------------------


async def create_daily_report(
    db: AsyncSession,
    project_id: uuid.UUID,
    report_date: date,
    generated_by: uuid.UUID | None = None,
    pre_built_aggregate: DailyDataAggregate | None = None,
) -> Any:
    """Aggregate data, generate narrative, and save to DB.

    If *pre_built_aggregate* is provided, skip the aggregation step and
    use the supplied data directly.  This allows callers (e.g. the ambient
    intelligence bridge) to inject IoT-sourced data into the report instead
    of relying solely on the standard DB queries.

    Returns the created GeneratedDailyReport record.
    """
    from app.models.generated_report import GeneratedDailyReport

    if pre_built_aggregate is not None:
        aggregate = pre_built_aggregate
    else:
        aggregate = await aggregate_daily_data(db, project_id, report_date)
    narrative = await generate_daily_narrative(aggregate)

    # Serialize aggregate for JSONB storage
    agg_json = _aggregate_to_json(aggregate)

    report = GeneratedDailyReport(
        project_id=project_id,
        report_date=report_date,
        aggregated_data=agg_json,
        narrative_markdown=narrative,
        status="draft",
        generated_by=generated_by,
    )
    db.add(report)
    await db.flush()
    await db.refresh(report)

    logger.info(
        "Generated daily report %s for project %s date %s",
        report.id,
        project_id,
        report_date,
    )
    return report


async def review_and_approve_report(
    db: AsyncSession,
    report_id: uuid.UUID,
    approved_by: uuid.UUID,
    edits: str | None = None,
) -> Any:
    """Approve a report (optionally with narrative edits).

    If *edits* is provided, the narrative is replaced with the edited version.
    Sets status to 'approved'.
    """
    from app.models.generated_report import GeneratedDailyReport

    result = await db.execute(
        select(GeneratedDailyReport).where(GeneratedDailyReport.id == report_id)
    )
    report = result.scalars().first()
    if report is None:
        raise ValueError("Report not found")

    if edits is not None:
        report.narrative_markdown = edits
    report.status = "approved"
    report.reviewed_by = approved_by
    report.approved_at = datetime.now(UTC)

    await db.flush()
    await db.refresh(report)
    return report


async def save_report_as_daily_log(
    db: AsyncSession,
    report_id: uuid.UUID,
) -> Any:
    """Convert an approved report into an official DailyLog record.

    The report must be in 'approved' status.  Creates a new DailyLog
    record and links it back via the report's daily_log_id.
    """
    from app.models.generated_report import GeneratedDailyReport
    from app.models.productivity import DailyLog

    result = await db.execute(
        select(GeneratedDailyReport).where(GeneratedDailyReport.id == report_id)
    )
    report = result.scalars().first()
    if report is None:
        raise ValueError("Report not found")

    if report.status != "approved":
        raise ValueError("Only approved reports can be saved as daily logs")

    if report.daily_log_id is not None:
        raise ValueError("Report already has an associated daily log")

    agg = report.aggregated_data or {}

    # Build DailyLog from aggregated data
    daily_log = DailyLog(
        project_id=report.project_id,
        log_date=report.report_date,
        status="submitted",
        weather=agg.get("weather", {}),
        crew_count=agg.get("workforce", {}).get("total_headcount", 0),
        work_hours=Decimal(str(agg.get("workforce", {}).get("total_hours", 0))),
        work_narrative=report.narrative_markdown,
        manpower_by_trade=agg.get("workforce", {}).get("by_trade", []),
        equipment_entries=agg.get("equipment", []),
        deliveries=agg.get("deliveries", []),
        activities_completed=[
            {"activity": a.get("name", ""), "pct_complete": a.get("pct_complete", 0)}
            for a in agg.get("schedule_activities", [])
        ],
        delays=[],
        notes=f"Auto-generated from report {report.id}",
        safety_incidents=agg.get("safety_summary", ""),
        created_by=report.generated_by,
    )
    db.add(daily_log)
    await db.flush()
    await db.refresh(daily_log)

    # Link report to daily log
    report.daily_log_id = daily_log.id
    await db.flush()
    await db.refresh(report)

    logger.info("Saved report %s as daily log %s", report_id, daily_log.id)
    return daily_log


# ---------------------------------------------------------------------------
# Data source fetchers (each wrapped in try/except)
# ---------------------------------------------------------------------------


async def _fetch_daily_log(
    db: AsyncSession,
    project_id: uuid.UUID,
    report_date: date,
) -> dict | None:
    """Fetch the daily log for this date, if any."""
    try:
        from app.models.productivity import DailyLog

        result = await db.execute(
            select(DailyLog)
            .where(
                DailyLog.project_id == project_id,
                DailyLog.log_date == report_date,
            )
            .limit(1)
        )
        log = result.scalars().first()
        if log is None:
            return None
        return {
            "crew_count": log.crew_count,
            "work_hours": float(log.work_hours) if log.work_hours else 0,
            "work_narrative": log.work_narrative,
            "weather": log.weather,
            "manpower_by_trade": log.manpower_by_trade or [],
            "equipment_entries": log.equipment_entries or [],
            "deliveries": log.deliveries or [],
            "activities_completed": log.activities_completed or [],
            "delays": log.delays or [],
            "safety_incidents": log.safety_incidents,
            "safety_topic_discussed": log.safety_topic_discussed,
            "weather_delay_hours": (
                float(log.weather_delay_hours) if log.weather_delay_hours else 0
            ),
        }
    except Exception as exc:
        logger.warning("Failed to fetch daily log: %s", exc)
        return None


async def _fetch_weather(
    db: AsyncSession,
    project_id: uuid.UUID,
    report_date: date,
    daily_log: dict | None,
) -> dict:
    """Get weather data from daily log or weather service."""
    try:
        # First try daily log weather field
        if daily_log and daily_log.get("weather"):
            return daily_log["weather"]

        # Try to fetch from weather service using project location
        from app.models.project import Project

        result = await db.execute(select(Project).where(Project.id == project_id).limit(1))
        project = result.scalars().first()
        if project and hasattr(project, "latitude") and hasattr(project, "longitude"):
            lat = getattr(project, "latitude", None)
            lon = getattr(project, "longitude", None)
            if lat and lon:
                from app.services.scheduling.weather_service import get_weather_forecast

                forecasts = await get_weather_forecast(
                    latitude=float(lat),
                    longitude=float(lon),
                    start_date=report_date.isoformat(),
                    end_date=report_date.isoformat(),
                )
                if forecasts:
                    return forecasts[0]
    except Exception as exc:
        logger.warning("Weather fetch failed: %s", exc)

    return {}


async def _fetch_safety_alerts(
    db: AsyncSession,
    project_id: uuid.UUID,
    report_date: date,
) -> list[dict]:
    """Fetch safety alerts created on the report date."""
    try:
        from app.models.safety_incident import SafetyAlert

        result = await db.execute(
            select(SafetyAlert).where(
                SafetyAlert.project_id == project_id,
                func.date(SafetyAlert.created_at) == report_date,
            )
        )
        alerts = list(result.scalars().all())
        return [
            {
                "id": str(a.id),
                "alert_type": a.alert_type,
                "priority": a.priority,
                "description": a.description,
                "confidence": float(a.confidence),
                "is_acknowledged": a.is_acknowledged,
                "is_false_positive": a.is_false_positive,
            }
            for a in alerts
        ]
    except Exception as exc:
        logger.warning("Safety alerts fetch failed: %s", exc)
        return []


async def _fetch_workforce(
    db: AsyncSession,
    project_id: uuid.UUID,
    report_date: date,
    daily_log: dict | None,
) -> dict:
    """Aggregate workforce data from daily log and crew productivity."""
    workforce: dict[str, Any] = {
        "total_headcount": 0,
        "total_hours": 0,
        "by_trade": [],
    }

    try:
        # From daily log
        if daily_log:
            workforce["total_headcount"] = daily_log.get("crew_count", 0)
            workforce["total_hours"] = daily_log.get("work_hours", 0)
            workforce["by_trade"] = daily_log.get("manpower_by_trade", [])

        # Supplement with crew productivity data
        from app.models.productivity import CrewProductivity

        result = await db.execute(
            select(CrewProductivity).where(
                CrewProductivity.project_id == project_id,
                CrewProductivity.work_date == report_date,
            )
        )
        crew_records = list(result.scalars().all())
        if crew_records:
            crew_data = [
                {
                    "trade": c.trade,
                    "crew_size": c.crew_size,
                    "planned_units": float(c.planned_units),
                    "actual_units": float(c.actual_units),
                    "unit_of_measure": c.unit_of_measure,
                    "productivity_rate": (
                        float(c.productivity_rate) if c.productivity_rate else None
                    ),
                }
                for c in crew_records
            ]
            workforce["crew_productivity"] = crew_data

            # If daily log didn't have headcount, sum from crew records
            if not workforce["total_headcount"]:
                workforce["total_headcount"] = sum(c.crew_size for c in crew_records)
    except Exception as exc:
        logger.warning("Workforce fetch failed: %s", exc)

    return workforce


async def _fetch_equipment(
    db: AsyncSession,
    project_id: uuid.UUID,
    report_date: date,
) -> list[dict]:
    """Fetch active equipment records for the project."""
    try:
        from app.models.field_management import Equipment

        result = await db.execute(
            select(Equipment).where(
                Equipment.project_id == project_id,
                Equipment.status.in_(["active", "in_use", "available"]),
            )
        )
        equipment = list(result.scalars().all())
        return [
            {
                "equipment_type": e.equipment_type,
                "make": e.make,
                "model": e.model,
                "status": e.status,
                "location": e.location,
            }
            for e in equipment
        ]
    except Exception as exc:
        logger.warning("Equipment fetch failed: %s", exc)
        return []


async def _fetch_deliveries(
    db: AsyncSession,
    project_id: uuid.UUID,
    report_date: date,
    daily_log: dict | None,
) -> list[dict]:
    """Fetch materials delivered on the report date."""
    deliveries: list[dict] = []

    try:
        # From daily log
        if daily_log and daily_log.get("deliveries"):
            deliveries.extend(daily_log["deliveries"])

        # From material records
        from app.models.field_management import Material

        result = await db.execute(
            select(Material).where(
                Material.project_id == project_id,
                Material.expected_delivery == report_date,
            )
        )
        materials = list(result.scalars().all())
        for m in materials:
            deliveries.append(
                {
                    "description": m.name,
                    "category": m.category,
                    "quantity_ordered": float(m.quantity_ordered),
                    "quantity_received": float(m.quantity_received),
                    "supplier": m.supplier,
                    "status": m.status,
                }
            )
    except Exception as exc:
        logger.warning("Deliveries fetch failed: %s", exc)

    return deliveries


async def _fetch_schedule_activities(
    db: AsyncSession,
    project_id: uuid.UUID,
    report_date: date,
) -> list[dict]:
    """Fetch schedule activities that should be in progress on the report date."""
    try:
        from app.models.scheduling import ScheduleActivity

        result = await db.execute(
            select(ScheduleActivity).where(
                ScheduleActivity.project_id == project_id,
                ScheduleActivity.start_date <= report_date,
                ScheduleActivity.finish_date >= report_date,
            )
        )
        activities = list(result.scalars().all())
        return [
            {
                "id": str(a.id),
                "name": a.name,
                "activity_code": a.activity_code,
                "status": a.status,
                "pct_complete": float(a.pct_complete),
                "is_critical": a.is_critical,
                "start_date": a.start_date.isoformat() if a.start_date else None,
                "finish_date": a.finish_date.isoformat() if a.finish_date else None,
                "total_float": a.total_float,
            }
            for a in activities
        ]
    except Exception as exc:
        logger.warning("Schedule activities fetch failed: %s", exc)
        return []


async def _fetch_quality(
    db: AsyncSession,
    project_id: uuid.UUID,
    report_date: date,
) -> dict:
    """Fetch inspections and defect reports for the date."""
    quality: dict[str, Any] = {
        "inspections": [],
        "defects": [],
    }

    try:
        from app.models.quality import DefectReport, Inspection

        # Inspections
        insp_result = await db.execute(
            select(Inspection).where(
                Inspection.project_id == project_id,
                func.date(Inspection.created_at) == report_date,
            )
        )
        inspections = list(insp_result.scalars().all())
        quality["inspections"] = [
            {
                "id": str(i.id),
                "inspection_type": i.inspection_type,
                "status": i.status,
                "score": float(i.score) if i.score else None,
                "location": i.location,
            }
            for i in inspections
        ]

        # Defect Reports
        defect_result = await db.execute(
            select(DefectReport).where(
                DefectReport.project_id == project_id,
                func.date(DefectReport.created_at) == report_date,
            )
        )
        defects = list(defect_result.scalars().all())
        quality["defects"] = [
            {
                "id": str(d.id),
                "defect_type": d.defect_type,
                "severity": d.severity,
                "description": d.description,
                "location": d.location,
            }
            for d in defects
        ]
    except Exception as exc:
        logger.warning("Quality data fetch failed: %s", exc)

    return quality


# ---------------------------------------------------------------------------
# Formatting helpers (for LLM prompt construction)
# ---------------------------------------------------------------------------


def _format_weather(weather: dict) -> str:
    if not weather:
        return "No weather data available."
    parts = []
    if "temperature_high" in weather or "temperature_max" in weather:
        hi = weather.get("temperature_high") or weather.get("temperature_max", "N/A")
        lo = weather.get("temperature_low") or weather.get("temperature_min", "N/A")
        parts.append(f"Temperature: High {hi}F / Low {lo}F")
    if "precipitation_mm" in weather:
        parts.append(f"Precipitation: {weather['precipitation_mm']}mm")
    if "wind_speed_max" in weather:
        parts.append(f"Wind: {weather['wind_speed_max']} mph max")
    if "humidity" in weather:
        parts.append(f"Humidity: {weather['humidity']}%")
    if "conditions" in weather:
        parts.append(f"Conditions: {weather['conditions']}")
    return "\n".join(parts) if parts else "No weather data available."


def _format_workforce(workforce: dict) -> str:
    if not workforce or not workforce.get("total_headcount"):
        return "No workforce data recorded."
    parts = [
        f"Total headcount: {workforce['total_headcount']}",
        f"Total hours: {workforce.get('total_hours', 'N/A')}",
    ]
    by_trade = workforce.get("by_trade", [])
    if by_trade:
        parts.append("By trade:")
        for entry in by_trade[:15]:
            if isinstance(entry, dict):
                trade = entry.get("trade", "Unknown")
                headcount = entry.get("headcount", 0)
                hours = entry.get("hours", 0)
                parts.append(f"  - {trade}: {headcount} workers, {hours} hours")
    return "\n".join(parts)


def _format_equipment(equipment: list[dict]) -> str:
    if not equipment:
        return "No equipment data recorded."
    parts = [f"Equipment on site: {len(equipment)}"]
    for e in equipment[:10]:
        eq_type = e.get("equipment_type", "Unknown")
        eq_status = e.get("status", "")
        eq_loc = e.get("location", "")
        line = f"  - {eq_type}"
        if eq_status:
            line += f" ({eq_status})"
        if eq_loc:
            line += f" at {eq_loc}"
        parts.append(line)
    return "\n".join(parts)


def _format_deliveries(deliveries: list[dict]) -> str:
    if not deliveries:
        return "No deliveries received today."
    parts = [f"Deliveries: {len(deliveries)}"]
    for d in deliveries[:10]:
        desc = d.get("description", "Unknown material")
        supplier = d.get("supplier", "")
        qty = d.get("quantity_received") or d.get("quantity_ordered", "")
        line = f"  - {desc}"
        if supplier:
            line += f" from {supplier}"
        if qty:
            line += f" (qty: {qty})"
        parts.append(line)
    return "\n".join(parts)


def _format_activities(activities: list[dict]) -> str:
    if not activities:
        return "No schedule activities in progress for this date."
    parts = [f"Activities in progress: {len(activities)}"]
    for a in activities[:15]:
        name = a.get("name", "Unknown")
        pct = a.get("pct_complete", 0)
        critical = " [CRITICAL]" if a.get("is_critical") else ""
        parts.append(f"  - {name}: {pct}% complete{critical}")
    return "\n".join(parts)


def _format_safety(alerts: list[dict], daily_log: dict | None) -> str:
    parts = []
    if alerts:
        parts.append(f"Safety alerts: {len(alerts)}")
        for a in alerts[:5]:
            parts.append(
                f"  - [{a.get('priority', 'info')}] {a.get('alert_type', '')}: "
                f"{a.get('description', '')}"
            )
    else:
        parts.append("No safety alerts today.")

    if daily_log:
        if daily_log.get("safety_incidents"):
            parts.append(f"Incidents: {daily_log['safety_incidents']}")
        if daily_log.get("safety_topic_discussed"):
            parts.append(f"Toolbox talk topic: {daily_log['safety_topic_discussed']}")

    return "\n".join(parts)


def _format_quality(quality: dict) -> str:
    if not quality:
        return "No quality observations."
    parts = []
    inspections = quality.get("inspections", [])
    if inspections:
        parts.append(f"Inspections: {len(inspections)}")
        for i in inspections[:5]:
            score_str = f" (score: {i['score']})" if i.get("score") else ""
            parts.append(
                f"  - {i.get('inspection_type', 'Unknown')} at {i.get('location', 'N/A')}"
                f" — {i.get('status', '')}{score_str}"
            )
    defects = quality.get("defects", [])
    if defects:
        parts.append(f"Defects: {len(defects)}")
        for d in defects[:5]:
            parts.append(
                f"  - [{d.get('severity', '')}] {d.get('defect_type', '')}: "
                f"{d.get('description', '')}"
            )
    if not parts:
        parts.append("No quality observations.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Template fallback narrative
# ---------------------------------------------------------------------------


def _template_narrative(aggregate: DailyDataAggregate) -> str:
    """Generate a simple template-based narrative without LLM."""
    lines = [
        "# Daily Construction Report",
        f"**Date:** {aggregate.report_date.isoformat()}",
        f"**Project:** {aggregate.project_id}",
        "",
        "## Weather Conditions",
        _format_weather(aggregate.weather),
        "",
        "## Workforce",
        _format_workforce(aggregate.workforce),
        "",
        "## Equipment",
        _format_equipment(aggregate.equipment),
        "",
        "## Deliveries",
        _format_deliveries(aggregate.deliveries),
        "",
        "## Work Performed",
        _format_activities(aggregate.schedule_activities),
        "",
        "## Safety",
        _format_safety(aggregate.safety_alerts, aggregate.daily_log),
        "",
        "## Quality",
        _format_quality(aggregate.quality),
        "",
        "---",
        "*Report generated automatically by ConstructAI.*",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _aggregate_to_json(aggregate: DailyDataAggregate) -> dict:
    """Convert aggregate to a JSON-serializable dict for JSONB storage."""
    safety_summary = ""
    if aggregate.safety_alerts:
        safety_summary = "; ".join(a.get("description", "") for a in aggregate.safety_alerts[:5])
    return {
        "weather": aggregate.weather,
        "safety_alerts": aggregate.safety_alerts,
        "safety_summary": safety_summary,
        "workforce": aggregate.workforce,
        "equipment": aggregate.equipment,
        "deliveries": aggregate.deliveries,
        "schedule_activities": aggregate.schedule_activities,
        "quality": aggregate.quality,
    }
