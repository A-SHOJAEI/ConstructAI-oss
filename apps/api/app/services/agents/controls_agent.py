"""LangGraph agent for project controls workflow."""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal
from typing import TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.services.controls.eac_forecaster import forecast_eac
from app.services.controls.evm_engine import (
    compute_evm_snapshot,
)
from app.services.controls.monte_carlo_schedule import (
    run_schedule_risk_simulation,
)
from app.services.controls.scurve_generator import (
    generate_scurve_data,
)

logger = logging.getLogger(__name__)


class ControlsAgentState(TypedDict):
    """State schema for the project controls agent."""

    project_id: str
    bac: str  # Decimal as string for serialization
    pv: str
    ev: str
    ac: str
    activities: list[dict]
    evm_results: dict | None
    eac_results: dict | None
    risk_results: dict | None
    scurve_results: dict | None
    status: str
    error: str | None


async def compute_evm_node(
    state: ControlsAgentState,
) -> dict:
    """Compute EVM metrics from base values."""
    try:
        metrics = await compute_evm_snapshot(
            bac=Decimal(state["bac"]),
            pv=Decimal(state["pv"]),
            ev=Decimal(state["ev"]),
            ac=Decimal(state["ac"]),
        )
        return {
            "evm_results": metrics,
            "status": "evm_computed",
        }
    except Exception as exc:
        logger.error("EVM computation failed: %s", exc)
        return {
            "evm_results": None,
            "status": "evm_failed",
            "error": str(exc),
        }


async def forecast_eac_node(
    state: ControlsAgentState,
) -> dict:
    """Run EAC forecasting using multiple methods."""
    try:
        evm = state.get("evm_results", {})
        if not evm:
            return {
                "eac_results": None,
                "status": "eac_skipped",
            }

        results = {}
        for method in ("cpi", "spi_cpi", "remaining_work"):
            result = await forecast_eac(
                bac=Decimal(state["bac"]),
                ev=Decimal(state["ev"]),
                ac=Decimal(state["ac"]),
                spi=Decimal(str(evm.get("spi", 1))),
                cpi=Decimal(str(evm.get("cpi", 1))),
                method=method,
            )
            results[method] = result

        return {
            "eac_results": results,
            "status": "eac_computed",
        }
    except Exception as exc:
        logger.error("EAC forecasting failed: %s", exc)
        return {
            "eac_results": None,
            "status": "eac_failed",
            "error": str(exc),
        }


async def risk_simulation_node(
    state: ControlsAgentState,
) -> dict:
    """Run Monte Carlo schedule risk simulation."""
    try:
        activities = state.get("activities", [])
        if not activities:
            return {
                "risk_results": None,
                "status": "risk_skipped",
            }

        result = await run_schedule_risk_simulation(
            activities=activities,
            num_iterations=1000,
            seed=42,
        )
        return {
            "risk_results": result,
            "status": "risk_computed",
        }
    except Exception as exc:
        logger.error("Risk simulation failed: %s", exc)
        return {
            "risk_results": None,
            "status": "risk_failed",
            "error": str(exc),
        }


async def scurve_node(
    state: ControlsAgentState,
) -> dict:
    """Generate S-Curve data."""
    try:
        from datetime import date

        evm = state.get("evm_results") or {}
        snapshot = {
            "snapshot_date": date.today().isoformat(),
            "pv": state["pv"],
            "ev": state["ev"],
            "ac": state["ac"],
            "spi": str(evm.get("spi", 1)),
        }
        result = await generate_scurve_data(
            snapshots=[snapshot],
            bac=Decimal(state["bac"]),
            start_date=date.today(),
        )
        return {
            "scurve_results": {
                "data_points_count": len(result.get("data_points", [])),
                "forecast_completion": str(result.get("forecast_completion")),
            },
            "status": "scurve_generated",
        }
    except Exception as exc:
        logger.error("S-Curve generation failed: %s", exc)
        return {
            "scurve_results": None,
            "status": "scurve_failed",
            "error": str(exc),
        }


def build_controls_agent(checkpointer=None) -> CompiledStateGraph:
    """Build the project controls agent graph.

    Flow: compute_evm -> forecast_eac -> risk_simulation
          -> scurve -> END
    """
    workflow = StateGraph(ControlsAgentState)

    workflow.add_node("compute_evm", compute_evm_node)
    workflow.add_node("forecast_eac", forecast_eac_node)
    workflow.add_node(
        "risk_simulation",
        risk_simulation_node,
    )
    workflow.add_node("scurve", scurve_node)

    workflow.set_entry_point("compute_evm")
    workflow.add_edge("compute_evm", "forecast_eac")
    workflow.add_edge("forecast_eac", "risk_simulation")
    workflow.add_edge("risk_simulation", "scurve")
    workflow.add_edge("scurve", END)

    return workflow.compile(checkpointer=checkpointer)


async def run_controls_agent(
    project_id: str,
    bac: Decimal = Decimal("1000000"),
    pv: Decimal = Decimal("500000"),
    ev: Decimal = Decimal("450000"),
    ac: Decimal = Decimal("480000"),
    activities: list[dict] | None = None,
) -> dict:
    """Run the project controls agent.

    Parameters
    ----------
    project_id: Project UUID string
    bac: Budget at Completion
    pv: Planned Value
    ev: Earned Value
    ac: Actual Cost
    activities: Schedule activities for risk simulation
    """
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_controls_agent(checkpointer=checkpointer)
    config = cast(RunnableConfig, {"configurable": {"thread_id": f"controls_{uuid.uuid4().hex}"}})

    if activities is None:
        activities = [
            {
                "id": "1",
                "name": "Foundation",
                "duration_days": 30,
                "predecessors": [],
            },
            {
                "id": "2",
                "name": "Structure",
                "duration_days": 60,
                "predecessors": ["1"],
            },
            {
                "id": "3",
                "name": "Finishes",
                "duration_days": 45,
                "predecessors": ["2"],
            },
        ]

    initial_state: ControlsAgentState = {
        "project_id": project_id,
        "bac": str(bac),
        "pv": str(pv),
        "ev": str(ev),
        "ac": str(ac),
        "activities": activities,
        "evm_results": None,
        "eac_results": None,
        "risk_results": None,
        "scurve_results": None,
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
        logger.error("Agent timed out after 300s", extra={"agent": "controls"})
        return {**initial_state, "status": "timeout", "error": "Agent execution timed out"}
    except Exception as exc:
        logger.error(
            "Controls agent failed for %s: %s",
            project_id,
            exc,
        )
        return {
            **initial_state,
            "status": "failed",
            "error": str(exc),
        }
