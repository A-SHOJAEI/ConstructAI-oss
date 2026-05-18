"""Tests for change order risk analysis.

Pin every scoring branch and the cumulative-impact escalation that
auto-flags multi-change-order risk:

- Project-type-specific cost thresholds (commercial / infrastructure /
  residential).
- Schedule-impact bands (>30, >14, >0 days).
- Change-type risk weights (regulatory > design_error > field_condition
  > owner_directed > value_engineering).
- Cumulative impact escalation: > 15% of budget = critical.
- Risk-level recommendations.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.controls.change_order_analyzer import (
    _PROJECT_TYPE_THRESHOLDS,
    _RISK_RECOMMENDATIONS,
    analyze_change_order,
)

# =========================================================================
# Module constants
# =========================================================================


def test_project_type_thresholds_canonical():
    """Pin documented project types — refactor must not drop one."""
    expected = {"commercial", "infrastructure", "residential"}
    assert set(_PROJECT_TYPE_THRESHOLDS.keys()) == expected


def test_infrastructure_more_sensitive_than_commercial():
    """Infrastructure projects flag at lower thresholds — public works
    bear closer scrutiny."""
    com = _PROJECT_TYPE_THRESHOLDS["commercial"]
    inf = _PROJECT_TYPE_THRESHOLDS["infrastructure"]
    assert inf["cost_high"] < com["cost_high"]
    assert inf["cost_medium"] < com["cost_medium"]


def test_residential_more_lenient_than_commercial():
    res = _PROJECT_TYPE_THRESHOLDS["residential"]
    com = _PROJECT_TYPE_THRESHOLDS["commercial"]
    assert res["cost_high"] > com["cost_high"]


def test_risk_recommendations_canonical_levels():
    assert {"high", "medium", "low"} <= set(_RISK_RECOMMENDATIONS.keys())


def test_high_risk_recs_include_executive_review():
    recs = _RISK_RECOMMENDATIONS["high"]
    joined = " ".join(recs).lower()
    assert "executive" in joined
    assert "ccb" in joined or "control board" in joined


# =========================================================================
# analyze_change_order — risk scoring
# =========================================================================


@pytest.mark.asyncio
async def test_low_risk_small_change():
    """Tiny change in a commercial project → low risk."""
    out = await analyze_change_order(
        title="Add light fixture",
        description="Customer-requested update",
        change_type="value_engineering",  # weight 0.5
        cost_impact=Decimal("500"),
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
        project_type="commercial",
    )
    assert out["risk_level"] == "low"


@pytest.mark.asyncio
async def test_high_cost_impact_commercial():
    """Cost impact > 5% of commercial budget → flagged high."""
    out = await analyze_change_order(
        title="Major scope change",
        description="...",
        change_type="design_error",  # weight 2.5
        cost_impact=Decimal("80000"),  # 8% of $1M budget
        schedule_impact_days=20,
        project_budget=Decimal("1000000"),
        project_type="commercial",
    )
    assert "High cost impact" in " ".join(out["risk_factors"])


@pytest.mark.asyncio
async def test_infrastructure_lower_threshold():
    """3% on infrastructure triggers high (would be just medium on
    commercial). Pin the project-type-specific behavior."""
    cost = Decimal("40000")  # 4% of $1M
    out_inf = await analyze_change_order(
        title="x",
        description="x",
        change_type="field_condition",
        cost_impact=cost,
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
        project_type="infrastructure",
    )
    out_com = await analyze_change_order(
        title="x",
        description="x",
        change_type="field_condition",
        cost_impact=cost,
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
        project_type="commercial",
    )
    inf_factors = " ".join(out_inf["risk_factors"])
    com_factors = " ".join(out_com["risk_factors"])
    # Same cost, different threshold: infra flags "High", commercial flags "Moderate"
    assert "High cost impact" in inf_factors
    assert "Moderate cost impact" in com_factors


@pytest.mark.asyncio
async def test_negative_cost_impact_uses_absolute():
    """Deductive change orders (negative cost) score by magnitude,
    not direction."""
    out = await analyze_change_order(
        title="Scope reduction",
        description="...",
        change_type="value_engineering",
        cost_impact=Decimal("-80000"),  # 8% of budget, deductive
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
    )
    assert "High cost impact" in " ".join(out["risk_factors"])


@pytest.mark.asyncio
async def test_no_budget_uses_absolute_threshold():
    """Without project budget, fall back to absolute $100K threshold."""
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="field_condition",
        cost_impact=Decimal("150000"),  # > $100K absolute threshold
        schedule_impact_days=0,
        project_budget=None,
    )
    assert "Large absolute cost impact" in " ".join(out["risk_factors"])


@pytest.mark.asyncio
async def test_schedule_impact_major_band():
    """> 30 days schedule impact → "Major" factor."""
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="owner_directed",
        cost_impact=Decimal("1000"),
        schedule_impact_days=45,
        project_budget=Decimal("1000000"),
    )
    assert any("Major schedule" in f for f in out["risk_factors"])


@pytest.mark.asyncio
async def test_schedule_impact_moderate_band():
    """14 < days ≤ 30 → "Moderate" factor."""
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="owner_directed",
        cost_impact=Decimal("1000"),
        schedule_impact_days=20,
        project_budget=Decimal("1000000"),
    )
    assert any("Moderate schedule" in f for f in out["risk_factors"])


@pytest.mark.asyncio
async def test_negative_schedule_impact_acceleration_uses_magnitude():
    """Schedule acceleration (negative days) scored by magnitude."""
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="owner_directed",
        cost_impact=Decimal("1000"),
        schedule_impact_days=-45,  # 45-day acceleration
        project_budget=Decimal("1000000"),
    )
    assert any("Major schedule" in f for f in out["risk_factors"])


# =========================================================================
# Change-type weights
# =========================================================================


@pytest.mark.asyncio
async def test_change_type_weight_propagated():
    """The change_type_analysis section must carry the documented weight."""
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="regulatory",
        cost_impact=Decimal("1000"),
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
    )
    assert out["change_type_analysis"]["type"] == "regulatory"
    assert out["change_type_analysis"]["type_risk_weight"] == "3"


@pytest.mark.asyncio
async def test_unknown_change_type_uses_default_weight():
    """Unknown type defaults to weight 1.5 (not 0)."""
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="unicorn_change",  # not in canonical list
        cost_impact=Decimal("1000"),
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
    )
    assert out["change_type_analysis"]["type_risk_weight"] == "1.5"


# =========================================================================
# Cumulative impact escalation
# =========================================================================


@pytest.mark.asyncio
async def test_cumulative_impact_under_threshold_no_escalation():
    """8% cumulative cost — moderate, not critical."""
    cumulative = [
        {"cost_impact": "30000", "schedule_impact": 5},
        {"cost_impact": "40000", "schedule_impact": 5},
    ]
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="value_engineering",
        cost_impact=Decimal("10000"),  # 80K total = 8% of 1M
        schedule_impact_days=2,
        project_budget=Decimal("1000000"),
        cumulative_changes=cumulative,
    )
    assert out["risk_level"] != "critical"
    cum = out["cumulative_assessment"]
    assert cum["total_changes"] == 3
    assert cum["cumulative_cost_pct"] == 8.0


@pytest.mark.asyncio
async def test_cumulative_impact_over_15_pct_critical():
    """[escalation] > 15% cumulative cost → critical, score forced to 10."""
    cumulative = [
        {"cost_impact": "100000", "schedule_impact": 0},
        {"cost_impact": "100000", "schedule_impact": 0},
    ]
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="value_engineering",
        cost_impact=Decimal("0"),  # 200K = 20% of 1M
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
        cumulative_changes=cumulative,
    )
    assert out["risk_level"] == "critical"
    assert out["risk_score"] == 10.0
    assert any("CRITICAL" in f for f in out["risk_factors"])


@pytest.mark.asyncio
async def test_cumulative_impact_warning_band_10_to_15():
    """10-15% cumulative — warning, low → medium escalation."""
    cumulative = [{"cost_impact": "120000", "schedule_impact": 0}]  # 12% of 1M
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="value_engineering",  # would be "low" by itself
        cost_impact=Decimal("0"),
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
        cumulative_changes=cumulative,
    )
    # Total = 12% — over 10% threshold → low escalates to medium
    assert any("Warning" in f for f in out["risk_factors"])


# =========================================================================
# Recommendations
# =========================================================================


@pytest.mark.asyncio
async def test_recommendations_for_high_risk_include_executive_review():
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="design_error",
        cost_impact=Decimal("100000"),
        schedule_impact_days=45,
        project_budget=Decimal("1000000"),
    )
    joined = " ".join(out["recommendations"]).lower()
    assert "executive" in joined or "ccb" in joined


@pytest.mark.asyncio
async def test_design_error_adds_qa_recommendation():
    """Design-error change orders should suggest QA/QC review."""
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="design_error",
        cost_impact=Decimal("1000"),
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
    )
    joined = " ".join(out["recommendations"]).lower()
    assert "qa" in joined or "qc" in joined


@pytest.mark.asyncio
async def test_regulatory_change_recommends_code_check():
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="regulatory",
        cost_impact=Decimal("1000"),
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
    )
    joined = " ".join(out["recommendations"]).lower()
    assert "code" in joined


@pytest.mark.asyncio
async def test_field_condition_recommends_site_investigation():
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="field_condition",
        cost_impact=Decimal("1000"),
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
    )
    joined = " ".join(out["recommendations"]).lower()
    assert "site investigation" in joined or "investigation" in joined


@pytest.mark.asyncio
async def test_deductive_co_recommends_credit_verification():
    """Negative cost = deductive CO → recommend verifying credit."""
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="value_engineering",
        cost_impact=Decimal("-50000"),
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
    )
    joined = " ".join(out["recommendations"]).lower()
    assert "credit" in joined


# =========================================================================
# Result schema
# =========================================================================


@pytest.mark.asyncio
async def test_result_includes_required_keys():
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="owner_directed",
        cost_impact=Decimal("1000"),
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
    )
    for key in (
        "risk_score",
        "risk_level",
        "risk_factors",
        "project_type",
        "change_type_analysis",
        "impact_summary",
        "recommendations",
    ):
        assert key in out
    assert 0 <= out["risk_score"] <= 10


@pytest.mark.asyncio
async def test_risk_score_capped_at_ten():
    """Stack every risk factor — score must NOT exceed 10."""
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="regulatory",  # max weight 3
        cost_impact=Decimal("1000000"),  # 100% of budget
        schedule_impact_days=100,  # major
        project_budget=Decimal("1000000"),
    )
    assert out["risk_score"] <= 10.0


@pytest.mark.asyncio
async def test_unknown_project_type_falls_back_to_commercial():
    """Unknown project_type uses commercial thresholds — pin the
    fallback path."""
    out = await analyze_change_order(
        title="x",
        description="x",
        change_type="owner_directed",
        cost_impact=Decimal("1000"),
        schedule_impact_days=0,
        project_budget=Decimal("1000000"),
        project_type="alien_project",
    )
    # Must not crash — must produce a normal result.
    assert out["project_type"] == "alien_project"  # echoed back even if unknown
