"""Tests for the procurement agent LangGraph nodes.

Pin price-trend recommendation thresholds, contract-risk priority
tiers, vendor-score sorting, and per-node error isolation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.agents.procurement_agent import (
    assess_contracts_node,
    build_procurement_agent,
    compile_recommendations_node,
    forecast_prices_node,
    score_vendors_node,
)

# =========================================================================
# forecast_prices_node
# =========================================================================


@pytest.mark.asyncio
async def test_forecast_prices_uses_documented_default_materials():
    """[contract] Empty materials -> default 3 categories
    (concrete/structural_steel/lumber). Pin so a refactor doesn't
    silently change the demo defaults shown to first-time users."""
    captured_categories = []

    async def fake_bls(_series_id):
        return [{"date": "2026-01", "value": 100}]

    async def fake_forecast(*, material_category, **_kwargs):
        captured_categories.append(material_category)
        return {"trend": "stable"}

    state = {"project_id": "p-1", "materials": []}
    with (
        patch("app.services.agents.procurement_agent.get_bls_ppi_series", fake_bls),
        patch("app.services.agents.procurement_agent.forecast_prices", fake_forecast),
    ):
        out = await forecast_prices_node(state)

    assert set(captured_categories) == {"concrete", "structural_steel", "lumber"}
    assert out["status"] == "prices_forecast"


@pytest.mark.asyncio
async def test_forecast_prices_uses_6_month_horizon():
    """[contract] 6-month horizon. Pin so refactor doesn't quietly
    extend (data degrades) or shorten (loses long-lead value)."""
    captured = {}

    async def fake_bls(_series_id):
        return []

    async def fake_forecast(*, horizon_months, **_kwargs):
        captured["horizon"] = horizon_months
        return {}

    state = {
        "project_id": "p-1",
        "materials": [{"category": "concrete", "series_id": "X"}],
    }
    with (
        patch("app.services.agents.procurement_agent.get_bls_ppi_series", fake_bls),
        patch("app.services.agents.procurement_agent.forecast_prices", fake_forecast),
    ):
        await forecast_prices_node(state)

    assert captured["horizon"] == 6


@pytest.mark.asyncio
async def test_forecast_prices_failure_isolated():
    async def boom(_series_id):
        raise RuntimeError("BLS API down")

    state = {
        "project_id": "p-1",
        "materials": [{"category": "concrete", "series_id": "X"}],
    }
    with patch("app.services.agents.procurement_agent.get_bls_ppi_series", boom):
        out = await forecast_prices_node(state)

    assert out["price_forecasts"] is None
    assert out["status"] == "forecast_failed"
    assert "BLS API down" in out["error"]


# =========================================================================
# score_vendors_node
# =========================================================================


@pytest.mark.asyncio
async def test_score_vendors_no_vendors_uses_default_4():
    """[contract] No vendors -> 4 default demo vendors. Pin so a
    refactor doesn't silently change the demo set shown to first-time
    users."""
    scored = []

    async def fake_score(vendor):
        scored.append(vendor.get("name"))
        return {"overall_score": 75.0, "recommendation": "approved"}

    state = {"project_id": "p-1", "materials": []}
    with patch("app.services.agents.procurement_agent.score_vendor", fake_score):
        out = await score_vendors_node(state)

    assert len(scored) == 4
    # Pin 4 documented demo vendor names:
    assert "Pacific Steel Fabricators" in scored
    assert "Central Ready Mix" in scored
    assert "Southwest Rebar Supply" in scored
    assert "BuildMat Distributors" in scored
    assert out["status"] == "vendors_scored"


@pytest.mark.asyncio
async def test_score_vendors_sorts_by_overall_score_descending():
    """[contract] Vendors sorted by overall_score descending — top
    vendor MUST be first. Pin: refactor must not change sort order."""
    score_lookup = {"A": 50, "B": 90, "C": 70}

    async def fake_score(vendor):
        return {"overall_score": score_lookup[vendor["name"]]}

    state = {
        "project_id": "p-1",
        "materials": [
            {
                "vendors": [
                    {"name": "A"},
                    {"name": "B"},
                    {"name": "C"},
                ]
            }
        ],
    }
    with patch("app.services.agents.procurement_agent.score_vendor", fake_score):
        out = await score_vendors_node(state)

    names = [v["vendor_name"] for v in out["vendor_scores"]]
    assert names == ["B", "C", "A"]


@pytest.mark.asyncio
async def test_score_vendors_failure_isolated():
    async def boom(_vendor):
        raise RuntimeError("scorer crashed")

    state = {
        "project_id": "p-1",
        "materials": [{"vendors": [{"name": "X"}]}],
    }
    with patch("app.services.agents.procurement_agent.score_vendor", boom):
        out = await score_vendors_node(state)

    assert out["vendor_scores"] == []
    assert out["status"] == "scoring_failed"


# =========================================================================
# assess_contracts_node
# =========================================================================


@pytest.mark.asyncio
async def test_assess_contracts_uses_first_contract_text_in_materials():
    """When multiple materials have contract_text, the FIRST one is
    used (deterministic — pin so a refactor doesn't pick last/random)."""
    captured = {}

    async def fake_score(*, contract_text, project_type):
        captured["contract_text"] = contract_text
        return {"overall_risk_score": 50.0}

    state = {
        "project_id": "p-1",
        "materials": [
            {"contract_text": "First contract"},
            {"contract_text": "Second contract"},
        ],
    }
    with patch(
        "app.services.agents.procurement_agent.score_contract_risk",
        fake_score,
    ):
        await assess_contracts_node(state)

    assert captured["contract_text"] == "First contract"


@pytest.mark.asyncio
async def test_assess_contracts_uses_demo_text_when_missing():
    """[fallback] No contract_text -> documented demo contract used.
    Pin so first-time users see realistic risk assessment."""
    captured = {}

    async def fake_score(*, contract_text, project_type):
        captured["contract_text"] = contract_text
        return {"overall_risk_score": 50.0}

    state = {"project_id": "p-1", "materials": []}
    with patch(
        "app.services.agents.procurement_agent.score_contract_risk",
        fake_score,
    ):
        await assess_contracts_node(state)

    # Demo includes the 4 canonical articles:
    assert "PAYMENT TERMS" in captured["contract_text"]
    assert "INDEMNIFICATION" in captured["contract_text"]
    assert "LIQUIDATED DAMAGES" in captured["contract_text"]


@pytest.mark.asyncio
async def test_assess_contracts_uses_commercial_project_type():
    """[contract] Default project_type='commercial'."""
    captured = {}

    async def fake_score(*, contract_text, project_type):
        captured["project_type"] = project_type
        return {}

    state = {"project_id": "p-1", "materials": []}
    with patch(
        "app.services.agents.procurement_agent.score_contract_risk",
        fake_score,
    ):
        await assess_contracts_node(state)

    assert captured["project_type"] == "commercial"


@pytest.mark.asyncio
async def test_assess_contracts_failure_isolated():
    async def boom(**_kwargs):
        raise RuntimeError("scorer down")

    state = {"project_id": "p-1", "materials": []}
    with patch(
        "app.services.agents.procurement_agent.score_contract_risk",
        boom,
    ):
        out = await assess_contracts_node(state)

    assert out["contract_risk"] is None
    assert out["status"] == "contract_failed" or "error" in out


# =========================================================================
# compile_recommendations_node — pin price/contract priority tiers
# =========================================================================


@pytest.mark.asyncio
async def test_compile_rising_price_high_priority():
    """[business invariant] Rising price -> 'high' priority + lock-in
    advice. Pin so a refactor doesn't downgrade urgency."""
    state = {
        "project_id": "p-1",
        "price_forecasts": {"concrete": {"trend": "rising"}},
        "vendor_scores": [],
        "contract_risk": None,
    }
    out = await compile_recommendations_node(state)
    recs = out["recommendations"]
    price_recs = [r for r in recs if r["type"] == "price_action"]
    assert len(price_recs) == 1
    assert price_recs[0]["priority"] == "high"
    assert "Lock in" in price_recs[0]["recommendation"]


@pytest.mark.asyncio
async def test_compile_falling_price_low_priority():
    """[business invariant] Falling price -> 'low' priority + defer
    advice."""
    state = {
        "project_id": "p-1",
        "price_forecasts": {"concrete": {"trend": "falling"}},
        "vendor_scores": [],
        "contract_risk": None,
    }
    out = await compile_recommendations_node(state)
    rec = next(r for r in out["recommendations"] if r["type"] == "price_action")
    assert rec["priority"] == "low"
    assert "Defer" in rec["recommendation"]


@pytest.mark.asyncio
async def test_compile_stable_price_medium_priority():
    state = {
        "project_id": "p-1",
        "price_forecasts": {"concrete": {"trend": "stable"}},
        "vendor_scores": [],
        "contract_risk": None,
    }
    out = await compile_recommendations_node(state)
    rec = next(r for r in out["recommendations"] if r["type"] == "price_action")
    assert rec["priority"] == "medium"


@pytest.mark.asyncio
async def test_compile_top_vendor_recommendation():
    """Top vendor (already sorted by score_vendors_node) gets a
    'vendor_selection' recommendation."""
    state = {
        "project_id": "p-1",
        "price_forecasts": {},
        "vendor_scores": [
            {"vendor_name": "Best Vendor", "overall_score": 90.0, "recommendation": "approved"},
        ],
        "contract_risk": None,
    }
    out = await compile_recommendations_node(state)
    selection = next(r for r in out["recommendations"] if r["type"] == "vendor_selection")
    assert selection["priority"] == "high"
    assert "Best Vendor" in selection["recommendation"]


@pytest.mark.asyncio
async def test_compile_vendor_with_risk_flags_gets_warning():
    """Vendor with risk_flags -> separate 'vendor_risk' recommendation."""
    state = {
        "project_id": "p-1",
        "price_forecasts": {},
        "vendor_scores": [
            {
                "vendor_name": "Risky Vendor",
                "overall_score": 40.0,
                "risk_flags": ["financial_instability", "low_safety"],
            },
        ],
        "contract_risk": None,
    }
    out = await compile_recommendations_node(state)
    risk_recs = [r for r in out["recommendations"] if r["type"] == "vendor_risk"]
    assert len(risk_recs) >= 1
    assert "financial_instability" in risk_recs[0]["recommendation"]


@pytest.mark.asyncio
async def test_compile_contract_risk_critical_at_70():
    """[business invariant] Risk score >= 70 -> 'critical' priority +
    'Legal review required'. Pin: refactor must not raise the
    threshold (legal review skipped on borderline contracts)."""
    state = {
        "project_id": "p-1",
        "price_forecasts": {},
        "vendor_scores": [],
        "contract_risk": {"overall_risk_score": 70.0},
    }
    out = await compile_recommendations_node(state)
    contract_rec = next(r for r in out["recommendations"] if r["type"] == "contract_risk")
    assert contract_rec["priority"] == "critical"
    assert "Legal review required" in contract_rec["recommendation"]


@pytest.mark.asyncio
async def test_compile_contract_risk_medium_at_40():
    """[boundary] Risk score in [40, 70) -> 'medium' priority."""
    state = {
        "project_id": "p-1",
        "price_forecasts": {},
        "vendor_scores": [],
        "contract_risk": {"overall_risk_score": 40.0},
    }
    out = await compile_recommendations_node(state)
    rec = next(r for r in out["recommendations"] if r["type"] == "contract_risk")
    assert rec["priority"] == "medium"


@pytest.mark.asyncio
async def test_compile_contract_risk_low_under_40():
    state = {
        "project_id": "p-1",
        "price_forecasts": {},
        "vendor_scores": [],
        "contract_risk": {"overall_risk_score": 30.0},
    }
    out = await compile_recommendations_node(state)
    rec = next(r for r in out["recommendations"] if r["type"] == "contract_risk")
    assert rec["priority"] == "low"
    assert "acceptable" in rec["recommendation"]


@pytest.mark.asyncio
async def test_compile_includes_strategy_recommendation():
    """[contract] Always includes the documented general-strategy
    recommendation (90-day pre-qualify)."""
    state = {
        "project_id": "p-1",
        "price_forecasts": {},
        "vendor_scores": [],
        "contract_risk": None,
    }
    out = await compile_recommendations_node(state)
    strategy = next(r for r in out["recommendations"] if r["type"] == "strategy")
    assert "Pre-qualify vendors" in strategy["recommendation"]
    assert "90 days" in strategy["recommendation"]


@pytest.mark.asyncio
async def test_compile_empty_inputs_still_returns_strategy():
    """[robustness] All inputs empty/None -> still returns at least
    the strategy recommendation."""
    state = {
        "project_id": "p-1",
        "price_forecasts": None,
        "vendor_scores": [],
        "contract_risk": None,
    }
    out = await compile_recommendations_node(state)
    assert len(out["recommendations"]) >= 1
    assert any(r["type"] == "strategy" for r in out["recommendations"])


# =========================================================================
# Graph build
# =========================================================================


def test_build_procurement_agent_returns_compiled_graph():
    graph = build_procurement_agent()
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    assert {
        "forecast_prices",
        "score_vendors",
        "assess_contracts",
        "compile_recommendations",
    } <= nodes
