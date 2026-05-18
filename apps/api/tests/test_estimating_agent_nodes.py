"""Tests for the estimating agent LangGraph nodes.

Pin per-node behavior + the confidence-tier formula
(line/parametric variance: <10% high, <25% medium, else low) +
the Monte Carlo P50 preference for ``recommended_total``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.agents.estimating_agent import (
    build_estimating_agent,
    compile_estimate_node,
    extract_quantities_node,
    match_costs_node,
    run_monte_carlo_node,
    run_parametric_node,
)

# =========================================================================
# extract_quantities_node
# =========================================================================


@pytest.mark.asyncio
async def test_extract_quantities_no_documents_returns_empty():
    state = {"project_id": "p-1", "documents": []}
    out = await extract_quantities_node(state)
    assert out["quantities"] == []
    assert out["status"] == "quantities_extracted"


@pytest.mark.asyncio
async def test_extract_quantities_routes_ifc_vs_document():
    """[contract] Documents with type='ifc' go to extract_quantities_from_ifc;
    everything else goes to extract_quantities_from_document. Pin the
    routing so a refactor doesn't accidentally pass IFC data through
    the document-text extractor."""
    captured = {"ifc_called": 0, "doc_called": 0}

    async def fake_ifc(data):
        captured["ifc_called"] += 1
        return [{"item": "ifc-quantity"}]

    async def fake_doc(*, text_content, filename):
        captured["doc_called"] += 1
        return [{"item": "doc-quantity"}]

    state = {
        "project_id": "p-1",
        "documents": [
            {"type": "ifc", "data": {"some": "model"}},
            {"type": "specification", "text_content": "Section 03 30 00", "filename": "spec.pdf"},
            {"type": "drawing", "text_content": "Detail 5/A-301", "filename": "drawing.pdf"},
        ],
    }
    with (
        patch(
            "app.services.agents.estimating_agent.extract_quantities_from_ifc",
            fake_ifc,
        ),
        patch(
            "app.services.agents.estimating_agent.extract_quantities_from_document",
            fake_doc,
        ),
    ):
        out = await extract_quantities_node(state)

    assert captured["ifc_called"] == 1
    assert captured["doc_called"] == 2  # 2 non-IFC docs
    assert len(out["quantities"]) == 3


@pytest.mark.asyncio
async def test_extract_quantities_failure_isolated():
    async def boom(*_args, **_kwargs):
        raise RuntimeError("extractor crashed")

    state = {"project_id": "p-1", "documents": [{"type": "ifc", "data": {}}]}
    with patch(
        "app.services.agents.estimating_agent.extract_quantities_from_ifc",
        boom,
    ):
        out = await extract_quantities_node(state)
    assert out["quantities"] == []
    assert out["status"] == "extraction_failed"
    assert "extractor crashed" in out["error"]


# =========================================================================
# match_costs_node
# =========================================================================


@pytest.mark.asyncio
async def test_match_costs_no_quantities_short_circuits():
    """[edge case] No quantities -> 'no_quantities' status (NOT failure)."""
    state = {"project_id": "p-1", "quantities": []}
    out = await match_costs_node(state)
    assert out["cost_matches"] == []
    assert out["status"] == "no_quantities"


@pytest.mark.asyncio
async def test_match_costs_uses_national_region():
    """[contract] Default cost matching uses 'national' region. Pin so a
    refactor doesn't accidentally use a regional default that gives
    location-skewed costs for cross-project comparisons."""
    captured = {}

    async def fake_match(quantities, *, region):
        captured["region"] = region
        return [{"total_cost": 100}]

    state = {"project_id": "p-1", "quantities": [{"item": "x"}]}
    with patch("app.services.agents.estimating_agent.match_costs", fake_match):
        await match_costs_node(state)

    assert captured["region"] == "national"


@pytest.mark.asyncio
async def test_match_costs_failure_isolated():
    async def boom(*_args, **_kwargs):
        raise RuntimeError("DB error")

    state = {"project_id": "p-1", "quantities": [{"item": "x"}]}
    with patch("app.services.agents.estimating_agent.match_costs", boom):
        out = await match_costs_node(state)
    assert out["cost_matches"] == []
    assert out["status"] == "matching_failed"


# =========================================================================
# run_parametric_node
# =========================================================================


@pytest.mark.asyncio
async def test_parametric_uses_input_data_when_provided():
    captured = {}

    async def fake_predict(params):
        captured.update(params)
        return {"total_predicted_cost": 123_456}

    state = {
        "project_id": "p-1",
        "cost_matches": [],
        "input_data": {
            "sqft": 80_000,
            "stories": 5,
            "project_type": "healthcare",
            "region": "northeast",
            "quality_level": "premium",
        },
    }
    with patch("app.services.agents.estimating_agent.predict_cost", fake_predict):
        await run_parametric_node(state)

    assert captured["sqft"] == 80_000
    assert captured["stories"] == 5
    assert captured["type"] == "healthcare"
    assert captured["region"] == "northeast"
    assert captured["quality_level"] == "premium"


@pytest.mark.asyncio
async def test_parametric_default_params_when_no_input():
    """[fallback] Missing input_data -> documented defaults
    (50000 sqft, 3 stories, commercial, national, standard)."""
    captured = {}

    async def fake_predict(params):
        captured.update(params)
        return {}

    state = {"project_id": "p-1", "cost_matches": []}
    with patch("app.services.agents.estimating_agent.predict_cost", fake_predict):
        await run_parametric_node(state)

    assert captured["sqft"] == 50_000
    assert captured["stories"] == 3
    assert captured["type"] == "commercial"
    assert captured["region"] == "national"
    assert captured["quality_level"] == "standard"


@pytest.mark.asyncio
async def test_parametric_failure_isolated():
    async def boom(_params):
        raise RuntimeError("model down")

    state = {"project_id": "p-1", "cost_matches": []}
    with patch("app.services.agents.estimating_agent.predict_cost", boom):
        out = await run_parametric_node(state)
    assert out["parametric_estimate"] is None
    assert out["status"] == "parametric_failed"


# =========================================================================
# run_monte_carlo_node
# =========================================================================


@pytest.mark.asyncio
async def test_monte_carlo_no_cost_data_short_circuits():
    """[edge case] No cost_matches -> 'no_cost_data' (don't run
    Monte Carlo on empty input — useless results)."""
    state = {"project_id": "p-1", "cost_matches": []}
    out = await run_monte_carlo_node(state)
    assert out["monte_carlo_results"] is None
    assert out["status"] == "no_cost_data"


@pytest.mark.asyncio
async def test_monte_carlo_uses_documented_iterations_and_contingency():
    """[contract] 10_000 simulations, 10% contingency. Pin so a
    refactor doesn't quietly reduce simulation count (statistical
    power) or change the documented contingency assumption."""
    captured = {}

    async def fake_mc(*, line_items, num_simulations, contingency_pct, org_id):
        captured.update(
            {
                "items": line_items,
                "iterations": num_simulations,
                "contingency": contingency_pct,
                "org_id": org_id,
            }
        )
        return {"p50": 1000, "p90": 1200, "mean": 1100}

    state = {
        "project_id": "p-1",
        "cost_matches": [{"total_cost": 1000}],
        "org_id": "org-x",
    }
    with patch("app.services.agents.estimating_agent.run_monte_carlo", fake_mc):
        await run_monte_carlo_node(state)

    assert captured["iterations"] == 10_000
    assert captured["contingency"] == 10.0
    assert captured["org_id"] == "org-x"


@pytest.mark.asyncio
async def test_monte_carlo_failure_isolated():
    async def boom(**_kwargs):
        raise RuntimeError("mc broken")

    state = {"project_id": "p-1", "cost_matches": [{"total_cost": 100}]}
    with patch("app.services.agents.estimating_agent.run_monte_carlo", boom):
        out = await run_monte_carlo_node(state)
    assert out["monte_carlo_results"] is None
    assert out["status"] == "monte_carlo_failed"


# =========================================================================
# compile_estimate_node — confidence tiers + recommended_total
# =========================================================================


@pytest.mark.asyncio
async def test_compile_uses_mc_p50_when_available():
    """[business invariant] When Monte Carlo P50 is available, use it
    as the recommended total. Pin so a refactor doesn't fall back
    to line_item_total when MC was successful."""
    state = {
        "project_id": "p-1",
        "cost_matches": [{"total_cost": 100}],
        "parametric_estimate": {"total_predicted_cost": 100},
        "monte_carlo_results": {"p50": 110, "p90": 130, "mean": 115},
    }
    out = await compile_estimate_node(state)
    assert out["final_estimate"]["recommended_total"] == 110.0


@pytest.mark.asyncio
async def test_compile_falls_back_to_line_item_when_mc_missing():
    """[fallback] No MC -> use line item total."""
    state = {
        "project_id": "p-1",
        "cost_matches": [{"total_cost": 1000}, {"total_cost": 500}],
        "parametric_estimate": {"total_predicted_cost": 1500},
        "monte_carlo_results": None,
    }
    out = await compile_estimate_node(state)
    assert out["final_estimate"]["recommended_total"] == 1500.0


@pytest.mark.asyncio
async def test_compile_confidence_high_under_10pct_variance():
    """[business invariant] line vs parametric within 10% -> 'high'.
    Pin so a refactor doesn't widen the band silently."""
    state = {
        "project_id": "p-1",
        "cost_matches": [{"total_cost": 1000}],
        "parametric_estimate": {"total_predicted_cost": 1050},  # 5% variance
        "monte_carlo_results": None,
    }
    out = await compile_estimate_node(state)
    assert out["final_estimate"]["confidence"] == "high"


@pytest.mark.asyncio
async def test_compile_confidence_medium_under_25pct_variance():
    state = {
        "project_id": "p-1",
        "cost_matches": [{"total_cost": 1000}],
        "parametric_estimate": {"total_predicted_cost": 1200},  # 20% variance
        "monte_carlo_results": None,
    }
    out = await compile_estimate_node(state)
    assert out["final_estimate"]["confidence"] == "medium"


@pytest.mark.asyncio
async def test_compile_confidence_low_above_25pct_variance():
    state = {
        "project_id": "p-1",
        "cost_matches": [{"total_cost": 1000}],
        "parametric_estimate": {"total_predicted_cost": 1500},  # 50% variance
        "monte_carlo_results": None,
    }
    out = await compile_estimate_node(state)
    assert out["final_estimate"]["confidence"] == "low"


@pytest.mark.asyncio
async def test_compile_confidence_medium_when_no_parametric():
    """[fallback] No parametric (or zero line items) -> 'medium'
    confidence (we have insufficient data to claim 'high' or 'low')."""
    state = {
        "project_id": "p-1",
        "cost_matches": [{"total_cost": 1000}],
        "parametric_estimate": None,
        "monte_carlo_results": None,
    }
    out = await compile_estimate_node(state)
    assert out["final_estimate"]["confidence"] == "medium"


@pytest.mark.asyncio
async def test_compile_rounds_all_dollar_values_to_2_decimals():
    """[contract] All dollar fields are rounded to 2 decimal places
    (cents precision). Pin so a refactor doesn't show full-precision
    floats in the UI."""
    state = {
        "project_id": "p-1",
        "cost_matches": [{"total_cost": 1234.56789}],
        "parametric_estimate": {"total_predicted_cost": 1234.5678},
        "monte_carlo_results": {"p50": 1100.999, "p90": 1300.001, "mean": 1200.555},
    }
    out = await compile_estimate_node(state)
    e = out["final_estimate"]
    # Verify max 2 decimal places:
    for key, val in e.items():
        if isinstance(val, int | float):
            assert abs(val - round(val, 2)) < 1e-9, f"{key} not rounded: {val}"


@pytest.mark.asyncio
async def test_compile_includes_canonical_keys():
    """[contract] Pin the final_estimate dict shape — UI rendering
    depends on these exact keys."""
    state = {
        "project_id": "p-1",
        "estimate_type": "detailed",
        "cost_matches": [{"total_cost": 1000}],
        "parametric_estimate": {"total_predicted_cost": 1000},
        "monte_carlo_results": {"p50": 1100, "p90": 1300, "mean": 1200},
    }
    out = await compile_estimate_node(state)
    e = out["final_estimate"]
    expected = {
        "project_id",
        "estimate_type",
        "line_item_total",
        "line_item_count",
        "parametric_total",
        "monte_carlo",
        "recommended_total",
        "confidence",
        "summary",
    }
    assert expected <= set(e)


@pytest.mark.asyncio
async def test_compile_estimate_type_passes_through():
    state = {
        "project_id": "p-1",
        "estimate_type": "schematic",
        "cost_matches": [{"total_cost": 1}],
        "parametric_estimate": None,
        "monte_carlo_results": None,
    }
    out = await compile_estimate_node(state)
    assert out["final_estimate"]["estimate_type"] == "schematic"


# =========================================================================
# Graph build
# =========================================================================


def test_build_estimating_agent_returns_compiled_graph():
    graph = build_estimating_agent()
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    assert {
        "extract_quantities",
        "match_costs",
        "run_parametric",
        "run_monte_carlo",
        "compile_estimate",
    } <= nodes
