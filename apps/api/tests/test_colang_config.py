"""Tests for the NeMo Guardrails Colang topic-enforcement config.

Pin the documented allowed-topics list, the construction keyword
set used for keyword-matching fallback, and the output flow names
(no_pii_in_reports, no_legal_advice).
"""

from __future__ import annotations

import pytest

from app.services.guardrails.colang_config import (
    COLANG_CONFIG,
    check_topic_allowed,
    get_colang_config,
)

# =========================================================================
# COLANG_CONFIG structure
# =========================================================================


def test_config_has_input_and_output_rails():
    """[contract] Colang config has both input rails (topic check)
    and output rails (PII + legal advice). Pin so a refactor
    doesn't drop a side."""
    assert "input" in COLANG_CONFIG["rails"]
    assert "output" in COLANG_CONFIG["rails"]


def test_input_topic_check_flow_present():
    """Input rails include the construction_topic_check flow."""
    flows = COLANG_CONFIG["rails"]["input"]["flows"]
    flow_names = {f["name"] for f in flows}
    assert "construction_topic_check" in flow_names


def test_output_flows_no_pii_and_no_legal_advice():
    """[business invariant] Output rails enforce 2 documented
    safeguards: no PII in reports, no legal advice. Pin so a
    refactor doesn't silently drop one (regulatory/liability risk)."""
    flows = COLANG_CONFIG["rails"]["output"]["flows"]
    flow_names = {f["name"] for f in flows}
    assert "no_pii_in_reports" in flow_names
    assert "no_legal_advice" in flow_names


def test_allowed_topics_canonical_set():
    """[contract] Pin the 11 documented allowed topics. Refactor
    must NOT silently add (broadens scope) or drop (breaks UX)."""
    flow = COLANG_CONFIG["rails"]["input"]["flows"][0]
    expected = {
        "construction",
        "project management",
        "cost estimation",
        "scheduling",
        "safety",
        "quality control",
        "procurement",
        "logistics",
        "document management",
        "compliance",
        "reporting",
    }
    assert set(flow["allowed_topics"]) == expected


# =========================================================================
# get_colang_config
# =========================================================================


def test_get_colang_config_returns_module_constant():
    """get_colang_config() returns the module-level COLANG_CONFIG.
    Pin so callers always get the same instance (no copies)."""
    out = get_colang_config()
    assert out is COLANG_CONFIG


# =========================================================================
# check_topic_allowed — keyword fallback
# =========================================================================


@pytest.mark.asyncio
async def test_topic_allowed_construction_keyword():
    out = await check_topic_allowed("Tell me about construction safety")
    assert out["allowed"] is True
    assert "construction" in out["matched_topics"]


@pytest.mark.asyncio
async def test_topic_allowed_multiple_matches():
    """Multiple keyword matches all returned."""
    out = await check_topic_allowed("Generate a cost estimate for the foundation")
    assert out["allowed"] is True
    matched = set(out["matched_topics"])
    # 'cost', 'estimate', 'foundation' are all keywords:
    assert "cost" in matched
    assert "estimate" in matched
    assert "foundation" in matched


@pytest.mark.asyncio
async def test_topic_allowed_case_insensitive():
    """[contract] Matching is case-insensitive."""
    out = await check_topic_allowed("CONCRETE STRENGTH")
    assert out["allowed"] is True
    assert "concrete" in out["matched_topics"]


@pytest.mark.asyncio
async def test_topic_blocked_when_no_keywords_match():
    """[security] No keyword match -> allowed=False with explanatory
    message. Pin: refactor must NOT default to allowed=True (would
    let unrelated queries through)."""
    out = await check_topic_allowed("What is the capital of France?")
    assert out["allowed"] is False
    assert "construction" in out["message"].lower()


@pytest.mark.asyncio
async def test_topic_blocked_empty_query():
    """[edge case] Empty query has no keywords -> blocked."""
    out = await check_topic_allowed("")
    assert out["allowed"] is False


@pytest.mark.asyncio
async def test_topic_allowed_trade_keywords():
    """[invariant] Trade keywords are explicitly allowed:
    concrete, steel, masonry, electrical, plumbing, hvac. Pin so
    a refactor doesn't break trade-specific queries."""
    for trade in ("concrete", "steel", "electrical", "plumbing", "hvac"):
        out = await check_topic_allowed(f"Tell me about {trade}")
        assert out["allowed"] is True, f"trade '{trade}' should be allowed"


@pytest.mark.asyncio
async def test_topic_allowed_phase_keywords():
    """[invariant] Construction phase keywords are allowed:
    excavation, foundation, framing."""
    for phase in ("excavation", "foundation", "framing"):
        out = await check_topic_allowed(f"During {phase}")
        assert out["allowed"] is True


@pytest.mark.asyncio
async def test_topic_allowed_artifact_keywords():
    """[invariant] Construction artifact keywords are allowed:
    bid, submittal, rfi, inspection, defect."""
    for artifact in ("bid", "submittal", "rfi", "inspection", "defect"):
        out = await check_topic_allowed(f"Find the {artifact}")
        assert out["allowed"] is True


@pytest.mark.asyncio
async def test_topic_match_on_substring():
    """[fallback] Keyword matching is substring-based — 'projects'
    matches 'project'."""
    out = await check_topic_allowed("ProjectsList")
    assert out["allowed"] is True
    assert "project" in out["matched_topics"]


@pytest.mark.asyncio
async def test_topic_response_dict_keys():
    """[contract] Allowed responses have 'allowed' + 'matched_topics'.
    Blocked responses have 'allowed' + 'message'. Pin: callers
    rely on these key names."""
    allowed = await check_topic_allowed("construction")
    blocked = await check_topic_allowed("xyz unrelated")
    assert "allowed" in allowed
    assert "matched_topics" in allowed
    assert "allowed" in blocked
    assert "message" in blocked
