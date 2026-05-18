"""Tests for the Orchestrator Agent (11th agent)."""

from __future__ import annotations

from app.services.agents.orchestrator_agent import (
    build_orchestrator_agent,
    compile_results,
    route_workflow,
    run_orchestrator_agent,
    select_workflow,
)


class TestOrchestratorAgent:
    def test_select_onboarding(self):
        state = {
            "workflow_type": "new_project_onboarding",
            "project_id": "p1",
        }
        assert select_workflow(state) == "onboarding"

    def test_select_change_order(self):
        state = {
            "workflow_type": "change_order_processing",
            "project_id": "p1",
        }
        assert select_workflow(state) == "change_order"

    def test_select_safety(self):
        state = {
            "workflow_type": "safety_incident_response",
            "project_id": "p1",
        }
        assert select_workflow(state) == "safety_incident"

    def test_select_default(self):
        state = {"workflow_type": "unknown", "project_id": "p1"}
        assert select_workflow(state) == "onboarding"

    async def test_route_workflow(self):
        state = {
            "workflow_type": "new_project_onboarding",
            "project_id": "p1",
            "current_priority": 3,
        }
        result = await route_workflow(state)
        assert "messages" in result
        assert len(result["messages"]) > 0

    async def test_compile_results_planning(self):
        state = {
            "project_id": "p1",
            "workflow_type": "new_project_onboarding",
            "planning_results": {"status": "completed"},
            "execution_results": None,
            "compliance_results": None,
        }
        result = await compile_results(state)
        assert result["final_output"]["status"] == "completed"
        assert "planning" in result["final_output"]

    def test_build_graph(self):
        graph = build_orchestrator_agent()
        assert graph is not None

    async def test_run_onboarding(self):
        result = await run_orchestrator_agent(
            project_id="test-p1",
            workflow_type="new_project_onboarding",
            input_data={"document_ids": ["doc-1"]},
        )
        assert result["status"] == "completed"
        assert result["workflow_type"] == "new_project_onboarding"

    async def test_run_change_order(self):
        result = await run_orchestrator_agent(
            project_id="test-p1",
            workflow_type="change_order_processing",
            input_data={
                "description": "Scope change",
                "cost_impact": 50000,
                "original_contract": 1000000,
            },
        )
        assert result is not None
        assert result["workflow_type"] == "change_order_processing"

    async def test_run_safety_incident(self):
        result = await run_orchestrator_agent(
            project_id="test-p1",
            workflow_type="safety_incident_response",
            input_data={
                "type": "fall_hazard",
                "severity": "high",
            },
        )
        assert result is not None
        assert result["workflow_type"] == "safety_incident_response"
