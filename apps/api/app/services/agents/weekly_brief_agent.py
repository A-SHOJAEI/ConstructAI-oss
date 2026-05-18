"""WeeklyBriefAgent — autonomous project intelligence report generator.

Orchestrates all of ConstructAI's analytical capabilities via LangGraph
with 4 parallel sub-agents + an LLM-powered synthesizer:

1. ScheduleIntelligence  (CPM, Monte Carlo, float erosion, SPI trend)
2. CostIntelligence      (EVM, EAC forecasting, CO impact, price trends)
3. RiskIntelligence      (weather, open items, emerging critical paths)
4. ProductivityIntelligence (planned vs actual production rates)
5. Synthesizer           (LLM narrative, health score, action items)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Health-score weights
# ---------------------------------------------------------------------------

WEIGHT_SCHEDULE = 0.30
WEIGHT_COST = 0.30
WEIGHT_RISK = 0.25
WEIGHT_PRODUCTIVITY = 0.15

# Status thresholds
STATUS_GREEN_THRESHOLD = 80
STATUS_YELLOW_THRESHOLD = 60


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class WeeklyBriefState(TypedDict):
    """State schema for the weekly brief agent graph."""

    project_id: str
    org_id: str
    project_data: dict  # loaded from DB: project info, EVM, schedule, COs, etc.

    # Sub-agent outputs (populated in parallel)
    schedule_intelligence: dict | None
    cost_intelligence: dict | None
    risk_intelligence: dict | None
    productivity_intelligence: dict | None

    # Synthesizer output
    executive_summary: str | None
    overall_health_score: int | None
    project_status: str | None  # GREEN / YELLOW / RED
    action_items: list[dict] | None
    metrics_dashboard: dict | None
    narrative_report: str | None

    # Guardrails
    guardrails_result: dict | None

    # Control
    status: str
    errors: list[str]


# ---------------------------------------------------------------------------
# Helper: scoring utilities
# ---------------------------------------------------------------------------


def _spi_to_score(spi: float) -> int:
    """Convert SPI value to a 0-100 score."""
    if spi >= 1.0:
        return 100
    if spi >= 0.95:
        return 85
    if spi >= 0.90:
        return 70
    if spi >= 0.85:
        return 55
    if spi >= 0.80:
        return 40
    return max(0, int(spi * 50))


def _cpi_to_score(cpi: float) -> int:
    """Convert CPI value to a 0-100 score."""
    if cpi >= 1.0:
        return 100
    if cpi >= 0.95:
        return 80
    if cpi >= 0.90:
        return 60
    if cpi >= 0.85:
        return 40
    return max(0, int(cpi * 40))


def _float_health(activities: list[dict]) -> int:
    """Score float health (0-100) based on average total float."""
    floats = [a.get("total_float", 10) for a in activities if a.get("total_float") is not None]
    if not floats:
        return 50  # no data
    avg = sum(floats) / len(floats)
    if avg > 10:
        return 100
    if avg > 5:
        return 70
    if avg > 2:
        return 40
    return 15


def _detect_spi_trend(spi_values: list[float]) -> str:
    """Detect SPI trend from last N values: improving/stable/deteriorating."""
    if len(spi_values) < 2:
        return "insufficient_data"
    diffs = [spi_values[i] - spi_values[i - 1] for i in range(1, len(spi_values))]
    avg_diff = sum(diffs) / len(diffs)
    if avg_diff > 0.02:
        return "improving"
    if avg_diff < -0.02:
        return "deteriorating"
    return "stable"


def _detect_float_erosion(
    current_activities: list[dict],
    previous_activities: list[dict],
) -> list[dict]:
    """Identify activities where float decreased since last period."""
    prev_floats = {a["id"]: a.get("total_float", 0) for a in previous_activities}
    erosion = []
    for act in current_activities:
        aid = act.get("id")
        curr_float = act.get("total_float", 0)
        prev_float = prev_floats.get(aid)
        if prev_float is not None and curr_float < prev_float:
            erosion.append(
                {
                    "activity_id": aid,
                    "activity_name": act.get("name", ""),
                    "previous_float": prev_float,
                    "current_float": curr_float,
                    "erosion_days": prev_float - curr_float,
                }
            )
    return sorted(erosion, key=lambda x: x["erosion_days"], reverse=True)[:10]


# ---------------------------------------------------------------------------
# Node 0: Load project data
# ---------------------------------------------------------------------------


async def load_project_data_node(state: WeeklyBriefState) -> dict:
    """Load project data into state for sub-agents to consume.

    In production this queries the DB. For agent invocation, data is
    passed directly via project_data in the initial state.
    """
    project_data = state.get("project_data", {})
    if not project_data:
        return {"status": "no_project_data", "errors": ["No project data provided"]}
    return {"status": "data_loaded"}


# ---------------------------------------------------------------------------
# Sub-Agent 1: Schedule Intelligence
# ---------------------------------------------------------------------------


async def schedule_intelligence_node(state: WeeklyBriefState) -> dict:
    """Analyze schedule health: CPM, float, Monte Carlo, SPI trend."""
    try:
        pd = state.get("project_data", {})
        activities = pd.get("activities", [])
        evm_snapshots = pd.get("evm_snapshots", [])
        previous_activities = pd.get("previous_activities", [])

        result: dict = {
            "health_score": 50,
            "critical_path": [],
            "critical_path_changes": [],
            "float_erosion_alerts": [],
            "p50_duration": None,
            "p90_duration": None,
            "spi_trend": "insufficient_data",
            "spi_values": [],
            "warnings": [],
        }

        # --- CPM analysis ---
        if activities:
            from app.services.scheduling.cpm_engine import calculate_cpm

            cpm = await calculate_cpm(activities)
            crit_path = cpm.get("critical_path", [])
            result["critical_path"] = crit_path
            result["project_duration"] = cpm.get("project_duration", 0)

            # Enrich activities with CPM data
            cpm_activities = cpm.get("activities", {})
            enriched = []
            for act in activities:
                cpm_data = cpm_activities.get(act["id"], {})
                enriched.append({**act, **cpm_data})

            # Float erosion
            if previous_activities:
                result["float_erosion_alerts"] = _detect_float_erosion(
                    enriched, previous_activities
                )

            # Compare against baseline critical path
            baseline_cp = pd.get("baseline_critical_path", [])
            if baseline_cp and crit_path:
                new_on_cp = [a for a in crit_path if a not in baseline_cp]
                removed_from_cp = [a for a in baseline_cp if a not in crit_path]
                result["critical_path_changes"] = {
                    "new_critical": new_on_cp,
                    "no_longer_critical": removed_from_cp,
                }

            # Float health score component
            float_score = _float_health(enriched)
        else:
            float_score = 50
            result["warnings"].append("No schedule activities available")

        # --- Monte Carlo ---
        if activities:
            try:
                from app.services.controls.monte_carlo_schedule import (
                    run_schedule_risk_simulation,
                )

                mc_result = await run_schedule_risk_simulation(
                    activities=activities,
                    num_iterations=2000,
                    seed=42,
                    use_correlations=True,
                )
                result["p50_duration"] = mc_result.get("p50")
                result["p90_duration"] = mc_result.get("p90")
                result["criticality_index"] = mc_result.get("criticality_index", {})

                # Monte Carlo score: how close is P50 to baseline?
                planned = pd.get("planned_duration")
                if planned and planned > 0:
                    ratio = mc_result.get("p50", planned) / planned
                    if ratio <= 1.0:
                        mc_score = 100
                    elif ratio <= 1.05:
                        mc_score = 80
                    elif ratio <= 1.10:
                        mc_score = 60
                    elif ratio <= 1.20:
                        mc_score = 40
                    else:
                        mc_score = max(0, int(100 - (ratio - 1) * 200))
                else:
                    mc_score = 50
            except Exception as exc:
                logger.warning("Monte Carlo failed in schedule intelligence: %s", exc)
                mc_score = 50
                result["warnings"].append(f"Monte Carlo unavailable: {exc}")
        else:
            mc_score = 50

        # --- SPI trend ---
        spi_values = [
            float(s.get("spi", 1))
            for s in sorted(evm_snapshots, key=lambda s: s.get("snapshot_date", ""))
        ]
        if spi_values:
            last4 = spi_values[-4:]
            result["spi_trend"] = _detect_spi_trend(last4)
            result["spi_values"] = last4
            spi_score = _spi_to_score(last4[-1])
        else:
            spi_score = 50

        # --- Composite health score ---
        result["health_score"] = int(spi_score * 0.40 + float_score * 0.30 + mc_score * 0.30)

        return {"schedule_intelligence": result}

    except Exception as exc:
        logger.error("Schedule intelligence failed: %s", exc)
        return {
            "schedule_intelligence": {
                "health_score": 50,
                "warnings": [f"Schedule analysis error: {exc}"],
            },
            "errors": [f"schedule: {exc}"],
        }


# ---------------------------------------------------------------------------
# Sub-Agent 2: Cost Intelligence
# ---------------------------------------------------------------------------


async def cost_intelligence_node(state: WeeklyBriefState) -> dict:
    """Analyze cost health: EVM, EAC, change orders, price trends."""
    try:
        pd = state.get("project_data", {})
        evm_data = pd.get("latest_evm", {})
        change_orders = pd.get("change_orders", [])
        contract_value = Decimal(str(pd.get("contract_value", 0) or 0))

        result: dict = {
            "health_score": 50,
            "evm_metrics": {},
            "eac_forecasts": {},
            "co_impact": {},
            "material_price_alerts": [],
            "budget_variance_flags": [],
            "warnings": [],
        }

        # --- EVM ---
        bac = Decimal(str(evm_data.get("bac", 0) or 0))
        pv = Decimal(str(evm_data.get("pv", 0) or 0))
        ev = Decimal(str(evm_data.get("ev", 0) or 0))
        ac = Decimal(str(evm_data.get("ac", 0) or 0))

        if bac > 0:
            from app.services.controls.evm_engine import compute_evm_snapshot

            metrics = await compute_evm_snapshot(bac=bac, pv=pv, ev=ev, ac=ac)
            result["evm_metrics"] = {
                k: str(v) if isinstance(v, Decimal) else v for k, v in metrics.items()
            }

            cpi = float(metrics.get("cpi") or 1)
            spi = float(metrics.get("spi") or 1)
            cpi_score = _cpi_to_score(cpi)

            # --- EAC ---
            from app.services.controls.eac_forecaster import forecast_eac

            project_type = pd.get("type", "commercial")
            eac_all = await forecast_eac(
                bac=bac,
                ev=ev,
                ac=ac,
                spi=Decimal(str(spi)),
                cpi=Decimal(str(cpi)),
                method="all",
                project_type=project_type,
            )
            result["eac_forecasts"] = eac_all

            # EAC vs BAC score
            eac_cpi = eac_all.get("all_methods", {}).get("cpi", {}).get("eac_value")
            if eac_cpi and bac > 0:
                eac_ratio = float(Decimal(str(eac_cpi)) / bac)
                if eac_ratio <= 1.0:
                    eac_score = 100
                elif eac_ratio <= 1.05:
                    eac_score = 75
                elif eac_ratio <= 1.10:
                    eac_score = 50
                else:
                    eac_score = max(0, int(100 - (eac_ratio - 1) * 300))
            else:
                eac_score = 50
        else:
            cpi_score = 50
            eac_score = 50
            result["warnings"].append("No EVM data available")

        # --- Change order impact ---
        if change_orders and contract_value > 0:
            cum_cost: Decimal = sum(
                (Decimal(str(co.get("cost_impact", 0))) for co in change_orders),
                Decimal(0),
            )
            co_pct = float(abs(cum_cost) / contract_value * 100)
            result["co_impact"] = {
                "total_change_orders": len(change_orders),
                "cumulative_cost_impact": str(cum_cost),
                "percent_of_contract": round(co_pct, 2),
                "open_count": sum(1 for co in change_orders if co.get("status") == "pending"),
            }
            if co_pct < 5:
                co_score = 100
            elif co_pct < 10:
                co_score = 60
            elif co_pct < 15:
                co_score = 30
            else:
                co_score = 0
        else:
            co_score = 80  # no COs = generally positive
            result["co_impact"] = {"total_change_orders": 0}

        # --- Budget variance flags ---
        division_budgets = pd.get("division_budgets", {})
        division_actuals = pd.get("division_actuals", {})
        for div, budget in division_budgets.items():
            actual = division_actuals.get(div, 0)
            if budget > 0 and actual > budget * 1.05:
                result["budget_variance_flags"].append(
                    {
                        "division": div,
                        "budget": str(budget),
                        "actual": str(actual),
                        "variance_pct": round((actual / budget - 1) * 100, 1),
                    }
                )

        # --- Material price alerts ---
        try:
            from app.services.procurement.price_forecaster import FRED_SERIES_MAP

            for series_id, info in list(FRED_SERIES_MAP.items())[:3]:
                # Just flag which series are tracked; full forecasting is expensive
                result["material_price_alerts"].append(
                    {
                        "series": series_id,
                        "category": info.get("category", ""),
                        "status": "monitored",
                    }
                )
        except Exception as e:
            logger.warning(f"Weather data collection failed for brief: {e}", exc_info=True)

        # --- Composite health score ---
        result["health_score"] = int(cpi_score * 0.40 + eac_score * 0.30 + co_score * 0.30)

        return {"cost_intelligence": result}

    except Exception as exc:
        logger.error("Cost intelligence failed: %s", exc)
        return {
            "cost_intelligence": {
                "health_score": 50,
                "warnings": [f"Cost analysis error: {exc}"],
            },
            "errors": [f"cost: {exc}"],
        }


# ---------------------------------------------------------------------------
# Sub-Agent 3: Risk Intelligence
# ---------------------------------------------------------------------------


async def risk_intelligence_node(state: WeeklyBriefState) -> dict:
    """Analyze project risks: weather, open items, schedule risk."""
    try:
        pd = state.get("project_data", {})
        address = pd.get("address", "")
        change_orders = pd.get("change_orders", [])
        rfis = pd.get("rfis", [])
        pd.get("activities", [])

        result: dict = {
            "health_score": 50,
            "top_5_risks": [],
            "weather_outlook": {},
            "open_items_summary": {},
            "emerging_critical_paths": [],
            "warnings": [],
        }

        risks: list[dict] = []

        # --- Weather ---
        weather_score = 75
        if address:
            try:
                from app.services.scheduling.weather_service import get_weather_impact

                # Check weather against sensitive activities
                sensitive_types = [
                    "concrete pour",
                    "crane operation",
                    "roofing",
                    "exterior paint",
                    "excavation",
                ]
                weather_impacts = []
                _today = date.today()
                _week_ahead = _today + timedelta(days=7)
                for activity_type in sensitive_types:
                    try:
                        impact = await get_weather_impact(
                            address,
                            _today,
                            _week_ahead,
                            activities=[{"type": activity_type}],
                        )
                        if hasattr(impact, "__dict__"):
                            impact_dict = {
                                "activity": activity_type,
                                "allowed": getattr(impact, "allowed", True),
                                "risk_level": str(getattr(impact, "risk_level", "GREEN")),
                                "reasons": getattr(impact, "reasons", []),
                            }
                        else:
                            impact_dict = {"activity": activity_type, "allowed": True}
                        weather_impacts.append(impact_dict)
                    except Exception as e:
                        logger.debug("Weather impact check failed for %s: %s", activity_type, e)
                        continue

                red_count = sum(1 for w in weather_impacts if w.get("risk_level") == "RED")
                yellow_count = sum(1 for w in weather_impacts if w.get("risk_level") == "YELLOW")

                result["weather_outlook"] = {
                    "impacts": weather_impacts,
                    "red_alerts": red_count,
                    "yellow_alerts": yellow_count,
                }

                if red_count > 0:
                    weather_score = 30
                    risks.append(
                        {
                            "description": f"Weather: {red_count} activities at RED risk this week",
                            "probability": "high",
                            "impact": "high",
                            "mitigation": "Review schedule for weather-sensitive activities; prepare contingency plans",
                        }
                    )
                elif yellow_count > 0:
                    weather_score = 60
                    risks.append(
                        {
                            "description": f"Weather: {yellow_count} activities at YELLOW risk",
                            "probability": "medium",
                            "impact": "medium",
                            "mitigation": "Monitor forecast daily; have backup indoor work ready",
                        }
                    )

            except Exception as exc:
                logger.warning("Weather check failed: %s", exc)
                result["warnings"].append(f"Weather data unavailable: {exc}")

        # --- Open items ---
        today = date.today()
        overdue_cos = [
            co
            for co in change_orders
            if co.get("status") == "pending"
            and co.get("submitted_at")
            and (today - date.fromisoformat(str(co["submitted_at"])[:10])).days > 7
        ]
        overdue_rfis = [
            rfi
            for rfi in rfis
            if rfi.get("status") in ("open", "submitted")
            and rfi.get("created_at")
            and (today - date.fromisoformat(str(rfi["created_at"])[:10])).days > 7
        ]

        result["open_items_summary"] = {
            "overdue_change_orders": len(overdue_cos),
            "overdue_rfis": len(overdue_rfis),
            "total_open_cos": sum(1 for co in change_orders if co.get("status") == "pending"),
            "total_open_rfis": sum(1 for r in rfis if r.get("status") in ("open", "submitted")),
        }

        total_overdue = len(overdue_cos) + len(overdue_rfis)
        if total_overdue == 0:
            items_score = 100
        elif total_overdue <= 3:
            items_score = 70
        elif total_overdue <= 7:
            items_score = 40
        else:
            items_score = 15

        if overdue_cos:
            risks.append(
                {
                    "description": f"{len(overdue_cos)} change orders pending > 7 days",
                    "probability": "high",
                    "impact": "medium",
                    "mitigation": "Expedite CO review; schedule decision meeting this week",
                }
            )
        if overdue_rfis:
            risks.append(
                {
                    "description": f"{len(overdue_rfis)} RFIs unanswered > 7 days",
                    "probability": "high",
                    "impact": "medium",
                    "mitigation": "Escalate to architect/engineer; track daily until resolved",
                }
            )

        # --- Schedule & cost risk (from raw EVM data in project_data) ---
        # NOTE: Cannot read schedule_intelligence/cost_intelligence here because
        # those nodes run in parallel with this one.  Use raw EVM snapshots instead.
        evm_snapshots = pd.get("evm_snapshots", [])
        latest_evm = evm_snapshots[-1] if evm_snapshots else {}
        latest_spi = float(latest_evm.get("spi", 1.0))
        latest_cpi = float(latest_evm.get("cpi", 1.0))

        schedule_risk_score = _spi_to_score(latest_spi)
        cost_risk_score = _cpi_to_score(latest_cpi)

        # --- Populate top 5 risks ---
        # Add generic risks if we don't have enough specific ones
        if len(risks) < 3:
            if latest_spi < 0.90:
                risks.append(
                    {
                        "description": f"Schedule performance below threshold (SPI = {latest_spi:.2f})",
                        "probability": "high",
                        "impact": "high",
                        "mitigation": "Evaluate critical path acceleration options; "
                        "consider overtime or additional crews",
                    }
                )
            if latest_cpi < 0.90:
                risks.append(
                    {
                        "description": f"Cost performance below threshold (CPI = {latest_cpi:.2f})",
                        "probability": "high",
                        "impact": "high",
                        "mitigation": "Conduct detailed variance analysis by CSI division; "
                        "review remaining procurement commitments",
                    }
                )

        result["top_5_risks"] = risks[:5]

        # --- Composite risk score (inverted: 100 = safe) ---
        result["health_score"] = int(
            weather_score * 0.25
            + items_score * 0.25
            + schedule_risk_score * 0.25
            + cost_risk_score * 0.25
        )

        return {"risk_intelligence": result}

    except Exception as exc:
        logger.error("Risk intelligence failed: %s", exc)
        return {
            "risk_intelligence": {
                "health_score": 50,
                "top_5_risks": [],
                "warnings": [f"Risk analysis error: {exc}"],
            },
            "errors": [f"risk: {exc}"],
        }


# ---------------------------------------------------------------------------
# Sub-Agent 4: Productivity Intelligence
# ---------------------------------------------------------------------------


async def productivity_intelligence_node(state: WeeklyBriefState) -> dict:
    """Analyze productivity: planned vs actual production rates."""
    try:
        pd = state.get("project_data", {})
        daily_logs = pd.get("daily_logs", [])
        evm_data = pd.get("latest_evm", {})

        result: dict = {
            "health_score": 50,
            "underperforming_areas": [],
            "overtime_trends": None,
            "data_source": "none",
            "warnings": [],
        }

        if daily_logs:
            # Use actual crew data
            result["data_source"] = "daily_logs"
            total_planned = 0
            total_actual = 0
            areas: dict[str, dict] = {}

            for log in daily_logs:
                planned_hrs = log.get("planned_hours", 0)
                actual_hrs = log.get("actual_hours", 0)
                total_planned += planned_hrs
                total_actual += actual_hrs

                area = log.get("area", "general")
                if area not in areas:
                    areas[area] = {"planned": 0, "actual": 0}
                areas[area]["planned"] += planned_hrs
                areas[area]["actual"] += actual_hrs

            if total_planned > 0:
                ratio = total_actual / total_planned
                result["health_score"] = min(100, max(0, int(ratio * 100)))
            else:
                result["health_score"] = 50

            # Flag underperforming areas
            for area, data in areas.items():
                if data["planned"] > 0 and data["actual"] < data["planned"] * 0.85:
                    result["underperforming_areas"].append(
                        {
                            "area": area,
                            "planned_hours": data["planned"],
                            "actual_hours": data["actual"],
                            "ratio": round(data["actual"] / data["planned"], 2),
                        }
                    )

        elif evm_data:
            # Use EVM as proxy
            result["data_source"] = "evm_proxy"
            float(evm_data.get("percent_complete", 0) or 0)
            spi = float(evm_data.get("spi", 1) or 1)

            # SPI as productivity proxy
            result["health_score"] = _spi_to_score(spi)

        else:
            result["warnings"].append("No productivity data available")

        return {"productivity_intelligence": result}

    except Exception as exc:
        logger.error("Productivity intelligence failed: %s", exc)
        return {
            "productivity_intelligence": {
                "health_score": 50,
                "warnings": [f"Productivity analysis error: {exc}"],
            },
            "errors": [f"productivity: {exc}"],
        }


# ---------------------------------------------------------------------------
# Synthesizer Node (LLM-powered)
# ---------------------------------------------------------------------------

SYNTHESIZER_PROMPT = """You are a senior project controls manager with 20+ years of commercial construction experience. Write like you're presenting at an owner's monthly progress meeting — direct, data-driven, no fluff. Use construction terminology naturally (float erosion, critical path slippage, earned value variance, etc.).

PROJECT: {project_name} ({project_type})
CONTRACT VALUE: ${contract_value:,.0f}
STATUS DATE: {report_date}
OVERALL HEALTH SCORE: {overall_health_score}/100 ({project_status})

=== SCHEDULE PERFORMANCE (Score: {schedule_score}/100) ===
{schedule_summary}

=== COST PERFORMANCE (Score: {cost_score}/100) ===
{cost_summary}

=== RISK ASSESSMENT (Score: {risk_score}/100) ===
{risk_summary}

=== PRODUCTIVITY (Score: {productivity_score}/100) ===
{productivity_summary}

Based on this data, generate a JSON object with exactly these keys:
1. "executive_summary": 3-4 sentences capturing project health at a glance. Lead with the most critical finding.
2. "action_items": Array of exactly 3 objects, each with "action" (what), "responsible" (role), "due_by" (when), "reason" (which risk/issue this addresses). Prioritize by impact.
3. "narrative_report": 1-2 pages of prose suitable for an owner's meeting. Include section headers. Be specific with numbers. Call out trends.

Respond with ONLY valid JSON, no markdown code fences."""


def _format_schedule_summary(sched: dict) -> str:
    """Format schedule intelligence for the LLM prompt."""
    lines = []
    lines.append(f"SPI Trend: {sched.get('spi_trend', 'unknown')}")
    spi_vals = sched.get("spi_values", [])
    if spi_vals:
        lines.append(f"Latest SPI: {spi_vals[-1]:.3f}")
    p50 = sched.get("p50_duration")
    p90 = sched.get("p90_duration")
    if p50:
        lines.append(f"Monte Carlo P50 Duration: {p50} days, P90: {p90} days")
    cp = sched.get("critical_path", [])
    if cp:
        lines.append(f"Critical Path: {len(cp)} activities")
    erosion = sched.get("float_erosion_alerts", [])
    if erosion:
        lines.append(f"Float Erosion: {len(erosion)} activities losing float")
    return "\n".join(lines) if lines else "No schedule data available"


def _format_cost_summary(cost: dict) -> str:
    """Format cost intelligence for the LLM prompt."""
    lines = []
    evm = cost.get("evm_metrics", {})
    if evm:
        lines.append(f"CPI: {evm.get('cpi', 'N/A')}, SPI: {evm.get('spi', 'N/A')}")
        lines.append(f"EAC: ${evm.get('eac', 'N/A')}, VAC: ${evm.get('vac', 'N/A')}")
        lines.append(f"% Complete: {evm.get('percent_complete', 'N/A')}%")
    co = cost.get("co_impact", {})
    if co.get("total_change_orders", 0) > 0:
        lines.append(
            f"Change Orders: {co['total_change_orders']} total, "
            f"{co.get('percent_of_contract', 0):.1f}% of contract"
        )
    flags = cost.get("budget_variance_flags", [])
    if flags:
        lines.append(f"Budget Overruns: {len(flags)} CSI divisions over budget")
    return "\n".join(lines) if lines else "No cost data available"


def _format_risk_summary(risk: dict) -> str:
    """Format risk intelligence for the LLM prompt."""
    lines = []
    weather = risk.get("weather_outlook", {})
    if weather:
        lines.append(
            f"Weather: {weather.get('red_alerts', 0)} RED, "
            f"{weather.get('yellow_alerts', 0)} YELLOW alerts"
        )
    items = risk.get("open_items_summary", {})
    if items:
        lines.append(
            f"Overdue Items: {items.get('overdue_change_orders', 0)} COs, "
            f"{items.get('overdue_rfis', 0)} RFIs (>7 days)"
        )
    risks = risk.get("top_5_risks", [])
    for i, r in enumerate(risks[:5], 1):
        lines.append(f"Risk {i}: {r.get('description', '')}")
    return "\n".join(lines) if lines else "No risk data available"


def _format_productivity_summary(prod: dict) -> str:
    """Format productivity intelligence for the LLM prompt."""
    lines = [f"Data Source: {prod.get('data_source', 'none')}"]
    under = prod.get("underperforming_areas", [])
    if under:
        lines.append(f"Underperforming Areas: {len(under)}")
        for area in under[:3]:
            lines.append(f"  - {area['area']}: {area['ratio']:.0%} of planned")
    return "\n".join(lines)


async def synthesizer_node(state: WeeklyBriefState) -> dict:
    """Synthesize all sub-agent outputs into executive brief via LLM."""
    try:
        pd = state.get("project_data", {})
        sched = state.get("schedule_intelligence") or {}
        cost = state.get("cost_intelligence") or {}
        risk = state.get("risk_intelligence") or {}
        prod = state.get("productivity_intelligence") or {}

        # Compute scores
        schedule_score = sched.get("health_score", 50)
        cost_score = cost.get("health_score", 50)
        risk_score_val = risk.get("health_score", 50)
        prod_score = prod.get("health_score", 50)

        overall = int(
            schedule_score * WEIGHT_SCHEDULE
            + cost_score * WEIGHT_COST
            + risk_score_val * WEIGHT_RISK
            + prod_score * WEIGHT_PRODUCTIVITY
        )
        overall = min(100, max(0, overall))

        if overall >= STATUS_GREEN_THRESHOLD:
            status = "GREEN"
        elif overall >= STATUS_YELLOW_THRESHOLD:
            status = "YELLOW"
        else:
            status = "RED"

        # Build metrics dashboard
        metrics_dashboard = {
            "scores": {
                "overall": overall,
                "schedule": schedule_score,
                "cost": cost_score,
                "risk": risk_score_val,
                "productivity": prod_score,
            },
            "status": status,
            "evm": cost.get("evm_metrics", {}),
            "schedule": {
                "p50": sched.get("p50_duration"),
                "p90": sched.get("p90_duration"),
                "spi_trend": sched.get("spi_trend"),
                "critical_path_count": len(sched.get("critical_path", [])),
                "float_erosion_count": len(sched.get("float_erosion_alerts", [])),
            },
            "risk": {
                "top_risks_count": len(risk.get("top_5_risks", [])),
                "overdue_items": risk.get("open_items_summary", {}),
            },
        }

        # Call LLM
        contract_value = float(pd.get("contract_value", 0) or 0)
        prompt = SYNTHESIZER_PROMPT.format(
            project_name=sanitize_for_prompt(
                str(pd.get("name", "Unknown Project")), max_length=200
            ),
            project_type=sanitize_for_prompt(str(pd.get("type", "commercial")), max_length=100),
            contract_value=contract_value,
            report_date=date.today().isoformat(),
            overall_health_score=overall,
            project_status=status,
            schedule_score=schedule_score,
            cost_score=cost_score,
            risk_score=risk_score_val,
            productivity_score=prod_score,
            schedule_summary=_format_schedule_summary(sched),
            cost_summary=_format_cost_summary(cost),
            risk_summary=_format_risk_summary(risk),
            productivity_summary=_format_productivity_summary(prod),
        )

        try:
            from app.services.reliability.llm_gateway import get_llm_gateway

            gateway = await get_llm_gateway()
            llm_response = await gateway.complete(
                messages=[{"role": "user", "content": prompt}],
                agent_name="weekly_brief_synthesizer",
                org_id=state.get("org_id"),
                temperature=0.3,
                max_tokens=3000,
            )

            content = llm_response.get("content", "")

            # Parse JSON from LLM output
            try:
                # Strip markdown fences if present
                clean = content.strip()
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                clean = clean.strip()
                if clean.startswith("json"):
                    clean = clean[4:].strip()

                llm_parsed = json.loads(clean)
                executive_summary = llm_parsed.get("executive_summary", "")
                action_items = llm_parsed.get("action_items", [])
                narrative_report = llm_parsed.get("narrative_report", "")
            except (json.JSONDecodeError, AttributeError):
                # Fallback: use raw content
                executive_summary = content[:500]
                action_items = []
                narrative_report = content

        except Exception as llm_exc:
            logger.warning("LLM synthesis failed, using template fallback: %s", llm_exc)
            executive_summary = (
                f"Project health score: {overall}/100 ({status}). "
                f"Schedule performance at {schedule_score}/100, "
                f"cost performance at {cost_score}/100. "
                f"Risk assessment: {risk_score_val}/100."
            )
            action_items = []
            narrative_report = "LLM synthesis unavailable. See structured data for details."

        return {
            "overall_health_score": overall,
            "project_status": status,
            "executive_summary": executive_summary,
            "action_items": action_items,
            "metrics_dashboard": metrics_dashboard,
            "narrative_report": narrative_report,
        }

    except Exception as exc:
        logger.error("Synthesizer failed: %s", exc)
        return {
            "overall_health_score": 50,
            "project_status": "YELLOW",
            "executive_summary": f"Synthesis error: {exc}",
            "action_items": [],
            "metrics_dashboard": {},
            "narrative_report": "",
            "errors": [f"synthesizer: {exc}"],
        }


# ---------------------------------------------------------------------------
# Guardrails Check Node
# ---------------------------------------------------------------------------


async def guardrails_check_node(state: WeeklyBriefState) -> dict:
    """Run confidence scoring and knowledge verification on LLM output."""
    try:
        from app.services.guardrails.confidence_scorer import ConfidenceScorer
        from app.services.guardrails.knowledge_verifier import verify

        # Build output dict for scoring
        output_for_scoring = {
            "executive_summary": state.get("executive_summary", ""),
            "overall_health_score": state.get("overall_health_score", 50),
            "project_status": state.get("project_status", "YELLOW"),
            "narrative_report": state.get("narrative_report", ""),
        }

        # Confidence scoring
        scorer = ConfidenceScorer()
        confidence_result = await scorer.score(output_for_scoring, "weekly_brief")

        # Knowledge verification
        verify_result = await verify(output_for_scoring, "weekly_brief")

        overall_confidence = confidence_result.get("overall_confidence", 0.0)
        needs_review = overall_confidence < 0.7

        guardrails = {
            "confidence_score": overall_confidence,
            "confidence_details": confidence_result.get("claim_scores", []),
            "knowledge_warnings": verify_result.get("warnings", []),
            "needs_human_review": needs_review,
            "routing": confidence_result.get("routing_recommendation", "auto_approve"),
        }

        return {"guardrails_result": guardrails, "status": "completed"}

    except Exception as exc:
        logger.warning("Guardrails check failed: %s", exc)
        return {
            "guardrails_result": {
                "confidence_score": 0.5,
                "needs_human_review": True,
                "error": str(exc),
            },
            "status": "completed",
        }


# ---------------------------------------------------------------------------
# Graph Construction
# ---------------------------------------------------------------------------


def build_weekly_brief_agent(checkpointer=None):
    """Build the weekly brief agent graph with parallel sub-agents.

    Topology::

        load_project_data ──┬──→ schedule_intelligence ──┐
                            ├──→ cost_intelligence ──────┤
                            ├──→ risk_intelligence ──────┤
                            └──→ productivity_intelligence ┘
                                                         │
                                                    synthesizer
                                                         │
                                                  guardrails_check
                                                         │
                                                        END
    """
    workflow = StateGraph(WeeklyBriefState)

    # Node names must differ from state keys to avoid LangGraph conflict
    workflow.add_node("load_data", load_project_data_node)
    workflow.add_node("analyze_schedule", schedule_intelligence_node)
    workflow.add_node("analyze_cost", cost_intelligence_node)
    workflow.add_node("analyze_risk", risk_intelligence_node)
    workflow.add_node("analyze_productivity", productivity_intelligence_node)
    workflow.add_node("synthesize", synthesizer_node)
    workflow.add_node("check_guardrails", guardrails_check_node)

    # Fan-out: load → 4 parallel sub-agents
    workflow.set_entry_point("load_data")
    workflow.add_edge("load_data", "analyze_schedule")
    workflow.add_edge("load_data", "analyze_cost")
    workflow.add_edge("load_data", "analyze_risk")
    workflow.add_edge("load_data", "analyze_productivity")

    # Fan-in: all 4 → synthesizer
    workflow.add_edge("analyze_schedule", "synthesize")
    workflow.add_edge("analyze_cost", "synthesize")
    workflow.add_edge("analyze_risk", "synthesize")
    workflow.add_edge("analyze_productivity", "synthesize")

    # Synthesizer → guardrails → END
    workflow.add_edge("synthesize", "check_guardrails")
    workflow.add_edge("check_guardrails", END)

    return workflow.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_weekly_brief(
    project_id: str,
    project_data: dict,
    org_id: str | None = None,
    generated_by: str | None = None,
) -> dict:
    """Run the complete weekly brief pipeline.

    Parameters
    ----------
    project_id: Project UUID string
    project_data: Dict with project info, EVM, schedule, COs, etc.
    org_id: Organization ID for LLM usage tracking
    generated_by: User ID who triggered the brief (None for scheduled)

    Returns
    -------
    Dict with all brief fields ready for DB storage.
    """
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_weekly_brief_agent(checkpointer=checkpointer)
    config = cast(
        RunnableConfig, {"configurable": {"thread_id": f"weekly_brief_{uuid.uuid4().hex}"}}
    )

    initial_state: WeeklyBriefState = {
        "project_id": project_id,
        "org_id": org_id or "",
        "project_data": project_data,
        "schedule_intelligence": None,
        "cost_intelligence": None,
        "risk_intelligence": None,
        "productivity_intelligence": None,
        "executive_summary": None,
        "overall_health_score": None,
        "project_status": None,
        "action_items": None,
        "metrics_dashboard": None,
        "narrative_report": None,
        "guardrails_result": None,
        "status": "processing",
        "errors": [],
    }

    try:
        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state, config=config),
            timeout=300.0,  # 5 minute timeout
        )

        return {
            "project_id": project_id,
            "generated_by": generated_by,
            "report_date": date.today().isoformat(),
            "overall_health_score": final_state.get("overall_health_score", 50),
            "project_status": final_state.get("project_status", "YELLOW"),
            "schedule_health_score": (final_state.get("schedule_intelligence") or {}).get(
                "health_score", 50
            ),
            "cost_health_score": (final_state.get("cost_intelligence") or {}).get(
                "health_score", 50
            ),
            "risk_score": (final_state.get("risk_intelligence") or {}).get("health_score", 50),
            "productivity_score": (final_state.get("productivity_intelligence") or {}).get(
                "health_score", 50
            ),
            "executive_summary": final_state.get("executive_summary", ""),
            "schedule_intelligence": final_state.get("schedule_intelligence") or {},
            "cost_intelligence": final_state.get("cost_intelligence") or {},
            "risk_intelligence": final_state.get("risk_intelligence") or {},
            "productivity_intelligence": final_state.get("productivity_intelligence") or {},
            "action_items": final_state.get("action_items") or [],
            "metrics_dashboard": final_state.get("metrics_dashboard") or {},
            "narrative_report": final_state.get("narrative_report", ""),
            "guardrails_result": final_state.get("guardrails_result") or {},
            "status": "completed",
        }

    except TimeoutError:
        logger.error("Agent timed out after 300s", extra={"agent": "weekly_brief"})
        return {
            "project_id": project_id,
            "generated_by": generated_by,
            "report_date": date.today().isoformat(),
            "overall_health_score": 0,
            "project_status": "RED",
            "executive_summary": "Weekly brief generation timed out",
            "schedule_intelligence": {},
            "cost_intelligence": {},
            "risk_intelligence": {},
            "productivity_intelligence": {},
            "action_items": [],
            "metrics_dashboard": {},
            "narrative_report": "",
            "guardrails_result": {},
            "status": "timeout",
            "error": "Agent execution timed out",
        }
    except Exception as exc:
        logger.error("Weekly brief generation failed for %s: %s", project_id, exc)
        return {
            "project_id": project_id,
            "generated_by": generated_by,
            "report_date": date.today().isoformat(),
            "overall_health_score": 0,
            "project_status": "RED",
            "executive_summary": f"Brief generation failed: {exc}",
            "schedule_intelligence": {},
            "cost_intelligence": {},
            "risk_intelligence": {},
            "productivity_intelligence": {},
            "action_items": [],
            "metrics_dashboard": {},
            "narrative_report": "",
            "guardrails_result": {},
            "status": "failed",
            "error": str(exc),
        }
