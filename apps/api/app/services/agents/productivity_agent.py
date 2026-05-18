"""LangGraph agent for productivity analysis workflow."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.services.productivity.activity_recognizer import (
    ActivityRecognizer,
)
from app.services.productivity.productivity_forecaster import (
    forecast_productivity,
)

logger = logging.getLogger(__name__)

_recognizer = ActivityRecognizer()


class ProductivityAgentState(TypedDict):
    """State schema for the productivity agent."""

    project_id: str
    frames: list
    historical_data: list[dict]
    trade: str
    telemetry_data: list[dict]
    activity_results: dict | None
    forecast_results: dict | None
    equipment_analysis: dict | None
    status: str
    error: str | None


async def recognize_activity_node(
    state: ProductivityAgentState,
) -> dict:
    """Recognize activities from video frames."""
    try:
        frames = state.get("frames", [])
        if not frames:
            return {
                "activity_results": {
                    "activity_type": "unknown",
                    "confidence": 0.0,
                },
                "status": "no_frames",
            }

        result = await _recognizer.recognize(
            frames=frames,
            camera_id="default",
        )
        return {
            "activity_results": result,
            "status": "activity_recognized",
        }
    except Exception as exc:
        logger.error(
            "Activity recognition failed: %s",
            exc,
        )
        return {
            "activity_results": None,
            "status": "recognition_failed",
            "error": str(exc),
        }


async def forecast_node(
    state: ProductivityAgentState,
) -> dict:
    """Forecast productivity trends."""
    try:
        historical = state.get("historical_data", [])
        trade = state.get("trade", "general")

        result = await forecast_productivity(
            historical_data=historical,
            trade=trade,
            forecast_days=14,
        )
        return {
            "forecast_results": result,
            "status": "forecast_complete",
        }
    except Exception as exc:
        logger.error(
            "Productivity forecast failed: %s",
            exc,
        )
        return {
            "forecast_results": None,
            "status": "forecast_failed",
            "error": str(exc),
        }


async def analyze_equipment_node(
    state: ProductivityAgentState,
) -> dict:
    """Analyze equipment telemetry data."""
    try:
        telemetry = state.get("telemetry_data", [])
        if not telemetry:
            return {
                "equipment_analysis": {
                    "summary": "No telemetry data",
                },
                "status": "no_telemetry",
            }

        # Aggregate utilization metrics
        total_engine = sum(float(t.get("engine_hours", 0) or 0) for t in telemetry)
        total_idle = sum(float(t.get("idle_time_hours", 0) or 0) for t in telemetry)
        total_fuel = sum(float(t.get("fuel_consumption", 0) or 0) for t in telemetry)

        active = total_engine - total_idle
        util_pct = (active / total_engine * 100) if total_engine > 0 else 0

        equip_ids = {t.get("equipment_id") for t in telemetry}

        analysis = {
            "total_engine_hours": round(
                total_engine,
                2,
            ),
            "total_idle_hours": round(total_idle, 2),
            "total_fuel_consumption": round(
                total_fuel,
                2,
            ),
            "utilization_pct": round(util_pct, 1),
            "equipment_count": len(equip_ids),
            "summary": (f"Fleet utilization: {util_pct:.1f}%, {total_engine:.0f} engine hours"),
        }

        return {
            "equipment_analysis": analysis,
            "status": "equipment_analyzed",
        }
    except Exception as exc:
        logger.error(
            "Equipment analysis failed: %s",
            exc,
        )
        return {
            "equipment_analysis": None,
            "status": "equipment_failed",
            "error": str(exc),
        }


def build_productivity_agent(checkpointer=None) -> CompiledStateGraph:
    """Build the productivity agent graph.

    Flow: recognize_activity -> forecast
          -> analyze_equipment -> END
    """
    workflow = StateGraph(ProductivityAgentState)

    workflow.add_node(
        "recognize_activity",
        recognize_activity_node,
    )
    workflow.add_node("forecast", forecast_node)
    workflow.add_node(
        "analyze_equipment",
        analyze_equipment_node,
    )

    workflow.set_entry_point("recognize_activity")
    workflow.add_edge("recognize_activity", "forecast")
    workflow.add_edge("forecast", "analyze_equipment")
    workflow.add_edge("analyze_equipment", END)

    return workflow.compile(checkpointer=checkpointer)


async def run_productivity_agent(
    project_id: str,
    frames: list | None = None,
    historical_data: list[dict] | None = None,
    trade: str = "general",
    telemetry_data: list[dict] | None = None,
) -> dict:
    """Run the productivity analysis agent."""
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_productivity_agent(checkpointer=checkpointer)
    config = cast(
        RunnableConfig, {"configurable": {"thread_id": f"productivity_{uuid.uuid4().hex}"}}
    )

    initial_state: ProductivityAgentState = {
        "project_id": project_id,
        "frames": frames or [],
        "historical_data": historical_data or [],
        "trade": trade,
        "telemetry_data": telemetry_data or [],
        "activity_results": None,
        "forecast_results": None,
        "equipment_analysis": None,
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
        logger.error("Agent timed out after 300s", extra={"agent": "productivity"})
        return {**initial_state, "status": "timeout", "error": "Agent execution timed out"}
    except Exception as exc:
        logger.error(
            "Productivity agent failed for %s: %s",
            project_id,
            exc,
        )
        return {
            **initial_state,
            "status": "failed",
            "error": str(exc),
        }
