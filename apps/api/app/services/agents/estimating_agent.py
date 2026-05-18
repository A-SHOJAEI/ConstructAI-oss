"""LangGraph agent for construction cost estimation workflow."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, NotRequired, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.services.estimating.cost_database import match_costs
from app.services.estimating.monte_carlo import run_monte_carlo
from app.services.estimating.parametric_model import predict_cost
from app.services.estimating.quantity_extractor import (
    extract_quantities_from_document,
    extract_quantities_from_ifc,
)

logger = logging.getLogger(__name__)


class EstimatingAgentState(TypedDict):
    """State schema for the cost estimation agent graph."""

    project_id: str
    estimate_type: str  # conceptual, schematic, detailed
    documents: list  # document data for quantity extraction
    quantities: list  # extracted quantities
    cost_matches: list  # matched costs
    parametric_estimate: dict | None
    monte_carlo_results: dict | None
    final_estimate: dict | None
    status: str
    error: str | None
    # Optional keys populated by callers before invoking the graph.
    org_id: NotRequired[str]
    input_data: NotRequired[dict[str, Any]]


async def extract_quantities_node(state: EstimatingAgentState) -> dict:
    """Extract quantities from project documents and BIM data."""
    try:
        all_quantities: list[dict] = []

        for doc in state.get("documents", []):
            doc_type = doc.get("type", "document")

            if doc_type == "ifc":
                quantities = await extract_quantities_from_ifc(doc.get("data", {}))
            else:
                quantities = await extract_quantities_from_document(
                    text_content=doc.get("text_content", ""),
                    filename=doc.get("filename", "unknown"),
                )

            all_quantities.extend(quantities)

        logger.info(
            "Extracted %d quantities for project %s",
            len(all_quantities),
            state["project_id"],
        )
        return {"quantities": all_quantities, "status": "quantities_extracted"}

    except Exception as exc:
        logger.error(
            "Quantity extraction failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {"quantities": [], "status": "extraction_failed", "error": str(exc)}


async def match_costs_node(state: EstimatingAgentState) -> dict:
    """Match extracted quantities to cost database entries."""
    try:
        quantities = state.get("quantities", [])
        if not quantities:
            logger.warning("No quantities to match for project %s", state["project_id"])
            return {"cost_matches": [], "status": "no_quantities"}

        cost_matches = await match_costs(quantities, region="national")

        logger.info(
            "Matched %d cost items for project %s",
            len(cost_matches),
            state["project_id"],
        )
        return {"cost_matches": cost_matches, "status": "costs_matched"}

    except Exception as exc:
        logger.error(
            "Cost matching failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {"cost_matches": [], "status": "matching_failed", "error": str(exc)}


async def run_parametric_node(state: EstimatingAgentState) -> dict:
    """Run parametric cost model for comparison estimate."""
    try:
        # Build project parameters from available data
        cost_matches = state.get("cost_matches", [])
        total_line_cost = sum(item.get("total_cost", 0) for item in cost_matches)

        # Derive project parameters from available data, fall back to defaults
        input_data = state.get("input_data", {}) or {}
        project_params = {
            "sqft": input_data.get("sqft", 50000),
            "stories": input_data.get("stories", 3),
            "type": input_data.get("project_type", "commercial"),
            "region": input_data.get("region", "national"),
            "quality_level": input_data.get("quality_level", "standard"),
        }
        if not input_data.get("sqft"):
            logger.warning(
                "No sqft in input_data for project %s — using default 50,000",
                state["project_id"],
            )

        parametric = await predict_cost(project_params)

        logger.info(
            "Parametric estimate for project %s: $%.2f (line items total: $%.2f)",
            state["project_id"],
            parametric.get("total_predicted_cost", 0),
            total_line_cost,
        )
        return {"parametric_estimate": parametric, "status": "parametric_complete"}

    except Exception as exc:
        logger.error(
            "Parametric model failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {
            "parametric_estimate": None,
            "status": "parametric_failed",
            "error": str(exc),
        }


async def run_monte_carlo_node(state: EstimatingAgentState) -> dict:
    """Run Monte Carlo simulation for risk-adjusted estimate."""
    try:
        cost_matches = state.get("cost_matches", [])
        if not cost_matches:
            logger.warning("No cost data for Monte Carlo on project %s", state["project_id"])
            return {"monte_carlo_results": None, "status": "no_cost_data"}

        mc_results = await run_monte_carlo(
            line_items=cost_matches,
            num_simulations=10000,
            contingency_pct=10.0,
            org_id=state.get("org_id"),
        )

        logger.info(
            "Monte Carlo complete for project %s: P50=$%.2f, P90=$%.2f",
            state["project_id"],
            mc_results.get("p50", 0),
            mc_results.get("p90", 0),
        )
        return {"monte_carlo_results": mc_results, "status": "monte_carlo_complete"}

    except Exception as exc:
        logger.error(
            "Monte Carlo failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {
            "monte_carlo_results": None,
            "status": "monte_carlo_failed",
            "error": str(exc),
        }


async def compile_estimate_node(state: EstimatingAgentState) -> dict:
    """Compile all estimation results into a final estimate."""
    try:
        cost_matches = state.get("cost_matches", [])
        parametric = state.get("parametric_estimate")
        mc_results = state.get("monte_carlo_results")

        # Line-item total
        line_item_total = sum(item.get("total_cost", 0) for item in cost_matches)

        # Parametric comparison
        parametric_total = parametric.get("total_predicted_cost", 0) if parametric else 0

        # Monte Carlo risk-adjusted values
        mc_p50 = mc_results.get("p50", 0) if mc_results else 0
        mc_p90 = mc_results.get("p90", 0) if mc_results else 0
        mc_mean = mc_results.get("mean", 0) if mc_results else 0

        # Recommended estimate: use P50 from Monte Carlo if available, else line item total
        recommended_total = mc_p50 if mc_p50 > 0 else line_item_total

        # Confidence assessment
        if parametric_total > 0 and line_item_total > 0:
            variance_pct = abs(parametric_total - line_item_total) / line_item_total * 100
            if variance_pct < 10:
                confidence = "high"
            elif variance_pct < 25:
                confidence = "medium"
            else:
                confidence = "low"
        else:
            confidence = "medium"

        final_estimate = {
            "project_id": state["project_id"],
            "estimate_type": state.get("estimate_type", "conceptual"),
            "line_item_total": round(line_item_total, 2),
            "line_item_count": len(cost_matches),
            "parametric_total": round(parametric_total, 2),
            "monte_carlo": {
                "p50": round(mc_p50, 2),
                "p90": round(mc_p90, 2),
                "mean": round(mc_mean, 2),
            }
            if mc_results
            else None,
            "recommended_total": round(recommended_total, 2),
            "confidence": confidence,
            "summary": (
                f"Estimate for project {state['project_id']}: "
                f"Line items ${line_item_total:,.2f}, "
                f"Parametric ${parametric_total:,.2f}, "
                f"Monte Carlo P50 ${mc_p50:,.2f} / P90 ${mc_p90:,.2f}. "
                f"Recommended: ${recommended_total:,.2f} ({confidence} confidence)."
            ),
        }

        logger.info(
            "Final estimate compiled for project %s: $%.2f (%s confidence)",
            state["project_id"],
            recommended_total,
            confidence,
        )
        return {"final_estimate": final_estimate, "status": "completed"}

    except Exception as exc:
        logger.error(
            "Estimate compilation failed for project %s: %s",
            state["project_id"],
            exc,
        )
        return {
            "final_estimate": None,
            "status": "compilation_failed",
            "error": str(exc),
        }


def build_estimating_agent(checkpointer=None) -> CompiledStateGraph:
    """Build and compile the LangGraph estimating workflow.

    Graph flow::

        extract_quantities -> match_costs -> run_parametric
        -> run_monte_carlo -> compile_estimate -> END

    Returns
    -------
    A compiled LangGraph ``StateGraph``.
    """
    workflow = StateGraph(EstimatingAgentState)

    workflow.add_node("extract_quantities", extract_quantities_node)
    workflow.add_node("match_costs", match_costs_node)
    workflow.add_node("run_parametric", run_parametric_node)
    workflow.add_node("run_monte_carlo", run_monte_carlo_node)
    workflow.add_node("compile_estimate", compile_estimate_node)

    workflow.set_entry_point("extract_quantities")
    workflow.add_edge("extract_quantities", "match_costs")
    workflow.add_edge("match_costs", "run_parametric")
    workflow.add_edge("run_parametric", "run_monte_carlo")
    workflow.add_edge("run_monte_carlo", "compile_estimate")
    workflow.add_edge("compile_estimate", END)

    return workflow.compile(checkpointer=checkpointer)


async def run_estimating_agent(
    project_id: str,
    estimate_type: str = "conceptual",
    documents: list | None = None,
) -> dict:
    """Build and invoke the estimating agent.

    Parameters
    ----------
    project_id:
        UUID string of the project being estimated.
    estimate_type:
        Type of estimate: "conceptual", "schematic", or "detailed".
    documents:
        List of document dicts for quantity extraction. Each dict should
        have ``type`` ("ifc" or "document"), and either ``data`` (for IFC)
        or ``text_content`` and ``filename`` (for documents).

    Returns
    -------
    The final agent state as a dict containing quantities, cost matches,
    parametric estimate, Monte Carlo results, final estimate, status, and
    any error information.
    """
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_estimating_agent(checkpointer=checkpointer)
    config = cast(RunnableConfig, {"configurable": {"thread_id": f"estimating_{uuid.uuid4().hex}"}})

    initial_state: EstimatingAgentState = {
        "project_id": project_id,
        "estimate_type": estimate_type,
        "documents": documents or [],
        "quantities": [],
        "cost_matches": [],
        "parametric_estimate": None,
        "monte_carlo_results": None,
        "final_estimate": None,
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
        logger.error("Agent timed out after 300s", extra={"agent": "estimating"})
        return {**initial_state, "status": "timeout", "error": "Agent execution timed out"}
    except Exception as exc:
        logger.error("Estimating agent failed for %s: %s", project_id, exc)
        return {
            **initial_state,
            "status": "failed",
            "error": str(exc),
        }
