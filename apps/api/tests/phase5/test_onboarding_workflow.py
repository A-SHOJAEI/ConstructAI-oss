"""Tests for new project onboarding workflow."""

from __future__ import annotations

from app.services.orchestration.workflows.new_project_onboarding import (
    run_onboarding,
)


class TestOnboardingWorkflow:
    async def test_full_onboarding(self):
        result = await run_onboarding(
            project_id="test-project-1",
            document_ids=["doc-1", "doc-2", "doc-3"],
        )
        assert result["status"] == "completed"
        assert len(result["steps_completed"]) == 5

    async def test_onboarding_steps_order(self):
        result = await run_onboarding(
            project_id="test-project-1",
            document_ids=["doc-1"],
        )
        steps = [s["step"] for s in result["steps_completed"]]
        assert steps[0] == "document_classification"
        assert steps[1] == "cost_estimation"
        assert steps[2] == "schedule_analysis"
        assert steps[3] == "site_layout"
        assert steps[4] == "procurement_setup"

    async def test_onboarding_results_structure(self):
        result = await run_onboarding(
            project_id="test-project-1",
        )
        assert "documents" in result["results"]
        assert "estimate" in result["results"]
        assert "schedule" in result["results"]
        assert "site_layout" in result["results"]
        assert "procurement" in result["results"]

    async def test_onboarding_no_documents(self):
        result = await run_onboarding(
            project_id="test-project-1",
        )
        assert result["status"] == "completed"
        docs = result["results"]["documents"]
        assert docs["classified_documents"] == 0

    async def test_onboarding_project_id(self):
        result = await run_onboarding(
            project_id="my-project-123",
        )
        assert result["project_id"] == "my-project-123"
