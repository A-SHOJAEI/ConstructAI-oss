"""Tests for change order analysis."""

from __future__ import annotations

from decimal import Decimal

from app.services.controls.change_order_analyzer import (
    analyze_change_order,
)


class TestChangeOrderAnalyzer:
    async def test_high_risk_change_order(self):
        result = await analyze_change_order(
            title="Major redesign",
            description="Complete foundation redesign",
            change_type="design_error",
            cost_impact=Decimal("500000"),
            schedule_impact_days=45,
            project_budget=Decimal("2000000"),
        )
        assert result["risk_level"] in ("high", "medium")
        assert result["risk_score"] >= Decimal("4")
        assert len(result["recommendations"]) > 0

    async def test_low_risk_change_order(self):
        result = await analyze_change_order(
            title="Minor finish change",
            description="Paint color change",
            change_type="value_engineering",
            cost_impact=Decimal("5000"),
            schedule_impact_days=0,
            project_budget=Decimal("10000000"),
        )
        assert result["risk_level"] == "low"
        assert result["risk_score"] < Decimal("4")

    async def test_regulatory_change_high_risk(self):
        result = await analyze_change_order(
            title="Fire code update",
            description="New fire code compliance required",
            change_type="regulatory",
            cost_impact=Decimal("200000"),
            schedule_impact_days=30,
        )
        assert Decimal(str(result["risk_score"])) >= Decimal("4")
        assert "regulatory" in result["change_type_analysis"]["type"]

    async def test_result_structure(self):
        result = await analyze_change_order(
            title="Test",
            description="Test change",
            change_type="owner_directed",
            cost_impact=Decimal("10000"),
            schedule_impact_days=5,
        )
        assert "risk_score" in result
        assert "risk_level" in result
        assert "risk_factors" in result
        assert "recommendations" in result
        assert "impact_summary" in result

    async def test_zero_impact(self):
        result = await analyze_change_order(
            title="Cosmetic",
            description="No impact change",
            change_type="value_engineering",
            cost_impact=Decimal("0"),
            schedule_impact_days=0,
        )
        assert result["risk_score"] <= Decimal("5")
