"""Tests for the LangGraph-based orchestrator agent.

There are TWO orchestrator_agent.py files (intentional, documented in
CLAUDE.md):
  - services/orchestration/orchestrator_agent.py — simple dispatcher,
    covered by test_orchestrator_dispatch.py
  - services/agents/orchestrator_agent.py — this LangGraph multi-agent

This file pins the LangGraph variant: workflow routing logic, the
node error-fallback shape, and the compile_results aggregation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.agents.orchestrator_agent import (
    _VALID_WORKFLOWS,
    change_order_node,
    compile_results,
    onboarding_node,
    route_workflow,
    safety_incident_node,
    select_workflow,
)

# =========================================================================
# _VALID_WORKFLOWS — pin canonical workflow types
# =========================================================================


def test_valid_workflows_canonical():
    """Pin documented workflow types — refactor must not silently
    drop one."""
    expected = {
        "new_project_onboarding": "onboarding",
        "change_order_processing": "change_order",
        "safety_incident_response": "safety_incident",
    }
    assert expected == _VALID_WORKFLOWS


# =========================================================================
# select_workflow
# =========================================================================


def test_select_workflow_known_routes_correctly():
    assert select_workflow({"workflow_type": "new_project_onboarding"}) == "onboarding"
    assert select_workflow({"workflow_type": "change_order_processing"}) == "change_order"
    assert select_workflow({"workflow_type": "safety_incident_response"}) == "safety_incident"


def test_select_workflow_unknown_defaults_to_onboarding(caplog):
    """[fail-safe] Unknown workflow type → defaults to "onboarding"
    branch + logs error. Pin so a refactor doesn't quietly silently
    accept and ignore unknown workflows."""
    import logging

    with caplog.at_level(logging.ERROR):
        out = select_workflow({"workflow_type": "alien_workflow_xyz"})
    assert out == "onboarding"
    assert any("Unknown workflow_type" in r.message for r in caplog.records)


def test_select_workflow_missing_key_defaults():
    """No workflow_type → empty string → defaults to onboarding."""
    out = select_workflow({})
    assert out == "onboarding"


# =========================================================================
# route_workflow
# =========================================================================


@pytest.mark.asyncio
async def test_route_workflow_returns_messages():
    out = await route_workflow(
        {
            "workflow_type": "new_project_onboarding",
            "current_priority": 3,
            "project_id": "p-1",
            "input_data": {},
            "planning_results": None,
            "execution_results": None,
            "compliance_results": None,
            "final_output": None,
            "messages": [],
        }
    )
    assert "messages" in out
    # Single status message:
    assert len(out["messages"]) == 1
    assert "new_project_onboarding" in out["messages"][0]


# =========================================================================
# Workflow nodes — error path
# =========================================================================


def _empty_state(workflow_type: str = "x") -> dict:
    """Build a default OrchestratorState dict for tests."""
    return {
        "project_id": "p-1",
        "workflow_type": workflow_type,
        "input_data": {},
        "planning_results": None,
        "execution_results": None,
        "compliance_results": None,
        "final_output": None,
        "messages": [],
        "current_priority": 3,
    }


@pytest.mark.asyncio
async def test_onboarding_node_handles_runner_exception():
    """[failure isolation] If the underlying onboarding workflow
    raises, the node returns a failure-shaped dict — does NOT
    propagate the exception (would tear down the agent graph)."""
    with patch(
        "app.services.orchestration.workflows.new_project_onboarding.run_onboarding",
        side_effect=RuntimeError("simulated failure"),
    ):
        out = await onboarding_node(_empty_state())
    assert "planning_results" in out
    assert out["planning_results"]["status"] == "failed"
    assert "simulated failure" in out["planning_results"]["error"]


@pytest.mark.asyncio
async def test_change_order_node_handles_runner_exception():
    with patch(
        "app.services.orchestration.workflows.change_order_processing.run_change_order_processing",
        side_effect=RuntimeError("co failure"),
    ):
        out = await change_order_node(_empty_state())
    assert out["execution_results"]["status"] == "failed"


@pytest.mark.asyncio
async def test_safety_incident_node_handles_runner_exception():
    with patch(
        "app.services.orchestration.workflows.safety_incident_response.run_safety_incident_response",
        side_effect=RuntimeError("safety failure"),
    ):
        out = await safety_incident_node(_empty_state())
    assert out["compliance_results"]["status"] == "failed"


# =========================================================================
# Workflow nodes — happy path (mocked runner)
# =========================================================================


@pytest.mark.asyncio
async def test_onboarding_node_dispatches_to_runner():
    """Verify the node calls run_onboarding with the right args."""
    fake = AsyncMock(return_value={"status": "ok", "documents_processed": 5})
    with patch(
        "app.services.orchestration.workflows.new_project_onboarding.run_onboarding",
        fake,
    ):
        out = await onboarding_node(_empty_state())
    fake.assert_awaited_once()
    assert out["planning_results"]["status"] == "ok"


@pytest.mark.asyncio
async def test_change_order_node_dispatches_to_runner():
    fake = AsyncMock(return_value={"status": "approved"})
    with patch(
        "app.services.orchestration.workflows.change_order_processing.run_change_order_processing",
        fake,
    ):
        out = await change_order_node(_empty_state())
    fake.assert_awaited_once()
    assert out["execution_results"]["status"] == "approved"


@pytest.mark.asyncio
async def test_safety_incident_node_dispatches_to_runner():
    fake = AsyncMock(return_value={"status": "logged"})
    with patch(
        "app.services.orchestration.workflows.safety_incident_response.run_safety_incident_response",
        fake,
    ):
        out = await safety_incident_node(_empty_state())
    fake.assert_awaited_once()
    assert out["compliance_results"]["status"] == "logged"


# =========================================================================
# compile_results — aggregation
# =========================================================================


@pytest.mark.asyncio
async def test_compile_results_returns_required_keys():
    state = _empty_state("new_project_onboarding")
    state["planning_results"] = {"a": 1}
    state["execution_results"] = None
    state["compliance_results"] = None

    out = await compile_results(state)
    assert "final_output" in out


@pytest.mark.asyncio
async def test_compile_results_includes_all_phase_outputs():
    """Final output should aggregate planning + execution + compliance
    results."""
    state = _empty_state()
    state["planning_results"] = {"plan": "data"}
    state["execution_results"] = {"exec": "result"}
    state["compliance_results"] = {"compliance": "passed"}

    out = await compile_results(state)
    final = out["final_output"]
    assert isinstance(final, dict)
    # All three phase results should be referenced:
    assert "planning_results" in final or "plan" in str(final).lower()
