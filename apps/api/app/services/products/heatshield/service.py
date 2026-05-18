"""HeatShield core service — real-time heat illness prevention and OSHA compliance.

Provides monitoring configuration, threshold calculation, WBGT approximation,
worker acclimatization tracking, break scheduling/compliance, incident reporting,
and HIIPP (Heat Illness Injury Prevention Plan) generation.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.heat_compliance import (
    HeatIncidentReport,
    HeatMonitoringConfig,
    HeatPlan,
    JobsiteHeatMonitoring,
    RestBreakLog,
    WorkerAcclimatization,
)
from app.schemas.heat_compliance import BreakScheduleItem
from app.services.reliability.llm_gateway import get_llm_gateway

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THRESHOLD_INITIAL_F: float = 80.0
THRESHOLD_HIGH_HEAT_F: float = 90.0
ACCLIMATIZATION_DAYS: int = 14
ABSENCE_RESET_DAYS: int = 7

# Minimum break duration (minutes) before flagging as an exception
_MIN_BREAK_DURATION_NORMAL = 10

# Tolerance (minutes) for matching logged breaks to scheduled breaks
_SCHEDULE_TOLERANCE_MINUTES = 15


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def calculate_threshold(
    temp_f: float,
    config: HeatMonitoringConfig | None = None,
) -> str:
    """Return the heat threshold level for a given temperature.

    Uses custom thresholds from *config* when available, otherwise falls
    back to the module-level defaults.

    Returns one of ``'normal'``, ``'initial'``, or ``'high_heat'``.
    """
    initial = float(config.threshold_initial_f) if config else THRESHOLD_INITIAL_F
    high = float(config.threshold_high_heat_f) if config else THRESHOLD_HIGH_HEAT_F

    if temp_f >= high:
        return "high_heat"
    if temp_f >= initial:
        return "initial"
    return "normal"


def calculate_wbgt(
    temp_f: float,
    humidity_pct: float,
    wind_speed_mph: float | None = None,
) -> float:
    """Approximate Wet Bulb Globe Temperature (WBGT) from temp and humidity.

    This is a simplified Liljegren approximation suitable for outdoor
    construction sites.  A full WBGT calculation requires black globe and
    natural wet-bulb measurements; this heuristic is conservative enough
    for screening purposes.

    Returns WBGT in degrees Fahrenheit.
    """
    # Convert to Celsius for the approximation
    temp_c = (temp_f - 32.0) * 5.0 / 9.0
    rh = max(0.0, min(humidity_pct, 100.0))

    # Stull (2011) wet-bulb approximation
    tw_c = (
        temp_c * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        + math.atan(temp_c + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * rh**1.5 * math.atan(0.023101 * rh)
        - 4.686035
    )

    # Approximate globe temp ≈ dry-bulb + solar offset (assume partial sun)
    tg_c = temp_c + 3.0  # conservative +3 C for partial sun

    # Wind correction: higher wind reduces globe temp slightly
    if wind_speed_mph is not None and wind_speed_mph > 5.0:
        wind_correction = min((wind_speed_mph - 5.0) * 0.15, 3.0)
        tg_c -= wind_correction

    # WBGT outdoor = 0.7 * Tw + 0.2 * Tg + 0.1 * Td
    wbgt_c = 0.7 * tw_c + 0.2 * tg_c + 0.1 * temp_c

    # Convert back to Fahrenheit
    return round(wbgt_c * 9.0 / 5.0 + 32.0, 1)


def generate_break_schedule(
    crew_start_time: str,
    threshold_level: str,
) -> list[BreakScheduleItem]:
    """Generate a daily break schedule based on threshold level.

    * **normal**: every 4 hours, 10-minute breaks
    * **initial**: every 2 hours, 15-minute breaks
    * **high_heat**: every 1 hour, 15-minute breaks

    Returns a list of :class:`BreakScheduleItem` instances.
    """
    try:
        parts = crew_start_time.split(":")
        start_hour = int(parts[0])
        start_minute = int(parts[1])
    except (ValueError, IndexError):
        start_hour, start_minute = 7, 0

    if threshold_level == "high_heat":
        interval_hours = 1
        duration = 15
    elif threshold_level == "initial":
        interval_hours = 2
        duration = 15
    else:
        interval_hours = 4
        duration = 10

    work_hours = 10  # assume a 10-hour workday for schedule generation
    schedule: list[BreakScheduleItem] = []
    total_minutes = start_hour * 60 + start_minute

    # First break is after the first interval
    total_minutes += interval_hours * 60

    end_minutes = (start_hour + work_hours) * 60 + start_minute
    while total_minutes < end_minutes:
        hour = total_minutes // 60
        minute = total_minutes % 60
        schedule.append(
            BreakScheduleItem(
                scheduled_time=f"{hour:02d}:{minute:02d}",
                threshold_level=threshold_level,
                duration_minutes=duration,
                status="scheduled",
            )
        )
        total_minutes += interval_hours * 60

    return schedule


def check_acclimatization_reset(
    worker: WorkerAcclimatization,
    today: date,
) -> bool:
    """Check if a worker's acclimatization should be reset due to absence.

    Returns ``True`` if the worker was reset, ``False`` otherwise.
    """
    if worker.last_work_date is None:
        return False
    absence_days = (today - worker.last_work_date).days
    if absence_days >= ABSENCE_RESET_DAYS and worker.status != "reset":
        worker.acclimatization_day = 1
        worker.status = "reset"
        worker.start_date = today
        return True
    return False


def advance_acclimatization(worker: WorkerAcclimatization) -> None:
    """Advance a worker's acclimatization by one day (max 14)."""
    if worker.acclimatization_day < ACCLIMATIZATION_DAYS:
        worker.acclimatization_day += 1
    if worker.acclimatization_day >= ACCLIMATIZATION_DAYS:
        worker.status = "acclimatized"


# ---------------------------------------------------------------------------
# DB-backed service functions
# ---------------------------------------------------------------------------


async def configure_monitoring(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    config_data: dict[str, Any],
) -> HeatMonitoringConfig:
    """Upsert the heat monitoring configuration for a project."""
    result = await db.execute(
        select(HeatMonitoringConfig).where(HeatMonitoringConfig.project_id == project_id)
    )
    config = result.scalar_one_or_none()

    if config is None:
        config = HeatMonitoringConfig(
            project_id=project_id,
            organization_id=org_id,
        )
        db.add(config)

    for key, value in config_data.items():
        if value is not None and hasattr(config, key):
            setattr(config, key, value)

    await db.flush()
    await db.refresh(config)
    return config


async def get_current_conditions(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Return the latest heat reading for a project, or None if no data."""
    result = await db.execute(
        select(JobsiteHeatMonitoring)
        .where(JobsiteHeatMonitoring.project_id == project_id)
        .order_by(JobsiteHeatMonitoring.timestamp.desc())
        .limit(1)
    )
    reading = result.scalar_one_or_none()
    if reading is None:
        return None

    return {
        "id": str(reading.id),
        "timestamp": reading.timestamp.isoformat() if reading.timestamp else None,
        "temperature_f": float(reading.temperature_f) if reading.temperature_f else None,
        "heat_index_f": float(reading.heat_index_f) if reading.heat_index_f else None,
        "wbgt_f": float(reading.wbgt_f) if reading.wbgt_f else None,
        "humidity_pct": float(reading.humidity_pct) if reading.humidity_pct else None,
        "wind_speed_mph": (float(reading.wind_speed_mph) if reading.wind_speed_mph else None),
        "data_source": reading.data_source,
        "threshold_level": reading.threshold_level,
        "protocol_activated": reading.protocol_activated,
    }


async def record_manual_reading(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    data: dict[str, Any],
) -> JobsiteHeatMonitoring:
    """Create a heat reading from manual field input."""
    temp_f = float(data["temperature_f"])
    humidity = data.get("humidity_pct")
    wind = data.get("wind_speed_mph")

    # Look up config for custom thresholds
    cfg_result = await db.execute(
        select(HeatMonitoringConfig).where(HeatMonitoringConfig.project_id == project_id)
    )
    config = cfg_result.scalar_one_or_none()

    level = calculate_threshold(temp_f, config)

    wbgt = None
    if humidity is not None:
        wbgt = calculate_wbgt(temp_f, float(humidity), float(wind) if wind else None)

    reading = JobsiteHeatMonitoring(
        project_id=project_id,
        organization_id=org_id,
        temperature_f=Decimal(str(temp_f)),
        humidity_pct=Decimal(str(humidity)) if humidity is not None else None,
        wind_speed_mph=Decimal(str(wind)) if wind is not None else None,
        wbgt_f=Decimal(str(wbgt)) if wbgt is not None else None,
        data_source="manual",
        threshold_level=level,
        protocol_activated=level != "normal",
    )
    db.add(reading)
    await db.flush()
    await db.refresh(reading)
    return reading


async def add_worker(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    data: dict[str, Any],
) -> WorkerAcclimatization:
    """Create a new worker acclimatization record."""
    from datetime import date as date_type

    worker = WorkerAcclimatization(
        project_id=project_id,
        organization_id=org_id,
        worker_id=data["worker_id"],
        worker_name=data["worker_name"],
        start_date=date_type.today(),
        last_work_date=date_type.today(),
        supervisor_id=data.get("supervisor_id"),
    )
    db.add(worker)
    await db.flush()
    await db.refresh(worker)
    return worker


async def update_worker(
    db: AsyncSession,
    project_id: uuid.UUID,
    worker_id: str,
    data: dict[str, Any],
) -> WorkerAcclimatization | None:
    """Update an existing worker acclimatization record."""
    result = await db.execute(
        select(WorkerAcclimatization).where(
            WorkerAcclimatization.project_id == project_id,
            WorkerAcclimatization.worker_id == worker_id,
        )
    )
    worker = result.scalar_one_or_none()
    if worker is None:
        return None

    for key, value in data.items():
        if value is not None and hasattr(worker, key):
            setattr(worker, key, value)

    await db.flush()
    await db.refresh(worker)
    return worker


async def list_workers(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> list[WorkerAcclimatization]:
    """List all workers, auto-checking for 7-day absence resets."""
    result = await db.execute(
        select(WorkerAcclimatization).where(WorkerAcclimatization.project_id == project_id)
    )
    workers = list(result.scalars().all())

    today = date.today()
    for w in workers:
        check_acclimatization_reset(w, today)

    await db.flush()
    return workers


async def log_break(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    data: dict[str, Any],
    user_id: uuid.UUID | None = None,
) -> RestBreakLog:
    """Log a rest/water break and detect exceptions."""
    exception_flag = False
    exception_reason = data.get("exception_reason")

    duration = data.get("duration_minutes", 0)
    location_ok = data.get("location_compliant", True)

    # Flag short breaks
    if duration < _MIN_BREAK_DURATION_NORMAL:
        exception_flag = True
        if not exception_reason:
            exception_reason = f"Break duration ({duration} min) below minimum"

    # Flag non-compliant shade/shelter location
    if not location_ok:
        exception_flag = True
        if not exception_reason:
            exception_reason = "Break location not compliant (shade/shelter required)"

    break_log = RestBreakLog(
        project_id=project_id,
        organization_id=org_id,
        break_date=data["break_date"],
        scheduled_time=data.get("scheduled_time"),
        actual_start=data["actual_start"],
        actual_end=data["actual_end"],
        duration_minutes=duration,
        location_compliant=location_ok,
        logged_by=user_id,
        workers_present=data.get("workers_present", 0),
        gps_lat=Decimal(str(data["gps_lat"])) if data.get("gps_lat") else None,
        gps_lng=Decimal(str(data["gps_lng"])) if data.get("gps_lng") else None,
        exception_flag=exception_flag,
        exception_reason=exception_reason,
    )
    db.add(break_log)
    await db.flush()
    await db.refresh(break_log)
    return break_log


async def list_breaks(
    db: AsyncSession,
    project_id: uuid.UUID,
    break_date: date | None = None,
) -> list[RestBreakLog]:
    """List break logs, optionally filtered by date."""
    stmt = select(RestBreakLog).where(RestBreakLog.project_id == project_id)
    if break_date is not None:
        stmt = stmt.where(RestBreakLog.break_date == break_date)
    stmt = stmt.order_by(RestBreakLog.created_at.desc())

    result = await db.execute(stmt)
    return list(result.scalars().all())


def _time_to_minutes(time_str: str) -> int:
    """Convert HH:MM string to minutes since midnight."""
    try:
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return 0


async def check_break_compliance(
    db: AsyncSession,
    project_id: uuid.UUID,
    break_date: date,
    crew_start_time: str,
    threshold_level: str,
) -> list[BreakScheduleItem]:
    """Match logged breaks against the expected schedule.

    Returns the schedule with each item's ``status`` set to
    ``'logged'`` or ``'missed'``.
    """
    schedule = generate_break_schedule(crew_start_time, threshold_level)
    logged = await list_breaks(db, project_id, break_date)

    logged_minutes = [_time_to_minutes(b.actual_start) for b in logged if b.actual_start]

    for item in schedule:
        scheduled_min = _time_to_minutes(item.scheduled_time)
        matched = any(
            abs(lm - scheduled_min) <= _SCHEDULE_TOLERANCE_MINUTES for lm in logged_minutes
        )
        item.status = "logged" if matched else "missed"

    return schedule


async def create_incident(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    data: dict[str, Any],
) -> HeatIncidentReport:
    """Create a heat incident report."""
    incident = HeatIncidentReport(
        project_id=project_id,
        organization_id=org_id,
        worker_id=data.get("worker_id"),
        worker_name=data.get("worker_name"),
        incident_date=data["incident_date"],
        incident_time=data.get("incident_time"),
        symptoms=data.get("symptoms", []),
        heat_index_at_incident=(
            Decimal(str(data["heat_index_at_incident"]))
            if data.get("heat_index_at_incident") is not None
            else None
        ),
        acclimatization_day=data.get("acclimatization_day"),
        actions_taken=data.get("actions_taken"),
        medical_response=data.get("medical_response", "none"),
        osha_recordable=data.get("osha_recordable", False),
        photos=data.get("photos", []),
    )
    db.add(incident)
    await db.flush()
    await db.refresh(incident)
    return incident


# ---------------------------------------------------------------------------
# HIIPP generation
# ---------------------------------------------------------------------------

_HIIPP_TEMPLATE: dict[str, Any] = {
    "title": "Heat Illness Injury Prevention Plan (HIIPP)",
    "sections": {
        "purpose": (
            "This plan establishes procedures to prevent heat-related illness "
            "on the jobsite in compliance with OSHA's heat illness prevention "
            "recommendations and applicable state regulations (Cal/OSHA "
            "Title 8, Section 3395 where applicable)."
        ),
        "responsibilities": {
            "project_manager": [
                "Ensure HIIPP is communicated to all workers",
                "Designate a competent person for heat illness monitoring",
                "Provide adequate resources (water, shade, rest areas)",
            ],
            "supervisor": [
                "Monitor weather conditions and adjust work schedules",
                "Enforce mandatory rest/water breaks",
                "Track worker acclimatization status",
                "Recognize symptoms of heat illness",
            ],
            "workers": [
                "Drink water frequently (minimum 1 quart/hour)",
                "Report heat illness symptoms immediately",
                "Take scheduled rest breaks",
                "Use buddy system during high-heat periods",
            ],
        },
        "water_provision": (
            "Potable drinking water shall be available at all times in sufficient "
            "quantity (minimum 1 quart per employee per hour for the entire shift). "
            "Water stations shall be located in shaded areas and replenished "
            "before supply is depleted."
        ),
        "rest_areas": (
            "Shaded rest areas shall be provided and maintained throughout the "
            "workday. Rest areas must be open to air or have ventilation/cooling, "
            "be large enough to accommodate the number of workers on break, and be "
            "close enough that workers can access them without significant effort."
        ),
        "acclimatization_procedures": {
            "new_workers": (
                "Workers new to outdoor heat exposure shall follow a 14-day "
                "acclimatization schedule: Day 1-2: 20% workload, Day 3-4: 40%, "
                "Day 5-6: 60%, Day 7-8: 80%, Day 9-14: full workload with "
                "continued monitoring."
            ),
            "returning_workers": (
                "Workers absent for 7 or more consecutive days shall restart "
                "the acclimatization schedule from Day 1."
            ),
        },
        "emergency_response": {
            "heat_cramps": [
                "Move worker to shaded rest area",
                "Provide water and electrolyte drinks",
                "Allow rest until symptoms subside",
            ],
            "heat_exhaustion": [
                "Move worker to cool/shaded area immediately",
                "Remove excess clothing, apply cool water",
                "Provide water if conscious",
                "Monitor for 20 minutes; call EMS if no improvement",
            ],
            "heat_stroke": [
                "Call 911 immediately — medical emergency",
                "Move worker to coolest available area",
                "Begin active cooling (ice packs, cold water immersion)",
                "Do NOT give fluids if unconscious",
                "Monitor breathing and pulse until EMS arrives",
            ],
        },
        "threshold_procedures": {
            "normal": "Standard work/rest schedule. Ensure water access.",
            "initial": (
                "Temperature >= 80 F: Implement high-heat procedures. "
                "Rest breaks every 2 hours for 15 minutes. "
                "Pre-shift meetings to discuss heat safety. "
                "Increased water consumption monitoring."
            ),
            "high_heat": (
                "Temperature >= 90 F: Mandatory buddy system. "
                "Rest breaks every hour for 15 minutes. "
                "Observe new employees closely for first 14 days. "
                "Consider schedule adjustments (early start, extended midday break)."
            ),
        },
    },
}


async def generate_hiipp(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
) -> HeatPlan:
    """Generate a Heat Illness Injury Prevention Plan.

    Tries LLM-based generation first; falls back to a template.
    """
    plan_content: dict[str, Any]

    try:
        gateway = await get_llm_gateway()
        prompt = (
            "Generate a comprehensive Heat Illness Injury Prevention Plan (HIIPP) "
            "for a construction jobsite. Include sections for: purpose, "
            "responsibilities (PM/supervisor/workers), water provision, rest areas, "
            "acclimatization procedures (14-day schedule, 7-day absence reset), "
            "emergency response (heat cramps/exhaustion/stroke), and threshold "
            "procedures (normal/initial >=80F/high_heat >=90F). "
            "Return the plan as structured JSON."
        )
        response = await gateway.complete(
            messages=[{"role": "user", "content": prompt}],
            agent_name="heatshield_hiipp",
            temperature=0.3,
        )
        # Attempt to parse structured JSON from LLM response
        import json

        plan_content = json.loads(response.get("content", "{}"))
        logger.info("HIIPP generated via LLM for project %s", project_id)
    except Exception:
        logger.info(
            "LLM-based HIIPP generation failed for project %s; using template",
            project_id,
        )
        plan_content = _HIIPP_TEMPLATE

    # Determine version by counting existing plans
    version_result = await db.execute(
        select(func.count(HeatPlan.id)).where(HeatPlan.project_id == project_id)
    )
    existing_count = version_result.scalar() or 0

    plan = HeatPlan(
        project_id=project_id,
        organization_id=org_id,
        version=existing_count + 1,
        plan_content=plan_content,
    )
    db.add(plan)
    await db.flush()
    await db.refresh(plan)
    return plan


# ---------------------------------------------------------------------------
# Dashboard aggregation
# ---------------------------------------------------------------------------


async def get_dashboard(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict[str, Any]:
    """Aggregate heat compliance data for the project dashboard."""
    conditions = await get_current_conditions(db, project_id)
    threshold_level = conditions["threshold_level"] if conditions else "normal"

    # --- Worker counts ---
    workers = await list_workers(db, project_id)
    worker_counts = {
        "total": len(workers),
        "acclimatizing": sum(1 for w in workers if w.status == "acclimatizing"),
        "acclimatized": sum(1 for w in workers if w.status == "acclimatized"),
        "reset": sum(1 for w in workers if w.status == "reset"),
    }

    # --- Today's break schedule with compliance ---
    today = date.today()
    cfg_result = await db.execute(
        select(HeatMonitoringConfig).where(HeatMonitoringConfig.project_id == project_id)
    )
    config = cfg_result.scalar_one_or_none()
    crew_start = config.crew_start_time if config else "07:00"

    today_breaks = await check_break_compliance(db, project_id, today, crew_start, threshold_level)

    # --- Break compliance rate ---
    # The break schedule is generated synthetically based on threshold_level,
    # so it's non-empty even when there are no workers on site. Treating
    # "no workers, no logs" as 0% compliance is misleading — there is no
    # one obligated to take the break. Compliance is only meaningful when
    # there are workers; otherwise return 1.0 (nothing to comply with).
    total_scheduled = len(today_breaks)
    logged_count = sum(1 for b in today_breaks if b.status == "logged")
    if worker_counts["total"] == 0 or total_scheduled == 0:
        compliance_rate = 1.0
    else:
        compliance_rate = logged_count / total_scheduled

    # --- Recent incidents (last 30 days) ---
    cutoff = today - timedelta(days=30)
    incident_result = await db.execute(
        select(HeatIncidentReport)
        .where(
            HeatIncidentReport.project_id == project_id,
            HeatIncidentReport.incident_date >= cutoff,
        )
        .order_by(HeatIncidentReport.incident_date.desc())
        .limit(10)
    )
    recent_incidents = list(incident_result.scalars().all())

    return {
        "current_conditions": conditions,
        "threshold_level": threshold_level,
        "workers": worker_counts,
        "today_breaks": today_breaks,
        "recent_incidents": recent_incidents,
        "break_compliance_rate": round(compliance_rate, 4),
    }
