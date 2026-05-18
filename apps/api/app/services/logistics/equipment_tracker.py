"""Equipment tracking, utilization analysis, and predictive maintenance.

Provides fleet-level utilization metrics from equipment usage logs and
maintenance prediction based on operating hours and service history.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Service intervals by equipment type (hours)
# ---------------------------------------------------------------------------

DEFAULT_SERVICE_INTERVALS: dict[str, int] = {
    "excavator": 250,
    "bulldozer": 250,
    "crane": 500,
    "loader": 250,
    "dump_truck": 300,
    "concrete_mixer": 200,
    "compactor": 250,
    "generator": 200,
    "forklift": 250,
    "default": 300,
}

# Expected useful life in years by type
EXPECTED_LIFE_YEARS: dict[str, int] = {
    "excavator": 15,
    "bulldozer": 15,
    "crane": 20,
    "loader": 12,
    "dump_truck": 10,
    "concrete_mixer": 10,
    "compactor": 12,
    "generator": 15,
    "forklift": 10,
    "default": 12,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO datetime string."""
    # Handle both 'YYYY-MM-DDTHH:MM:SS' and 'YYYY-MM-DD HH:MM:SS'
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unable to parse datetime: {value}")


def _hours_between(start: str, end: str) -> float:
    """Calculate hours between two datetime strings."""
    dt_start = _parse_datetime(start)
    dt_end = _parse_datetime(end)
    delta = dt_end - dt_start
    return max(0.0, delta.total_seconds() / 3600.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def calculate_equipment_utilization(
    equipment_logs: list[dict],
) -> dict:
    """Calculate equipment utilization metrics from usage logs.

    Parameters
    ----------
    equipment_logs:
        List of log entries, each with ``equipment_id``, ``equipment_type``,
        ``start_time``, ``end_time``, ``status`` ("active"|"idle"|"maintenance").

    Returns
    -------
    dict with:
        - summary: per-equipment utilization breakdown
        - fleet_utilization: overall fleet utilization percentage
        - underutilized: equipment ids below 60% utilization
        - recommendations: list of actionable insights
    """
    if not equipment_logs:
        return {
            "summary": {},
            "fleet_utilization": 0.0,
            "underutilized": [],
            "recommendations": ["No equipment logs provided."],
        }

    # Accumulate hours by equipment and status
    def _new_hours_bucket() -> dict[str, float]:
        return {"active": 0.0, "idle": 0.0, "maintenance": 0.0}

    hours_by_equipment: defaultdict[str, dict[str, float]] = defaultdict(_new_hours_bucket)
    equipment_types: dict[str, str] = {}

    for log in equipment_logs:
        eid = str(log["equipment_id"])
        status = log.get("status", "active")
        equipment_types[eid] = log.get("equipment_type", "unknown")

        try:
            delta_hours = _hours_between(log["start_time"], log["end_time"])
        except (ValueError, KeyError) as exc:
            logger.warning("Skipping log entry for %s: %s", eid, exc)
            continue

        if status in hours_by_equipment[eid]:
            hours_by_equipment[eid][status] += delta_hours
        else:
            hours_by_equipment[eid]["active"] += delta_hours

    # Build per-equipment summary
    summary: dict[str, dict] = {}
    total_active = 0.0
    total_tracked = 0.0
    underutilized: list[str] = []

    for eid, hours in hours_by_equipment.items():
        active = hours["active"]
        idle = hours["idle"]
        maintenance = hours["maintenance"]
        total = active + idle + maintenance

        util_pct = (active / total * 100.0) if total > 0 else 0.0

        summary[eid] = {
            "equipment_type": equipment_types.get(eid, "unknown"),
            "utilization_pct": round(util_pct, 1),
            "active_hours": round(active, 1),
            "idle_hours": round(idle, 1),
            "maintenance_hours": round(maintenance, 1),
        }

        total_active += active
        total_tracked += total

        if util_pct < 60.0:
            underutilized.append(eid)

    fleet_utilization = round(total_active / total_tracked * 100.0, 1) if total_tracked > 0 else 0.0

    # Generate recommendations
    recommendations: list[str] = []

    if underutilized:
        recommendations.append(
            f"{len(underutilized)} piece(s) of equipment below 60% utilization: "
            + ", ".join(underutilized)
            + ". Consider reassigning, sharing across projects, or reducing fleet size."
        )

    overutilized = [eid for eid, stats in summary.items() if stats["utilization_pct"] > 90]
    if overutilized:
        recommendations.append(
            f"{len(overutilized)} piece(s) of equipment above 90% utilization: "
            + ", ".join(overutilized)
            + ". High utilization may increase breakdown risk. "
            "Consider adding backup equipment or scheduling maintenance windows."
        )

    high_maintenance = [
        eid
        for eid, hours in hours_by_equipment.items()
        if hours["maintenance"] > 0
        and hours["maintenance"] / (hours["active"] + hours["idle"] + hours["maintenance"]) > 0.15
    ]
    if high_maintenance:
        recommendations.append(
            "Equipment with >15% time in maintenance: "
            + ", ".join(high_maintenance)
            + ". Evaluate whether replacement would be more cost-effective."
        )

    if not recommendations:
        recommendations.append(
            "Fleet utilization is within acceptable parameters. No immediate action required."
        )

    logger.info(
        "Equipment utilization calculated: %d units, fleet=%.1f%%, %d underutilized",
        len(summary),
        fleet_utilization,
        len(underutilized),
    )

    return {
        "summary": summary,
        "fleet_utilization": fleet_utilization,
        "underutilized": underutilized,
        "recommendations": recommendations,
    }


async def predict_maintenance(
    equipment_data: dict,
) -> dict:
    """Predict maintenance needs based on equipment hours and history.

    Parameters
    ----------
    equipment_data:
        Dict with ``equipment_id``, ``type``, ``total_hours``,
        ``last_service_hours``, ``service_interval`` (optional, uses default
        by type), ``age_years``.

    Returns
    -------
    dict with status, hours_until_service, estimated_service_date,
    risk_score, recommendations.
    """
    eid = str(equipment_data.get("equipment_id", "unknown"))
    eq_type = equipment_data.get("type", "default")
    total_hours = float(equipment_data.get("total_hours", 0))
    last_service = float(equipment_data.get("last_service_hours", 0))
    age_years = float(equipment_data.get("age_years", 0))

    # Determine service interval
    service_interval = equipment_data.get("service_interval")
    if service_interval is None:
        service_interval = DEFAULT_SERVICE_INTERVALS.get(
            eq_type, DEFAULT_SERVICE_INTERVALS["default"]
        )
    service_interval = int(service_interval)

    # Hours since last service
    hours_since_service = total_hours - last_service
    hours_until_service = service_interval - hours_since_service

    # Determine status
    if hours_until_service > service_interval * 0.25:
        status = "good"
    elif hours_until_service > 0:
        status = "due"
    elif hours_until_service > -service_interval * 0.1:
        status = "overdue"
    else:
        status = "critical"

    # Risk score (0.0 - 1.0)
    # Base risk from service overdue
    service_risk = max(0.0, min(1.0, 1.0 - (hours_until_service / service_interval)))

    # Age risk factor
    expected_life = EXPECTED_LIFE_YEARS.get(eq_type, EXPECTED_LIFE_YEARS["default"])
    age_risk = min(1.0, age_years / expected_life) * 0.3

    # Combined risk
    risk_score = min(1.0, service_risk * 0.7 + age_risk)

    # Estimated service date (assuming ~8 operating hours per workday)
    hours_per_day = 8.0
    if hours_until_service > 0:
        days_until_service = int(hours_until_service / hours_per_day)
        estimated_date = datetime.now(UTC) + timedelta(days=days_until_service)
    else:
        # Service is overdue
        estimated_date = datetime.now(UTC)
    estimated_service_date = estimated_date.strftime("%Y-%m-%d")

    # Recommendations
    recommendations: list[str] = []

    if status == "critical":
        recommendations.append(
            f"URGENT: Equipment {eid} ({eq_type}) is critically overdue for "
            f"service by {abs(int(hours_until_service))} hours. "
            f"Schedule maintenance immediately to prevent failure."
        )
    elif status == "overdue":
        recommendations.append(
            f"Equipment {eid} ({eq_type}) is overdue for service by "
            f"{abs(int(hours_until_service))} hours. Schedule maintenance as "
            f"soon as possible."
        )
    elif status == "due":
        recommendations.append(
            f"Equipment {eid} ({eq_type}) is approaching its service interval. "
            f"{int(hours_until_service)} operating hours remaining. "
            f"Plan maintenance for {estimated_service_date}."
        )
    else:
        recommendations.append(
            f"Equipment {eid} ({eq_type}) is in good condition. "
            f"Next service in approximately {int(hours_until_service)} hours "
            f"(estimated {estimated_service_date})."
        )

    if age_years > expected_life * 0.8:
        recommendations.append(
            f"Equipment is {age_years:.0f} years old "
            f"(expected life: {expected_life} years). "
            f"Begin planning for replacement."
        )

    if total_hours > 10000 and age_years < 3:
        recommendations.append(
            f"High usage rate detected ({total_hours:.0f} hours in "
            f"{age_years:.1f} years). Consider adding a second unit "
            f"to distribute workload."
        )

    logger.info(
        "Maintenance prediction for %s: status=%s, risk=%.2f, hours_until=%d",
        eid,
        status,
        risk_score,
        int(hours_until_service),
    )

    return {
        "status": status,
        "hours_until_service": int(hours_until_service),
        "estimated_service_date": estimated_service_date,
        "risk_score": round(risk_score, 3),
        "recommendations": recommendations,
    }
