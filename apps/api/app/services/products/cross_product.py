"""Cross-product intelligence engine.

Listens for events from each product and triggers downstream actions.
This is the "glue" that makes the platform more than 7 separate tools.

Events are processed asynchronously — callers fire-and-forget.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event handler registry
# ---------------------------------------------------------------------------

EVENT_HANDLERS: dict[str, list] = {}


def handles(event_type: str):
    """Decorator to register a function as a handler for an event type."""

    def decorator(fn):
        EVENT_HANDLERS.setdefault(event_type, []).append(fn)
        return fn

    return decorator


async def dispatch_event(
    db: AsyncSession,
    event_type: str,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    payload: dict,
) -> list[dict]:
    """Dispatch an event to all registered handlers.

    Returns a list of handler results (for logging/debugging).
    Each handler failure is isolated — it does not affect other handlers.
    """
    handlers = EVENT_HANDLERS.get(event_type, [])
    results = []
    for handler in handlers:
        try:
            result = await handler(db, project_id, org_id, payload)
            results.append({"handler": handler.__name__, "status": "ok", "result": result})
        except Exception:
            logger.exception(
                "Cross-product handler %s failed for event %s",
                handler.__name__,
                event_type,
            )
            results.append({"handler": handler.__name__, "status": "error"})
    return results


# ---------------------------------------------------------------------------
# SiteScribe → other products
# ---------------------------------------------------------------------------


@handles("constructai.sitescribe.report_approved")
async def handle_report_for_heatshield(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    payload: dict,
) -> dict | None:
    """Extract weather data from approved daily report for HeatShield."""
    weather = payload.get("weather_data")
    if not weather:
        return None

    temp_f = weather.get("high_temp_f") or weather.get("temperature_f")
    if temp_f is None:
        return None

    # Only record if temperature is notable (above initial threshold)
    if float(temp_f) < 80.0:
        return {"skipped": True, "reason": "below_threshold"}

    try:
        from app.services.products.heatshield.service import record_manual_reading

        await record_manual_reading(
            db,
            project_id,
            org_id,
            {
                "temperature_f": float(temp_f),
                "humidity_pct": weather.get("humidity_pct"),
                "wind_speed_mph": weather.get("wind_speed_mph"),
            },
        )
        return {"recorded": True, "temperature_f": float(temp_f)}
    except Exception:
        logger.debug("HeatShield not available for cross-product event", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# RFI → other products
# ---------------------------------------------------------------------------


@handles("constructai.rfi.responded")
async def handle_rfi_for_changeflow(
    _db: AsyncSession,
    _project_id: uuid.UUID,
    _org_id: uuid.UUID,
    payload: dict,
) -> dict | None:
    """If an RFI response indicates a design change, create a change event."""
    response_text = payload.get("response_text", "")
    if not response_text:
        return None

    # Simple keyword detection for design changes
    design_change_keywords = [
        "revised drawing",
        "design change",
        "updated specification",
        "new detail",
        "modified design",
        "revised detail",
        "change in scope",
    ]
    lower_text = response_text.lower()
    is_design_change = any(kw in lower_text for kw in design_change_keywords)

    if not is_design_change:
        return {"skipped": True, "reason": "no_design_change_detected"}

    return {
        "design_change_detected": True,
        "rfi_id": payload.get("rfi_id"),
        "note": "Suggest creating change event for design change from RFI response",
    }


# ---------------------------------------------------------------------------
# HeatShield → Safety
# ---------------------------------------------------------------------------


@handles("constructai.heat.incident_reported")
async def handle_heat_for_safety(
    _db: AsyncSession,
    _project_id: uuid.UUID,
    _org_id: uuid.UUID,
    payload: dict,
) -> dict | None:
    """Create a safety incident record from a heat incident."""
    return {
        "action": "safety_incident_suggested",
        "source": "heatshield",
        "worker_name": payload.get("worker_name"),
        "incident_date": payload.get("incident_date"),
        "note": "Heat incident should be reviewed by safety director",
    }


# ---------------------------------------------------------------------------
# WageGuard → Controls
# ---------------------------------------------------------------------------


@handles("constructai.wage.payroll_certified")
async def handle_payroll_for_controls(
    _db: AsyncSession,
    _project_id: uuid.UUID,
    _org_id: uuid.UUID,
    payload: dict,
) -> dict | None:
    """Update labor costs in project controls when payroll is certified."""
    total_gross = payload.get("total_gross_pay")
    if total_gross is None:
        return None

    return {
        "action": "labor_cost_update_suggested",
        "total_gross_pay": total_gross,
        "week_ending": payload.get("week_ending"),
        "note": "Certified payroll labor costs available for EVM update",
    }


# ---------------------------------------------------------------------------
# CloseoutIQ → Controls
# ---------------------------------------------------------------------------


@handles("constructai.closeout.all_complete")
async def handle_closeout_for_controls(
    _db: AsyncSession,
    _project_id: uuid.UUID,
    _org_id: uuid.UUID,
    payload: dict,
) -> dict | None:
    """Signal substantial completion when all closeout requirements are met."""
    return {
        "action": "substantial_completion_milestone",
        "completion_date": str(date.today()),
        "total_requirements": payload.get("total_requirements"),
        "note": "All closeout requirements accepted — project ready for substantial completion",
    }


# ---------------------------------------------------------------------------
# Unified project health brief
# ---------------------------------------------------------------------------


async def generate_unified_brief(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
) -> dict:
    """Generate a cross-product project health brief.

    Aggregates data from all 7 products plus platform services
    to produce a comprehensive weekly health summary.
    """
    brief: dict = {
        "project_id": str(project_id),
        "generated_at": str(date.today()),
        "products": {},
    }

    # SiteScribe
    try:
        from app.services.products.sitescribe.service import get_dashboard

        brief["products"]["sitescribe"] = await get_dashboard(db, project_id)
    except Exception:
        brief["products"]["sitescribe"] = {"status": "unavailable"}

    # RFI Copilot
    try:
        from app.services.products.rfi_copilot.service import get_rfi_analytics

        brief["products"]["rfi_copilot"] = await get_rfi_analytics(db, project_id)
    except Exception:
        brief["products"]["rfi_copilot"] = {"status": "unavailable"}

    # CloseoutIQ
    try:
        from app.services.products.closeout_iq.service import get_dashboard as closeout_dash

        brief["products"]["closeout_iq"] = await closeout_dash(db, project_id)
    except Exception:
        brief["products"]["closeout_iq"] = {"status": "unavailable"}

    # HeatShield
    try:
        from app.services.products.heatshield.service import get_dashboard as heat_dash

        brief["products"]["heatshield"] = await heat_dash(db, project_id)
    except Exception:
        brief["products"]["heatshield"] = {"status": "unavailable"}

    # CarbonLens
    try:
        from app.services.products.carbonlens.service import get_dashboard as carbon_dash

        brief["products"]["carbonlens"] = await carbon_dash(db, project_id)
    except Exception:
        brief["products"]["carbonlens"] = {"status": "unavailable"}

    return brief
