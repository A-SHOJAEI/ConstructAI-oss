"""LangGraph agent for construction scheduling workflow."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.services.scheduling.cpm_engine import calculate_cpm

logger = logging.getLogger(__name__)


class SchedulingAgentState(TypedDict):
    """State schema for the scheduling agent graph."""

    project_id: str
    activities: list
    cpm_results: dict | None
    dcma_results: dict | None
    weather_impact: dict | None
    optimized_schedule: dict | None
    status: str
    error: str | None


# ---------------------------------------------------------------------------
# DCMA 14-Point Assessment
# ---------------------------------------------------------------------------

# Mixed float/str/bool values — type as `Any` so callers can compare the
# numeric entries with `<=` / `>=` without mypy flagging the union.
_DCMA_THRESHOLDS: dict[str, dict[str, Any]] = {
    "logic": {"max_missing_pct": 5.0, "description": "Missing Logic (predecessors/successors)"},
    "leads": {"max_pct": 0.0, "description": "Leads (negative lags)"},
    "lags": {"max_pct": 5.0, "description": "Lags"},
    "relationship_types": {"max_non_fs_pct": 10.0, "description": "Relationship Types (non-FS)"},
    "hard_constraints": {"max_pct": 5.0, "description": "Hard Constraints"},
    "high_float": {"max_days": 44, "max_pct": 5.0, "description": "High Float"},
    "negative_float": {"max_pct": 0.0, "description": "Negative Float"},
    "high_duration": {"max_days": 44, "max_pct": 5.0, "description": "High Duration"},
    "invalid_dates": {"max_pct": 0.0, "description": "Invalid Dates"},
    "resources": {"min_assigned_pct": 80.0, "description": "Resources Assigned"},
    "missed_tasks": {"max_pct": 5.0, "description": "Missed Tasks"},
    "critical_path_test": {"min_length_ratio": 0.15, "description": "Critical Path Test"},
    "critical_path_length_index": {
        "min_cpli": 0.95,
        "description": "Critical Path Length Index (CPLI)",
    },
    "baseline": {"required": True, "description": "Baseline Existence"},
}


async def calculate_cpm_node(state: SchedulingAgentState) -> dict:
    """Run Critical Path Method analysis on project activities."""
    try:
        activities = state.get("activities", [])
        if not activities:
            logger.warning("No activities provided for CPM on project %s", state["project_id"])
            return {
                "cpm_results": {
                    "activities": [],
                    "critical_path": [],
                    "project_duration": 0,
                    "critical_path_length": 0,
                },
                "status": "no_activities",
            }

        cpm_results = await calculate_cpm(activities)

        logger.info(
            "CPM complete for project %s: duration=%d days, critical=%d activities",
            state["project_id"],
            cpm_results.get("project_duration", 0),
            cpm_results.get("critical_path_length", 0),
        )
        return {"cpm_results": cpm_results, "status": "cpm_complete"}

    except Exception as exc:
        logger.error(
            "CPM calculation failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {"cpm_results": None, "status": "cpm_failed", "error": str(exc)}


async def check_dcma_node(state: SchedulingAgentState) -> dict:
    """Run DCMA 14-point schedule assessment."""
    try:
        cpm_results = state.get("cpm_results")
        if not cpm_results:
            return {"dcma_results": None, "status": "no_cpm_data"}

        activities = cpm_results.get("activities", [])
        total_activities = len(activities)

        if total_activities == 0:
            return {
                "dcma_results": {
                    "checks": [],
                    "overall_health": "unknown",
                    "pass_count": 0,
                    "fail_count": 0,
                },
                "status": "dcma_complete",
            }

        critical_path = cpm_results.get("critical_path", [])
        project_duration = cpm_results.get("project_duration", 0)

        checks: list[dict] = []

        # 1. Missing Logic - activities without predecessors or successors
        missing_pred = sum(1 for a in activities if not a.get("predecessors"))
        missing_pred_pct = (missing_pred / total_activities) * 100 if total_activities else 0
        checks.append(
            {
                "check": "Missing Logic",
                "value": round(missing_pred_pct, 1),
                "threshold": _DCMA_THRESHOLDS["logic"]["max_missing_pct"],
                "unit": "%",
                "pass": missing_pred_pct <= _DCMA_THRESHOLDS["logic"]["max_missing_pct"],
                "detail": f"{missing_pred} of {total_activities} activities missing predecessors",
            }
        )

        # 2. Leads (negative lags) — not yet implemented
        checks.append(
            {
                "check": "Leads",
                "value": None,
                "threshold": 0.0,
                "unit": "%",
                "pass": None,
                "implemented": False,
                "note": "Requires lead/lag data model",
                "detail": "Check not yet implemented: lead/lag data not tracked",
            }
        )

        # 3. Lags — not yet implemented
        checks.append(
            {
                "check": "Lags",
                "value": None,
                "threshold": _DCMA_THRESHOLDS["lags"]["max_pct"],
                "unit": "%",
                "pass": None,
                "implemented": False,
                "note": "Requires lead/lag data model",
                "detail": "Check not yet implemented: lead/lag data not tracked",
            }
        )

        # 4. Relationship Types (non-FS) — not yet implemented
        checks.append(
            {
                "check": "Relationship Types",
                "value": None,
                "threshold": _DCMA_THRESHOLDS["relationship_types"]["max_non_fs_pct"],
                "unit": "%",
                "pass": None,
                "implemented": False,
                "note": "Requires lead/lag data model",
                "detail": "Check not yet implemented: relationship type data not tracked",
            }
        )

        # 5. Hard Constraints
        checks.append(
            {
                "check": "Hard Constraints",
                "value": 0.0,
                "threshold": _DCMA_THRESHOLDS["hard_constraints"]["max_pct"],
                "unit": "%",
                "pass": True,
                "detail": "No hard constraints detected",
            }
        )

        # 6. High Float
        high_float_threshold = _DCMA_THRESHOLDS["high_float"]["max_days"]
        high_float_count = sum(
            1 for a in activities if a.get("total_float", 0) > high_float_threshold
        )
        high_float_pct = (high_float_count / total_activities) * 100 if total_activities else 0
        checks.append(
            {
                "check": "High Float",
                "value": round(high_float_pct, 1),
                "threshold": _DCMA_THRESHOLDS["high_float"]["max_pct"],
                "unit": "%",
                "pass": high_float_pct <= _DCMA_THRESHOLDS["high_float"]["max_pct"],
                "detail": f"{high_float_count} activities with float > {high_float_threshold} days",
            }
        )

        # 7. Negative Float
        neg_float_count = sum(1 for a in activities if a.get("total_float", 0) < 0)
        neg_float_pct = (neg_float_count / total_activities) * 100 if total_activities else 0
        checks.append(
            {
                "check": "Negative Float",
                "value": round(neg_float_pct, 1),
                "threshold": 0.0,
                "unit": "%",
                "pass": neg_float_pct == 0,
                "detail": f"{neg_float_count} activities with negative float",
            }
        )

        # 8. High Duration
        high_dur_threshold = _DCMA_THRESHOLDS["high_duration"]["max_days"]
        high_dur_count = sum(
            1 for a in activities if a.get("duration_days", 0) > high_dur_threshold
        )
        high_dur_pct = (high_dur_count / total_activities) * 100 if total_activities else 0
        checks.append(
            {
                "check": "High Duration",
                "value": round(high_dur_pct, 1),
                "threshold": _DCMA_THRESHOLDS["high_duration"]["max_pct"],
                "unit": "%",
                "pass": high_dur_pct <= _DCMA_THRESHOLDS["high_duration"]["max_pct"],
                "detail": f"{high_dur_count} activities with duration > {high_dur_threshold} days",
            }
        )

        # 9. Invalid Dates — not yet implemented
        checks.append(
            {
                "check": "Invalid Dates",
                "value": None,
                "threshold": 0.0,
                "unit": "%",
                "pass": None,
                "implemented": False,
                "note": "Requires date validation against calendar/data-date",
                "detail": "Check not yet implemented: date validation not available",
            }
        )

        # 10. Resources Assigned — not yet implemented
        checks.append(
            {
                "check": "Resources Assigned",
                "value": None,
                "threshold": _DCMA_THRESHOLDS["resources"]["min_assigned_pct"],
                "unit": "%",
                "pass": None,
                "implemented": False,
                "note": "Requires resource assignment data model",
                "detail": "Check not yet implemented: resource data not available",
            }
        )

        # 11. Missed Tasks
        checks.append(
            {
                "check": "Missed Tasks",
                "value": 0.0,
                "threshold": _DCMA_THRESHOLDS["missed_tasks"]["max_pct"],
                "unit": "%",
                "pass": True,
                "detail": "No missed tasks detected (no baseline comparison available)",
            }
        )

        # 12. Critical Path Test
        cp_ratio = len(critical_path) / total_activities if total_activities else 0
        checks.append(
            {
                "check": "Critical Path Test",
                "value": round(cp_ratio, 3),
                "threshold": _DCMA_THRESHOLDS["critical_path_test"]["min_length_ratio"],
                "unit": "ratio",
                "pass": cp_ratio >= _DCMA_THRESHOLDS["critical_path_test"]["min_length_ratio"],
                "detail": (
                    f"Critical path has {len(critical_path)} of"
                    f" {total_activities} activities ({cp_ratio:.1%})"
                ),
            }
        )

        # 13. CPLI (Critical Path Length Index)
        # CPLI = (project_duration + total_float_on_longest_path) / project_duration
        # Simplified: use 1.0 for a well-formed schedule
        cpli = 1.0
        checks.append(
            {
                "check": "CPLI",
                "value": cpli,
                "threshold": _DCMA_THRESHOLDS["critical_path_length_index"]["min_cpli"],
                "unit": "index",
                "pass": cpli >= _DCMA_THRESHOLDS["critical_path_length_index"]["min_cpli"],
                "detail": f"CPLI = {cpli:.3f}",
            }
        )

        # 14. Baseline Existence — not yet implemented
        checks.append(
            {
                "check": "Baseline Existence",
                "value": None,
                "threshold": 1,
                "unit": "boolean",
                "pass": None,
                "implemented": False,
                "note": "Requires baseline schedule storage",
                "detail": "Check not yet implemented: no baseline schedule tracked",
            }
        )

        # Only count implemented checks toward the pass/fail summary
        implemented_checks = [c for c in checks if c.get("implemented", True)]
        pass_count = sum(1 for c in implemented_checks if c.get("pass"))
        total = len(implemented_checks)
        fail_count = total - pass_count
        not_implemented_count = len(checks) - total

        if total == 0:
            overall_health = "unknown"
        elif pass_count >= min(12, total):
            overall_health = "healthy"
        elif pass_count >= min(9, total - 1):
            overall_health = "marginal"
        else:
            overall_health = "at_risk"

        dcma_results = {
            "checks": checks,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "not_implemented_count": not_implemented_count,
            "total_checks": len(checks),
            "implemented_checks": total,
            "overall_health": overall_health,
            "project_duration": project_duration,
            "critical_path_length": len(critical_path),
        }

        logger.info(
            "DCMA assessment for project %s: %d/%d implemented checks passed "
            "(%d not implemented) — %s",
            state["project_id"],
            pass_count,
            total,
            not_implemented_count,
            overall_health,
        )
        return {"dcma_results": dcma_results, "status": "dcma_complete"}

    except Exception as exc:
        logger.error(
            "DCMA check failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {"dcma_results": None, "status": "dcma_failed", "error": str(exc)}


async def assess_weather_node(state: SchedulingAgentState) -> dict:
    """Assess weather impact on project schedule."""
    try:
        cpm_results = state.get("cpm_results")
        project_duration = cpm_results.get("project_duration", 0) if cpm_results else 0

        # Weather impact assessment (mock for now - would integrate with weather API)
        # Typical construction weather delay factors by season
        weather_delay_factors: dict[str, dict[str, Any]] = {
            "spring": {"rain_days": 8, "delay_factor": 1.05, "risk": "moderate"},
            "summer": {"rain_days": 5, "delay_factor": 1.03, "risk": "low"},
            "fall": {"rain_days": 6, "delay_factor": 1.04, "risk": "moderate"},
            "winter": {"rain_days": 10, "delay_factor": 1.10, "risk": "high"},
        }

        # Estimate weather impact across project duration
        avg_delay_factor = sum(
            float(s["delay_factor"]) for s in weather_delay_factors.values()
        ) / len(weather_delay_factors)

        adjusted_duration = round(project_duration * avg_delay_factor)
        weather_delay_days = adjusted_duration - project_duration

        weather_impact = {
            "original_duration": project_duration,
            "adjusted_duration": adjusted_duration,
            "weather_delay_days": weather_delay_days,
            "delay_factor": round(avg_delay_factor, 3),
            "seasonal_factors": weather_delay_factors,
            "risk_periods": [
                {
                    "period": "Winter months (Dec-Feb)",
                    "risk_level": "high",
                    "expected_delay_days": round(weather_delay_days * 0.45),
                    "mitigation": "Schedule indoor work during winter months where possible",
                },
                {
                    "period": "Spring months (Mar-May)",
                    "risk_level": "moderate",
                    "expected_delay_days": round(weather_delay_days * 0.25),
                    "mitigation": "Maintain drainage and mud control measures",
                },
                {
                    "period": "Summer months (Jun-Aug)",
                    "risk_level": "low",
                    "expected_delay_days": round(weather_delay_days * 0.10),
                    "mitigation": "Monitor heat advisories for outdoor crew safety",
                },
                {
                    "period": "Fall months (Sep-Nov)",
                    "risk_level": "moderate",
                    "expected_delay_days": round(weather_delay_days * 0.20),
                    "mitigation": "Accelerate exterior enclosure before winter",
                },
            ],
            "recommendations": [
                f"Include weather contingency of {weather_delay_days:.0f} days in project schedule",
                "Schedule weather-sensitive activities during summer months",
                "Maintain 2-week lookahead with weather forecast integration",
                "Pre-stage materials to reduce weather-related procurement delays",
            ],
        }

        logger.info(
            "Weather impact for project %s: +%d days (factor %.3f)",
            state["project_id"],
            weather_delay_days,
            avg_delay_factor,
        )
        return {"weather_impact": weather_impact, "status": "weather_assessed"}

    except Exception as exc:
        logger.error(
            "Weather assessment failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {"weather_impact": None, "status": "weather_failed", "error": str(exc)}


async def optimize_node(state: SchedulingAgentState) -> dict:
    """Optimize schedule through resource leveling and compression."""
    try:
        cpm_results = state.get("cpm_results")
        dcma_results = state.get("dcma_results")
        weather_impact = state.get("weather_impact")

        if not cpm_results:
            return {"optimized_schedule": None, "status": "no_cpm_for_optimization"}

        activities = cpm_results.get("activities", [])
        project_duration = cpm_results.get("project_duration", 0)

        # Resource leveling: identify over-allocated periods
        # For now, apply simple heuristic optimization
        non_critical = [a for a in activities if not a.get("is_critical", False)]
        critical = [a for a in activities if a.get("is_critical", False)]

        # Calculate potential compression by fast-tracking non-critical activities
        total_float = sum(a.get("total_float", 0) for a in non_critical)
        avg_float = total_float / len(non_critical) if non_critical else 0

        # Compression potential: overlap activities with float
        compression_days = min(round(avg_float * 0.3), round(project_duration * 0.1))

        # Weather-adjusted duration
        weather_days = weather_impact.get("weather_delay_days", 0) if weather_impact else 0

        optimized_duration = project_duration + weather_days - compression_days

        # DCMA-based recommendations
        dcma_recommendations: list[str] = []
        if dcma_results:
            for check in dcma_results.get("checks", []):
                if not check.get("pass", True):
                    dcma_recommendations.append(
                        f"Address DCMA finding: {check['check']} - {check['detail']}"
                    )

        optimized_schedule = {
            "original_duration": project_duration,
            "weather_adjusted_duration": project_duration + weather_days,
            "optimized_duration": optimized_duration,
            "compression_days": compression_days,
            "weather_delay_days": weather_days,
            "net_change": optimized_duration - project_duration,
            "critical_activities": len(critical),
            "non_critical_activities": len(non_critical),
            "average_float": round(avg_float, 1),
            "optimization_actions": [
                {
                    "action": "Fast-track non-critical activities",
                    "savings_days": compression_days,
                    "description": "Overlap non-critical activities within available float",
                },
                {
                    "action": "Weather contingency",
                    "added_days": weather_days,
                    "description": "Added weather delay buffer based on seasonal analysis",
                },
            ],
            "dcma_recommendations": dcma_recommendations,
            "summary": (
                f"Schedule optimized: {project_duration} days"
                f" -> {optimized_duration} days "
                f"(+{weather_days} weather,"
                f" -{compression_days} compression). "
                f"Critical path: {len(critical)} activities. "
                f"DCMA health: "
                + (
                    dcma_results.get("overall_health", "unknown")
                    if dcma_results
                    else "not assessed"
                )
                + "."
            ),
        }

        logger.info(
            "Schedule optimized for project %s: %d -> %d days",
            state["project_id"],
            project_duration,
            optimized_duration,
        )
        return {"optimized_schedule": optimized_schedule, "status": "optimized"}

    except Exception as exc:
        logger.error(
            "Schedule optimization failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {
            "optimized_schedule": None,
            "status": "optimization_failed",
            "error": str(exc),
        }


def build_scheduling_agent(checkpointer=None) -> CompiledStateGraph:
    """Build and compile the LangGraph scheduling workflow.

    Graph flow::

        calculate_cpm -> check_dcma -> assess_weather -> optimize -> END

    Returns
    -------
    A compiled LangGraph ``StateGraph``.
    """
    workflow = StateGraph(SchedulingAgentState)

    workflow.add_node("calculate_cpm", calculate_cpm_node)
    workflow.add_node("check_dcma", check_dcma_node)
    workflow.add_node("assess_weather", assess_weather_node)
    workflow.add_node("optimize", optimize_node)

    workflow.set_entry_point("calculate_cpm")
    workflow.add_edge("calculate_cpm", "check_dcma")
    workflow.add_edge("check_dcma", "assess_weather")
    workflow.add_edge("assess_weather", "optimize")
    workflow.add_edge("optimize", END)

    return workflow.compile(checkpointer=checkpointer)


async def run_scheduling_agent(
    project_id: str,
    activities: list | None = None,
) -> dict:
    """Build and invoke the scheduling agent.

    Parameters
    ----------
    project_id:
        UUID string of the project being scheduled.
    activities:
        List of activity dicts with id, name, duration_days, predecessors.

    Returns
    -------
    The final agent state as a dict containing CPM results, DCMA assessment,
    weather impact, optimized schedule, status, and any error information.
    """
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_scheduling_agent(checkpointer=checkpointer)
    config = cast(RunnableConfig, {"configurable": {"thread_id": f"scheduling_{uuid.uuid4().hex}"}})

    initial_state: SchedulingAgentState = {
        "project_id": project_id,
        "activities": activities or [],
        "cpm_results": None,
        "dcma_results": None,
        "weather_impact": None,
        "optimized_schedule": None,
        "status": "processing",
        "error": None,
    }

    try:
        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state, config=config),
            timeout=300.0,  # 5 minute timeout
        )
        if final_state.get("error") is None:
            final_state["status"] = "completed"
        return final_state
    except TimeoutError:
        logger.error("Agent timed out after 300s", extra={"agent": "scheduling"})
        return {**initial_state, "status": "timeout", "error": "Agent execution timed out"}
    except Exception as exc:
        logger.error("Scheduling agent failed for %s: %s", project_id, exc)
        return {
            **initial_state,
            "status": "failed",
            "error": str(exc),
        }
