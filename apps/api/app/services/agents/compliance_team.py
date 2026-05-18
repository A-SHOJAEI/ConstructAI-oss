"""LangGraph supervisor for the Compliance Team."""

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

from app.services.agents.communication_agent import (
    run_communication_agent,
)
from app.services.agents.quality_agent import (
    run_quality_agent,
)

logger = logging.getLogger(__name__)


class ComplianceTeamState(TypedDict):
    """State for the Compliance Team supervisor."""

    project_id: str
    request: str
    task_type: str
    quality_results: dict | None
    communication_results: dict | None
    human_feedback: str | None
    final_report: dict | None
    status: str
    error: str | None


_TASK_KEYWORDS: dict[str, list[str]] = {
    "quality": [
        "quality",
        "defect",
        "inspection",
        "ncr",
        "compliance",
        "osha",
    ],
    "communication": [
        "report",
        "meeting",
        "transcri",
        "rfi",
        "submittal",
        "minutes",
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
    state: ComplianceTeamState,
) -> dict:
    """Analyze request and determine routing."""
    request = state.get("request", "")
    task_type = state.get("task_type", "")
    if not task_type or task_type == "auto":
        task_type = _infer_task_type(request)
    return {"task_type": task_type, "status": "analyzed"}


async def run_quality_node(
    state: ComplianceTeamState,
) -> dict:
    """Invoke the quality agent."""
    try:
        result = await run_quality_agent(
            project_id=state["project_id"],
        )
        return {
            "quality_results": result,
            "status": "quality_complete",
        }
    except Exception as exc:
        logger.error("Quality agent failed: %s", exc)
        return {
            "quality_results": {
                "status": "failed",
                "error": str(exc),
            },
            "status": "quality_failed",
            "error": str(exc),
        }


async def run_communication_node(
    state: ComplianceTeamState,
) -> dict:
    """Invoke the communication agent."""
    try:
        result = await run_communication_agent(
            project_id=state["project_id"],
        )
        return {
            "communication_results": result,
            "status": "communication_complete",
        }
    except Exception as exc:
        logger.error(
            "Communication agent failed: %s",
            exc,
        )
        return {
            "communication_results": {
                "status": "failed",
                "error": str(exc),
            },
            "status": "communication_failed",
            "error": str(exc),
        }


async def human_review_node(
    state: ComplianceTeamState,
) -> dict:
    """Present results for human review."""
    review_data = {
        "quality": state.get("quality_results"),
        "communication": state.get(
            "communication_results",
        ),
    }
    feedback = interrupt(review_data)
    return {
        "human_feedback": feedback,
        "status": "reviewed",
    }


async def compile_report_node(
    state: ComplianceTeamState,
) -> dict:
    """Compile final compliance report."""
    try:
        report = {
            "project_id": state["project_id"],
            "task_type": state.get(
                "task_type",
                "full",
            ),
            "quality": state.get("quality_results"),
            "communication": state.get(
                "communication_results",
            ),
            "human_feedback": state.get(
                "human_feedback",
            ),
            "summary": ("Compliance team analysis complete."),
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
    state: ComplianceTeamState,
) -> list[Hashable]:
    """Route to appropriate agents."""
    task_type = state.get("task_type", "full")
    if task_type == "full":
        return ["run_quality", "run_communication"]
    return [f"run_{task_type}"]


def build_compliance_team(checkpointer=None) -> CompiledStateGraph:
    """Build the Compliance Team supervisor graph."""
    workflow = StateGraph(ComplianceTeamState)

    workflow.add_node("analyze", analyze_node)
    workflow.add_node(
        "run_quality",
        run_quality_node,
    )
    workflow.add_node(
        "run_communication",
        run_communication_node,
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
            "run_quality": "run_quality",
            "run_communication": "run_communication",
        },
    )
    workflow.add_edge("run_quality", "human_review")
    workflow.add_edge(
        "run_communication",
        "human_review",
    )
    workflow.add_edge("human_review", "compile_report")
    workflow.add_edge("compile_report", END)

    return workflow.compile(checkpointer=checkpointer)


async def run_compliance_team(
    project_id: str,
    request: str = "",
    task_type: str = "full",
) -> dict:
    """Run the Compliance Team supervisor."""
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_compliance_team(checkpointer=checkpointer)
    config = cast(
        RunnableConfig, {"configurable": {"thread_id": f"compliance_team_{uuid.uuid4().hex}"}}
    )

    initial_state: ComplianceTeamState = {
        "project_id": project_id,
        "request": request,
        "task_type": task_type,
        "quality_results": None,
        "communication_results": None,
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
        logger.error("Agent timed out after 300s", extra={"agent": "compliance_team"})
        return {**initial_state, "status": "timeout", "error": "Agent execution timed out"}
    except Exception as exc:
        logger.error(
            "Compliance team failed for %s: %s",
            project_id,
            exc,
        )
        return {
            **initial_state,
            "status": "failed",
            "error": str(exc),
        }
