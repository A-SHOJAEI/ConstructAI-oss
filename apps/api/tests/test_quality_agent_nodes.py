"""Tests for the quality management LangGraph agent nodes.

Pin per-node behavior + the NCR recommendation thresholds (only
critical/major defects + only 'warning' compliance checks become
NCRs) + per-node error isolation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.agents.quality_agent import (
    build_quality_agent,
    check_compliance_node,
    classify_defects_node,
    recommend_ncrs_node,
)

# =========================================================================
# classify_defects_node
# =========================================================================


@pytest.mark.asyncio
async def test_classify_defects_with_no_images_short_circuits():
    """[edge case] No images -> empty list, status='no_images' (NOT
    'classification_failed' — empty input is not a failure)."""
    state = {"project_id": "p-1", "images": []}
    out = await classify_defects_node(state)
    assert out["defect_results"] == []
    assert out["status"] == "no_images"


@pytest.mark.asyncio
async def test_classify_defects_runs_per_image():
    """Each image is classified independently."""
    fake = AsyncMock(return_value={"defect_type": "crack", "severity_estimate": "critical"})
    with patch("app.services.agents.quality_agent._classifier.classify", fake):
        out = await classify_defects_node(
            {"project_id": "p-1", "images": [b"img-1", b"img-2", b"img-3"]}
        )
    assert len(out["defect_results"]) == 3
    assert fake.call_count == 3
    assert out["status"] == "defects_classified"


@pytest.mark.asyncio
async def test_classify_defects_failure_isolated():
    fake = AsyncMock(side_effect=RuntimeError("model crashed"))
    with patch("app.services.agents.quality_agent._classifier.classify", fake):
        out = await classify_defects_node({"project_id": "p-1", "images": [b"img-1"]})
    assert out["defect_results"] is None
    assert out["status"] == "classification_failed"
    assert "model crashed" in out["error"]


# =========================================================================
# check_compliance_node
# =========================================================================


@pytest.mark.asyncio
async def test_check_compliance_passes_inspection_data():
    captured = {}

    async def fake_check(*, project_id, project_data):
        captured.update({"project_id": project_id, "project_data": project_data})
        return [{"regulation_code": "1926.501", "status": "compliant"}]

    state = {
        "project_id": "p-1",
        "inspection_data": {"phase": "structural", "activity": "fall_protection"},
    }
    with patch(
        "app.services.agents.quality_agent.check_project_compliance",
        fake_check,
    ):
        out = await check_compliance_node(state)

    assert captured["project_id"] == "p-1"
    assert captured["project_data"]["phase"] == "structural"
    assert out["status"] == "compliance_checked"
    assert len(out["compliance_results"]) == 1


@pytest.mark.asyncio
async def test_check_compliance_no_inspection_data_uses_empty_dict():
    """[edge case] Missing inspection_data -> empty dict passed to
    checker (don't crash on .get())."""
    captured = {}

    async def fake_check(*, project_data, **_kwargs):
        captured["project_data"] = project_data
        return []

    state = {"project_id": "p-1"}
    with patch(
        "app.services.agents.quality_agent.check_project_compliance",
        fake_check,
    ):
        await check_compliance_node(state)

    assert captured["project_data"] == {}


@pytest.mark.asyncio
async def test_check_compliance_failure_isolated():
    async def boom(**_kwargs):
        raise RuntimeError("compliance DB down")

    state = {"project_id": "p-1", "inspection_data": {}}
    with patch("app.services.agents.quality_agent.check_project_compliance", boom):
        out = await check_compliance_node(state)

    assert out["compliance_results"] is None
    assert out["status"] == "compliance_failed"
    assert "compliance DB down" in out["error"]


# =========================================================================
# recommend_ncrs_node — severity threshold pin
# =========================================================================


@pytest.mark.asyncio
async def test_ncrs_only_for_critical_or_major_defects():
    """[business invariant] NCRs are issued for critical AND major
    defects only — minor defects are NOT escalated. Pin so a
    refactor doesn't accidentally widen this (NCR has cost)."""
    state = {
        "defect_results": [
            {"severity_estimate": "critical", "defect_type": "crack"},
            {"severity_estimate": "major", "defect_type": "spalling"},
            {"severity_estimate": "minor", "defect_type": "stain"},
            {"severity_estimate": "none", "defect_type": "no_defect"},
        ],
        "compliance_results": [],
    }
    out = await recommend_ncrs_node(state)
    assert len(out["ncr_recommendations"]) == 2
    sources = {r["source"] for r in out["ncr_recommendations"]}
    assert sources == {"defect_detection"}
    severities = {r["severity"] for r in out["ncr_recommendations"]}
    assert severities == {"critical", "major"}


@pytest.mark.asyncio
async def test_ncrs_only_for_warning_compliance_status():
    """[business invariant] NCRs from compliance checks fire ONLY on
    'warning' status — 'compliant' / 'fail' / 'unknown' do NOT
    trigger NCR. Pin: refactor must not silently expand the trigger
    set."""
    state = {
        "defect_results": [],
        "compliance_results": [
            {
                "status": "warning",
                "regulation_code": "1926.501",
                "regulation_title": "Fall Protection",
            },
            {"status": "compliant", "regulation_code": "1926.500"},
            {"status": "unknown", "regulation_code": "1926.502"},
            {"status": "fail", "regulation_code": "1926.503"},
        ],
    }
    out = await recommend_ncrs_node(state)
    assert len(out["ncr_recommendations"]) == 1
    rec = out["ncr_recommendations"][0]
    assert rec["source"] == "compliance_check"
    assert rec["regulation"] == "1926.501"


@pytest.mark.asyncio
async def test_ncrs_combined_defects_and_compliance():
    """Both sources contribute to the NCR list."""
    state = {
        "defect_results": [{"severity_estimate": "critical", "defect_type": "exposed_rebar"}],
        "compliance_results": [
            {
                "status": "warning",
                "regulation_code": "1926.501",
                "regulation_title": "Fall Protection",
            }
        ],
    }
    out = await recommend_ncrs_node(state)
    assert len(out["ncr_recommendations"]) == 2
    sources = {r["source"] for r in out["ncr_recommendations"]}
    assert sources == {"defect_detection", "compliance_check"}


@pytest.mark.asyncio
async def test_ncrs_handles_none_defect_or_compliance():
    """[edge case] Defect/compliance results None (prior node
    failed) -> empty NCR list, status='ncrs_recommended' (don't
    crash)."""
    state = {"defect_results": None, "compliance_results": None}
    out = await recommend_ncrs_node(state)
    assert out["ncr_recommendations"] == []
    assert out["status"] == "ncrs_recommended"


@pytest.mark.asyncio
async def test_ncrs_recommendation_includes_severity_in_message():
    """Recommendation message format pins the severity verbatim."""
    state = {
        "defect_results": [{"severity_estimate": "critical", "defect_type": "crack"}],
        "compliance_results": [],
    }
    out = await recommend_ncrs_node(state)
    rec = out["ncr_recommendations"][0]
    assert "critical" in rec["recommendation"]
    assert "crack" in rec["recommendation"]


# =========================================================================
# Graph build
# =========================================================================


def test_build_quality_agent_returns_compiled_graph():
    graph = build_quality_agent()
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    assert {"classify_defects", "check_compliance", "recommend_ncrs"} <= nodes


def test_build_quality_agent_has_documented_sequential_flow():
    """[contract] classify_defects -> check_compliance -> recommend_ncrs
    sequential. recommend_ncrs needs both prior results, so the order
    is load-bearing."""
    graph = build_quality_agent()
    g = graph.get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    assert ("classify_defects", "check_compliance") in edges
    assert ("check_compliance", "recommend_ncrs") in edges
