"""Orchestrator Agent - the 11th agent wrapping team supervisors."""

from __future__ import annotations

import asyncio
import logging
import operator
import uuid
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)


class OrchestratorState(TypedDict):
    project_id: str
    workflow_type: str
    input_data: dict
    planning_results: dict | None
    execution_results: dict | None
    compliance_results: dict | None
    final_output: dict | None
    messages: Annotated[list, operator.add]
    current_priority: int


async def route_workflow(state: OrchestratorState) -> dict:
    """Route to appropriate workflow based on type."""
    workflow_type = state.get("workflow_type", "")
    logger.info(
        "Orchestrator routing: %s (P%d)",
        workflow_type,
        state.get("current_priority", 3),
    )
    return {"messages": [f"Routing to {workflow_type}"]}


_VALID_WORKFLOWS = {
    "new_project_onboarding": "onboarding",
    "change_order_processing": "change_order",
    "safety_incident_response": "safety_incident",
}


def select_workflow(
    state: OrchestratorState,
) -> str:
    """Select workflow branch. Rejects unknown workflow types."""
    wf = state.get("workflow_type", "")
    branch = _VALID_WORKFLOWS.get(wf)
    if branch is None:
        logger.error("Unknown workflow_type %r — defaulting to onboarding", wf)
        return "onboarding"
    return branch


async def onboarding_node(
    state: OrchestratorState,
) -> dict:
    """Run new project onboarding."""
    try:
        from app.services.orchestration.workflows.new_project_onboarding import (
            run_onboarding,
        )

        result = await run_onboarding(
            project_id=state["project_id"],
            input_data=state.get("input_data", {}),
        )
        return {
            "planning_results": result,
            "messages": ["Onboarding complete"],
        }
    except Exception as e:
        logger.error(f"Onboarding workflow failed: {e}", exc_info=True)
        return {
            "planning_results": {"error": str(e), "status": "failed"},
            "messages": [f"Onboarding failed: {e}"],
        }


async def change_order_node(
    state: OrchestratorState,
) -> dict:
    """Run change order processing."""
    try:
        from app.services.orchestration.workflows.change_order_processing import (
            run_change_order_processing,
        )

        result = await run_change_order_processing(
            project_id=state["project_id"],
            change_order_data=state.get("input_data", {}),
        )
        return {
            "execution_results": result,
            "messages": ["Change order processing complete"],
        }
    except Exception as e:
        logger.error(f"Change order workflow failed: {e}", exc_info=True)
        return {
            "execution_results": {"error": str(e), "status": "failed"},
            "messages": [f"Change order processing failed: {e}"],
        }


async def safety_incident_node(
    state: OrchestratorState,
) -> dict:
    """Run safety incident response."""
    try:
        from app.services.orchestration.workflows.safety_incident_response import (
            run_safety_incident_response,
        )

        result = await run_safety_incident_response(
            project_id=state["project_id"],
            incident_data=state.get("input_data", {}),
        )
        return {
            "compliance_results": result,
            "messages": ["Safety incident response complete"],
        }
    except Exception as e:
        logger.error(f"Safety incident workflow failed: {e}", exc_info=True)
        return {
            "compliance_results": {"error": str(e), "status": "failed"},
            "messages": [f"Safety incident response failed: {e}"],
        }


async def compile_results(
    state: OrchestratorState,
) -> dict:
    """Compile final output from all team results."""
    final: dict[str, Any] = {
        "project_id": state["project_id"],
        "workflow_type": state["workflow_type"],
    }

    if state.get("planning_results"):
        final["planning"] = state["planning_results"]
    if state.get("execution_results"):
        final["execution"] = state["execution_results"]
    if state.get("compliance_results"):
        final["compliance"] = state["compliance_results"]

    final["status"] = "completed"
    return {
        "final_output": final,
        "messages": ["Results compiled"],
    }


def build_orchestrator_agent(checkpointer=None) -> CompiledStateGraph:
    """Build the Orchestrator Agent graph."""
    workflow = StateGraph(OrchestratorState)

    workflow.add_node("route_workflow", route_workflow)
    workflow.add_node("onboarding", onboarding_node)
    workflow.add_node("change_order", change_order_node)
    workflow.add_node(
        "safety_incident",
        safety_incident_node,
    )
    workflow.add_node("compile_results", compile_results)

    workflow.add_edge(START, "route_workflow")
    workflow.add_conditional_edges(
        "route_workflow",
        select_workflow,
        {
            "onboarding": "onboarding",
            "change_order": "change_order",
            "safety_incident": "safety_incident",
        },
    )
    workflow.add_edge("onboarding", "compile_results")
    workflow.add_edge("change_order", "compile_results")
    workflow.add_edge("safety_incident", "compile_results")
    workflow.add_edge("compile_results", END)

    return workflow.compile(checkpointer=checkpointer)


async def run_orchestrator_agent(
    project_id: str,
    workflow_type: str,
    input_data: dict | None = None,
    priority: int = 3,
    correlation_id: str | None = None,
) -> dict:
    """Run the Orchestrator Agent."""
    from app.services.agents._config import make_agent_config
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_orchestrator_agent(checkpointer=checkpointer)
    # SECURITY: Use a random UUID for thread_id to prevent prediction or
    # enumeration of LangGraph thread IDs.
    thread_id = f"orchestrator_{uuid.uuid4().hex}"
    config = make_agent_config(thread_id, correlation_id=correlation_id)
    logger.info(
        "orchestrator_start",
        extra={
            "thread_id": thread_id,
            "correlation_id": correlation_id,
            "project_id": project_id,
            "workflow_type": workflow_type,
        },
    )
    initial_state: OrchestratorState = {
        "project_id": project_id,
        "workflow_type": workflow_type,
        "input_data": input_data or {},
        "planning_results": None,
        "execution_results": None,
        "compliance_results": None,
        "final_output": None,
        "messages": [],
        "current_priority": priority,
    }
    try:
        result = await asyncio.wait_for(
            graph.ainvoke(initial_state, config=config),
            timeout=300.0,  # 5 minute timeout
        )
        return result.get("final_output", {})
    except TimeoutError:
        logger.error("Agent timed out after 300s", extra={"agent": "orchestrator"})
        return {"error": "Agent execution timed out", "status": "timeout"}
    except Exception as exc:
        logger.error("Orchestrator agent failed: %s", exc, exc_info=True)
        return {"error": str(exc), "status": "failed"}
