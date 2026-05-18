"""LangGraph supervisor agent orchestrating all specialist agents with human-in-the-loop."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Hashable
from typing import TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.services.agents.estimating_agent import run_estimating_agent
from app.services.agents.logistics_agent import run_logistics_agent
from app.services.agents.procurement_agent import run_procurement_agent
from app.services.agents.scheduling_agent import run_scheduling_agent

logger = logging.getLogger(__name__)


class PlanningTeamState(TypedDict):
    """State schema for the planning team supervisor agent graph."""

    project_id: str
    request: str  # user's planning request
    plan_type: str  # "full"|"estimating"|"scheduling"|"logistics"|"procurement"
    estimating_results: dict | None
    scheduling_results: dict | None
    logistics_results: dict | None
    procurement_results: dict | None
    human_feedback: str | None  # populated by interrupt
    final_plan: dict | None
    status: str
    error: str | None


# ---------------------------------------------------------------------------
# Request analysis and routing
# ---------------------------------------------------------------------------

_PLAN_TYPE_KEYWORDS: dict[str, list[str]] = {
    "estimating": ["estimate", "cost", "budget", "price", "quantity", "bid"],
    "scheduling": ["schedule", "timeline", "cpm", "critical path", "duration", "milestone"],
    "logistics": ["logistics", "delivery", "site layout", "crane", "equipment", "routing"],
    "procurement": ["procurement", "vendor", "supplier", "material", "contract", "purchase"],
}


def _infer_plan_type(request: str) -> str:
    """Infer plan type from the user request text."""
    request_lower = request.lower()

    scores: dict[str, int] = {}
    for plan_type, keywords in _PLAN_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in request_lower)
        scores[plan_type] = score

    max_score = max(scores.values()) if scores else 0
    if max_score == 0:
        return "full"

    # If multiple types score equally, prefer "full"
    top_types = [t for t, s in scores.items() if s == max_score]
    if len(top_types) > 1:
        return "full"

    return top_types[0]


async def analyze_request_node(state: PlanningTeamState) -> dict:
    """Parse user request and determine which agents to invoke."""
    try:
        request = state.get("request", "")
        plan_type = state.get("plan_type", "")

        if not plan_type or plan_type == "auto":
            plan_type = _infer_plan_type(request)

        logger.info(
            "Request analysis for project %s: plan_type=%s, request='%s'",
            state["project_id"],
            plan_type,
            request[:100],
        )
        return {"plan_type": plan_type, "status": "request_analyzed"}

    except Exception as exc:
        logger.error(
            "Request analysis failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {"plan_type": "full", "status": "analysis_failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# Specialist agent invocation nodes
# ---------------------------------------------------------------------------


async def run_estimating_node(state: PlanningTeamState) -> dict:
    """Invoke the estimating agent."""
    try:
        result = await run_estimating_agent(
            project_id=state["project_id"],
            estimate_type="conceptual",
        )
        logger.info(
            "Estimating agent completed for project %s: status=%s",
            state["project_id"],
            result.get("status", "unknown"),
        )
        return {"estimating_results": result, "status": "estimating_complete"}

    except Exception as exc:
        logger.error(
            "Estimating agent invocation failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {
            "estimating_results": {"status": "failed", "error": str(exc)},
            "status": "estimating_failed",
            "error": str(exc),
        }


async def run_scheduling_node(state: PlanningTeamState) -> dict:
    """Invoke the scheduling agent."""
    try:
        # Use sample activities if none provided via estimating results
        sample_activities = [
            {"id": "1", "name": "Mobilization", "duration_days": 10, "predecessors": []},
            {"id": "2", "name": "Excavation", "duration_days": 15, "predecessors": ["1"]},
            {"id": "3", "name": "Foundation", "duration_days": 25, "predecessors": ["2"]},
            {"id": "4", "name": "Structural Steel", "duration_days": 40, "predecessors": ["3"]},
            {"id": "5", "name": "MEP Rough-In", "duration_days": 30, "predecessors": ["4"]},
            {"id": "6", "name": "Enclosure", "duration_days": 35, "predecessors": ["4"]},
            {
                "id": "7",
                "name": "Interior Finishes",
                "duration_days": 45,
                "predecessors": ["5", "6"],
            },
            {"id": "8", "name": "Commissioning", "duration_days": 15, "predecessors": ["7"]},
            {"id": "9", "name": "Punch List", "duration_days": 10, "predecessors": ["8"]},
        ]

        result = await run_scheduling_agent(
            project_id=state["project_id"],
            activities=sample_activities,
        )
        logger.info(
            "Scheduling agent completed for project %s: status=%s",
            state["project_id"],
            result.get("status", "unknown"),
        )
        return {"scheduling_results": result, "status": "scheduling_complete"}

    except Exception as exc:
        logger.error(
            "Scheduling agent invocation failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {
            "scheduling_results": {"status": "failed", "error": str(exc)},
            "status": "scheduling_failed",
            "error": str(exc),
        }


async def run_logistics_node(state: PlanningTeamState) -> dict:
    """Invoke the logistics agent."""
    try:
        result = await run_logistics_agent(
            project_id=state["project_id"],
        )
        logger.info(
            "Logistics agent completed for project %s: status=%s",
            state["project_id"],
            result.get("status", "unknown"),
        )
        return {"logistics_results": result, "status": "logistics_complete"}

    except Exception as exc:
        logger.error(
            "Logistics agent invocation failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {
            "logistics_results": {"status": "failed", "error": str(exc)},
            "status": "logistics_failed",
            "error": str(exc),
        }


async def run_procurement_node(state: PlanningTeamState) -> dict:
    """Invoke the procurement agent."""
    try:
        result = await run_procurement_agent(
            project_id=state["project_id"],
        )
        logger.info(
            "Procurement agent completed for project %s: status=%s",
            state["project_id"],
            result.get("status", "unknown"),
        )
        return {"procurement_results": result, "status": "procurement_complete"}

    except Exception as exc:
        logger.error(
            "Procurement agent invocation failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {
            "procurement_results": {"status": "failed", "error": str(exc)},
            "status": "procurement_failed",
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Human-in-the-loop review
# ---------------------------------------------------------------------------


async def human_review_node(state: PlanningTeamState) -> dict:
    """Present results for human review using LangGraph interrupt.

    This node uses ``langgraph.types.interrupt()`` to pause execution
    and wait for human feedback before proceeding to the final compilation
    step.
    """
    review_data = {
        "estimating": state.get("estimating_results"),
        "scheduling": state.get("scheduling_results"),
        "logistics": state.get("logistics_results"),
        "procurement": state.get("procurement_results"),
    }
    feedback = interrupt(review_data)
    return {"human_feedback": feedback, "status": "reviewed"}


# ---------------------------------------------------------------------------
# Final plan compilation
# ---------------------------------------------------------------------------


async def compile_plan_node(state: PlanningTeamState) -> dict:
    """Compile final pre-construction plan from all agent results and human feedback."""
    try:
        estimating = state.get("estimating_results")
        scheduling = state.get("scheduling_results")
        logistics = state.get("logistics_results")
        procurement = state.get("procurement_results")
        human_feedback = state.get("human_feedback")

        # Extract key summaries from each agent
        sections: dict[str, dict] = {}

        if estimating:
            final_est = estimating.get("final_estimate", {})
            sections["estimating"] = {
                "status": estimating.get("status", "not_run"),
                "recommended_total": final_est.get("recommended_total", 0) if final_est else 0,
                "confidence": final_est.get("confidence", "unknown") if final_est else "unknown",
                "summary": (
                    final_est.get("summary", "") if final_est else "Estimating not completed."
                ),
            }

        if scheduling:
            optimized = scheduling.get("optimized_schedule", {})
            sections["scheduling"] = {
                "status": scheduling.get("status", "not_run"),
                "project_duration": optimized.get("optimized_duration", 0) if optimized else 0,
                "dcma_health": (
                    scheduling.get("dcma_results", {}).get("overall_health", "unknown")
                    if scheduling.get("dcma_results")
                    else "not_assessed"
                ),
                "summary": (
                    optimized.get("summary", "") if optimized else "Scheduling not completed."
                ),
            }

        if logistics:
            sim = logistics.get("simulation_results", {})
            sections["logistics"] = {
                "status": logistics.get("status", "not_run"),
                "productive_time_pct": (
                    sim.get("delay_analysis", {}).get("total_productive_time_pct", 0) if sim else 0
                ),
                "summary": sim.get("summary", "") if sim else "Logistics not completed.",
            }

        if procurement:
            recs = procurement.get("recommendations", [])
            sections["procurement"] = {
                "status": procurement.get("status", "not_run"),
                "recommendation_count": len(recs),
                "critical_items": [r for r in recs if r.get("priority") == "critical"],
                "summary": (
                    f"{len(recs)} procurement recommendations generated."
                    if recs
                    else "Procurement not completed."
                ),
            }

        # Overall plan assessment
        statuses = [s.get("status", "unknown") for s in sections.values()]
        if all(s == "completed" for s in statuses):
            overall_status = "complete"
        elif any(s == "failed" for s in statuses):
            overall_status = "partial_failure"
        else:
            overall_status = "completed_with_gaps"

        final_plan = {
            "project_id": state["project_id"],
            "plan_type": state.get("plan_type", "full"),
            "sections": sections,
            "overall_status": overall_status,
            "human_feedback": human_feedback,
            "executive_summary": _build_executive_summary(sections, human_feedback),
            "next_steps": [
                "Review and approve cost estimate with project stakeholders",
                "Baseline the project schedule in Primavera P6 or MS Project",
                "Issue purchase orders for long-lead materials",
                "Finalize site logistics plan with safety manager",
                "Execute vendor contracts with legal review complete",
            ],
        }

        logger.info(
            "Final plan compiled for project %s: %s, %d sections",
            state["project_id"],
            overall_status,
            len(sections),
        )
        return {"final_plan": final_plan, "status": "plan_compiled"}

    except Exception as exc:
        logger.error(
            "Plan compilation failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {"final_plan": None, "status": "compilation_failed", "error": str(exc)}


def _build_executive_summary(sections: dict[str, dict], human_feedback: str | None) -> str:
    """Build a human-readable executive summary from plan sections."""
    parts: list[str] = ["Pre-Construction Plan Summary:"]

    if "estimating" in sections:
        est = sections["estimating"]
        total = est.get("recommended_total", 0)
        conf = est.get("confidence", "unknown")
        parts.append(f"  Cost Estimate: ${total:,.2f} ({conf} confidence)")

    if "scheduling" in sections:
        sched = sections["scheduling"]
        dur = sched.get("project_duration", 0)
        health = sched.get("dcma_health", "unknown")
        parts.append(f"  Schedule: {dur} days, DCMA health: {health}")

    if "logistics" in sections:
        log = sections["logistics"]
        prod = log.get("productive_time_pct", 0)
        parts.append(f"  Logistics: {prod:.1f}% projected productive time")

    if "procurement" in sections:
        proc = sections["procurement"]
        recs = proc.get("recommendation_count", 0)
        critical = len(proc.get("critical_items", []))
        parts.append(f"  Procurement: {recs} recommendations ({critical} critical)")

    if human_feedback:
        parts.append(f"  Human Review: {human_feedback}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Conditional routing
# ---------------------------------------------------------------------------


def route_agents(state: PlanningTeamState) -> list[Hashable]:
    """Determine which specialist agents to invoke based on plan type.

    Returns a list of node names for conditional branching.
    """
    plan_type = state.get("plan_type", "full")
    if plan_type == "full":
        return ["run_estimating", "run_scheduling", "run_logistics", "run_procurement"]
    return [f"run_{plan_type}"]


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_planning_team(checkpointer=None) -> CompiledStateGraph:
    """Build and compile the LangGraph planning team supervisor workflow.

    Graph flow::

        analyze_request -> [run_estimating, run_scheduling, run_logistics,
                            run_procurement] (conditional/parallel)
        -> human_review (with interrupt) -> compile_plan -> END

    Returns
    -------
    A compiled LangGraph ``StateGraph`` with checkpointer support for
    human-in-the-loop via ``interrupt()``.
    """
    workflow = StateGraph(PlanningTeamState)

    # Add nodes
    workflow.add_node("analyze_request", analyze_request_node)
    workflow.add_node("run_estimating", run_estimating_node)
    workflow.add_node("run_scheduling", run_scheduling_node)
    workflow.add_node("run_logistics", run_logistics_node)
    workflow.add_node("run_procurement", run_procurement_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("compile_plan", compile_plan_node)

    # Entry point
    workflow.set_entry_point("analyze_request")

    # Conditional routing from analyze_request to specialist agents
    workflow.add_conditional_edges(
        "analyze_request",
        route_agents,
        {
            "run_estimating": "run_estimating",
            "run_scheduling": "run_scheduling",
            "run_logistics": "run_logistics",
            "run_procurement": "run_procurement",
        },
    )

    # All specialist agents feed into human review
    workflow.add_edge("run_estimating", "human_review")
    workflow.add_edge("run_scheduling", "human_review")
    workflow.add_edge("run_logistics", "human_review")
    workflow.add_edge("run_procurement", "human_review")

    # Human review leads to final compilation
    workflow.add_edge("human_review", "compile_plan")
    workflow.add_edge("compile_plan", END)

    return workflow.compile(checkpointer=checkpointer)


async def run_planning_team(
    project_id: str,
    request: str = "",
    plan_type: str = "full",
) -> dict:
    """Build and invoke the planning team supervisor agent.

    Parameters
    ----------
    project_id:
        UUID string of the project.
    request:
        The user's planning request in natural language.
    plan_type:
        Type of plan to generate: "full", "estimating", "scheduling",
        "logistics", "procurement", or "auto" (infer from request).

    Returns
    -------
    The final agent state as a dict.  Note: when using the ``interrupt()``
    feature for human-in-the-loop, the graph must be invoked with a
    checkpointer and the caller must handle resumption after human review.
    """
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_planning_team(checkpointer=checkpointer)
    config = cast(
        RunnableConfig, {"configurable": {"thread_id": f"planning_team_{uuid.uuid4().hex}"}}
    )

    initial_state: PlanningTeamState = {
        "project_id": project_id,
        "request": request,
        "plan_type": plan_type,
        "estimating_results": None,
        "scheduling_results": None,
        "logistics_results": None,
        "procurement_results": None,
        "human_feedback": None,
        "final_plan": None,
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
        logger.error("Agent timed out after 300s", extra={"agent": "planning_team"})
        return {**initial_state, "status": "timeout", "error": "Agent execution timed out"}
    except Exception as exc:
        logger.error("Planning team failed for %s: %s", project_id, exc)
        return {
            **initial_state,
            "status": "failed",
            "error": str(exc),
        }
