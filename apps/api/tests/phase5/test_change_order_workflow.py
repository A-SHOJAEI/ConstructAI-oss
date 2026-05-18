"""Tests for change order processing workflow."""

from __future__ import annotations

from app.services.orchestration.workflows.change_order_processing import (
    run_change_order_processing,
)


class TestChangeOrderWorkflow:
    async def test_basic_change_order(self):
        result = await run_change_order_processing(
            project_id="test-p1",
            change_order_data={
                "description": "Foundation redesign",
                "type": "design_error",
                "cost_impact": 150000,
                "schedule_impact_days": 14,
                "original_contract": 5000000,
            },
        )
        assert result["status"] == "waiting_human"
        assert result["approval_required"] is True

    async def test_cost_impact_calculation(self):
        result = await run_change_order_processing(
            project_id="test-p1",
            change_order_data={
                "cost_impact": 100000,
                "original_contract": 2000000,
            },
        )
        cost = result["cost_impact"]
        assert cost["percentage"] == 5.0

    async def test_risk_exposure(self):
        result = await run_change_order_processing(
            project_id="test-p1",
            change_order_data={
                "cost_impact": 500000,
                "schedule_impact_days": 60,
                "original_contract": 1000000,
            },
        )
        risk = result["risk_exposure"]
        assert risk["risk_score"] > 0
        assert risk["risk_level"] in (
            "low",
            "medium",
            "high",
        )

    async def test_steps_completed(self):
        result = await run_change_order_processing(
            project_id="test-p1",
            change_order_data={"description": "test"},
        )
        steps = [s["step"] for s in result["steps_completed"]]
        assert "parse_scope" in steps
        assert "impact_analysis" in steps
        assert "material_impact" in steps

    async def test_high_risk_change_order(self):
        result = await run_change_order_processing(
            project_id="test-p1",
            change_order_data={
                "cost_impact": 1000000,
                "schedule_impact_days": 90,
                "original_contract": 2000000,
            },
        )
        assert result["risk_exposure"]["risk_level"] == "high"
