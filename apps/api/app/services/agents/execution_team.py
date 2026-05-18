"""LangGraph supervisor for the Execution Team."""

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

from app.services.agents.controls_agent import (
    run_controls_agent,
)
from app.services.agents.productivity_agent import (
    run_productivity_agent,
)

logger = logging.getLogger(__name__)


class ExecutionTeamState(TypedDict):
    """State for the Execution Team supervisor."""

    project_id: str
    request: str
    task_type: str
    controls_results: dict | None
    productivity_results: dict | None
    human_feedback: str | None
    final_report: dict | None
    status: str
    error: str | None


_TASK_KEYWORDS: dict[str, list[str]] = {
    "controls": [
        "evm",
        "earned value",
        "cost",
        "budget",
        "change order",
        "forecast",
        "s-curve",
        "variance",
    ],
    "productivity": [
        "productivity",
        "crew",
        "equipment",
        "telemetry",
        "activity",
        "utilization",
        "forecast",
    ],
}


def _infer_task_type(request: str) -> str:
    """Infer task type from request text."""
    lower = request.lower()
    scores: dict[str, int] = {}
    for task_type, keywords in _TASK_KEYWORDS.items():
        scores[task_type] = sum(1 for kw in keywords if kw in lower)
    max_score = max(scores.values()) if scores else 0
    if max_score == 0:
        return "full"
    top = [t for t, s in scores.items() if s == max_score]
    return top[0] if len(top) == 1 else "full"


async def analyze_node(
    state: ExecutionTeamState,
) -> dict:
    """Analyze request and determine routing."""
    request = state.get("request", "")
    task_type = state.get("task_type", "")
    if not task_type or task_type == "auto":
        task_type = _infer_task_type(request)
    return {"task_type": task_type, "status": "analyzed"}


async def run_controls_node(
    state: ExecutionTeamState,
) -> dict:
    """Invoke the controls agent."""
    try:
        result = await run_controls_agent(
            project_id=state["project_id"],
        )
        return {
            "controls_results": result,
            "status": "controls_complete",
        }
    except Exception as exc:
        logger.error("Controls agent failed: %s", exc)
        return {
            "controls_results": {
                "status": "failed",
                "error": str(exc),
            },
            "status": "controls_failed",
            "error": str(exc),
        }


async def run_productivity_node(
    state: ExecutionTeamState,
) -> dict:
    """Invoke the productivity agent."""
    try:
        result = await run_productivity_agent(
            project_id=state["project_id"],
        )
        return {
            "productivity_results": result,
            "status": "productivity_complete",
        }
    except Exception as exc:
        logger.error(
            "Productivity agent failed: %s",
            exc,
        )
        return {
            "productivity_results": {
                "status": "failed",
                "error": str(exc),
            },
            "status": "productivity_failed",
            "error": str(exc),
        }


async def human_review_node(
    state: ExecutionTeamState,
) -> dict:
    """Present results for human review."""
    review_data = {
        "controls": state.get("controls_results"),
        "productivity": state.get(
            "productivity_results",
        ),
    }
    feedback = interrupt(review_data)
    return {
        "human_feedback": feedback,
        "status": "reviewed",
    }


async def compile_report_node(
    state: ExecutionTeamState,
) -> dict:
    """Compile final execution report."""
    try:
        report = {
            "project_id": state["project_id"],
            "task_type": state.get("task_type", "full"),
            "controls": state.get("controls_results"),
            "productivity": state.get(
                "productivity_results",
            ),
            "human_feedback": state.get(
                "human_feedback",
            ),
            "summary": ("Execution team analysis complete."),
        }
        return {
            "final_report": report,
            "status": "report_compiled",
        }
    except Exception as exc:
        logger.error(
            "Report compilation failed: %s",
            exc,
        )
        return {
            "final_report": None,
            "status": "compilation_failed",
            "error": str(exc),
        }


def route_agents(
    state: ExecutionTeamState,
) -> list[Hashable]:
    """Route to appropriate agents."""
    task_type = state.get("task_type", "full")
    if task_type == "full":
        return ["run_controls", "run_productivity"]
    return [f"run_{task_type}"]


def build_execution_team(checkpointer=None) -> CompiledStateGraph:
    """Build the Execution Team supervisor graph."""
    workflow = StateGraph(ExecutionTeamState)

    workflow.add_node("analyze", analyze_node)
    workflow.add_node(
        "run_controls",
        run_controls_node,
    )
    workflow.add_node(
        "run_productivity",
        run_productivity_node,
    )
    workflow.add_node(
        "human_review",
        human_review_node,
    )
    workflow.add_node(
        "compile_report",
        compile_report_node,
    )

    workflow.set_entry_point("analyze")
    workflow.add_conditional_edges(
        "analyze",
        route_agents,
        {
            "run_controls": "run_controls",
            "run_productivity": "run_productivity",
        },
    )
    workflow.add_edge("run_controls", "human_review")
    workflow.add_edge(
        "run_productivity",
        "human_review",
    )
    workflow.add_edge("human_review", "compile_report")
    workflow.add_edge("compile_report", END)

    return workflow.compile(checkpointer=checkpointer)


async def run_execution_team(
    project_id: str,
    request: str = "",
    task_type: str = "full",
) -> dict:
    """Run the Execution Team supervisor."""
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_execution_team(checkpointer=checkpointer)
    config = cast(
        RunnableConfig, {"configurable": {"thread_id": f"execution_team_{uuid.uuid4().hex}"}}
    )

    initial_state: ExecutionTeamState = {
        "project_id": project_id,
        "request": request,
        "task_type": task_type,
        "controls_results": None,
        "productivity_results": None,
        "human_feedback": None,
        "final_report": None,
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
        logger.error("Agent timed out after 300s", extra={"agent": "execution_team"})
        return {**initial_state, "status": "timeout", "error": "Agent execution timed out"}
    except Exception as exc:
        logger.error(
            "Execution team failed for %s: %s",
            project_id,
            exc,
        )
        return {
            **initial_state,
            "status": "failed",
            "error": str(exc),
        }
