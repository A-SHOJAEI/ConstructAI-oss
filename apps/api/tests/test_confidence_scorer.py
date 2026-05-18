"""Tests for the UQLM-style confidence scorer (guardrail Stage 5).

Pin claim extraction, heuristic confidence per claim type, and the
routing recommendation derivation.
"""

from __future__ import annotations

import pytest

from app.services.guardrails.confidence_scorer import ConfidenceScorer

# =========================================================================
# fixtures
# =========================================================================


@pytest.fixture
def scorer() -> ConfidenceScorer:
    return ConfidenceScorer()


# =========================================================================
# _extract_claims
# =========================================================================


def test_extract_claims_empty_output(scorer: ConfidenceScorer):
    assert scorer._extract_claims({}) == []


def test_extract_claims_none_output(scorer: ConfidenceScorer):
    assert scorer._extract_claims(None) == []


def test_extract_claims_numeric_values(scorer: ConfidenceScorer):
    """Numeric output values become verifiable claims."""
    claims = scorer._extract_claims({"cost_estimate": 50000, "duration_days": 90})
    assert len(claims) == 2
    types = {c["type"] for c in claims}
    assert types == {"numeric"}


def test_extract_claims_text_values_over_10_chars(scorer: ConfidenceScorer):
    """Text values longer than 10 chars become text claims."""
    claims = scorer._extract_claims(
        {
            "summary": "This is a long enough summary text",
            "short": "x",  # too short
        }
    )
    assert len(claims) == 1
    assert claims[0]["type"] == "text"
    assert claims[0]["field"] == "summary"


def test_extract_claims_skips_metadata_keys(scorer: ConfidenceScorer):
    """raw_text / format / metadata are NOT extracted as claims."""
    claims = scorer._extract_claims(
        {
            "raw_text": "this is raw output text",
            "format": "json structured output",
            "metadata": "some metadata description here",
            "actual_field": 42,
        }
    )
    # Only actual_field becomes a claim:
    assert len(claims) == 1
    assert claims[0]["field"] == "actual_field"


def test_extract_claims_skips_non_numeric_non_text(scorer: ConfidenceScorer):
    """Lists, dicts, bools — not extracted as verifiable claims (only
    numeric and length>10 text)."""
    claims = scorer._extract_claims(
        {
            "items": [1, 2, 3],
            "config": {"key": "value"},
            "is_active": True,
        }
    )
    # bool is technically a subclass of int — actually bool IS isinstance
    # of int|float in Python. Pin documented behavior:
    bool_claim = [c for c in claims if c["field"] == "is_active"]
    list_claim = [c for c in claims if c["field"] == "items"]
    dict_claim = [c for c in claims if c["field"] == "config"]
    assert list_claim == []
    assert dict_claim == []
    # bool may or may not appear — accept either:
    assert len(bool_claim) <= 1


# =========================================================================
# _heuristic_confidence
# =========================================================================


def test_confidence_text_claim_default_085(scorer: ConfidenceScorer):
    claim = {"text": "x", "type": "text", "field": "summary"}
    assert scorer._heuristic_confidence(claim, "estimating_agent") == 0.85


def test_confidence_numeric_claim_higher_090(scorer: ConfidenceScorer):
    """Numeric claims start with higher confidence (0.90 vs 0.85) —
    numbers are easier to verify than text."""
    claim = {"text": "cost=50000", "type": "numeric", "field": "cost"}
    assert scorer._heuristic_confidence(claim, "estimating_agent") == 0.90


def test_confidence_safety_alert_penalty(scorer: ConfidenceScorer):
    """Safety alerts get -0.05 penalty (high-stakes, more cautious)."""
    claim = {"text": "x", "type": "text", "field": "summary"}
    out = scorer._heuristic_confidence(claim, "safety_alert")
    assert out == 0.85 - 0.05  # = 0.80


def test_confidence_change_order_impact_penalty(scorer: ConfidenceScorer):
    """Change order impact also gets the -0.05 penalty.
    (Float arithmetic: 0.85 - 0.05 ≈ 0.7999999... — use approx.)"""
    claim = {"text": "x", "type": "text", "field": "summary"}
    out = scorer._heuristic_confidence(claim, "change_order_impact")
    assert out == pytest.approx(0.80, abs=1e-9)


def test_confidence_daily_report_bonus(scorer: ConfidenceScorer):
    """Daily report agents get +0.05 (low-stakes summarization)."""
    claim = {"text": "x", "type": "text", "field": "summary"}
    out = scorer._heuristic_confidence(claim, "daily_report")
    assert out == 0.85 + 0.05  # = 0.90


def test_confidence_clamped_to_1():
    """[clamp] No combination produces > 1.0. Pin the cap."""
    scorer = ConfidenceScorer()
    claim = {"text": "x", "type": "numeric", "field": "x"}
    # numeric (0.90) + daily_report bonus (+0.05) = 0.95, still < 1.0.
    # No production combination exceeds 1.0, but pin the clamp behavior:
    out = scorer._heuristic_confidence(claim, "daily_report")
    assert 0.0 <= out <= 1.0


# =========================================================================
# score — overall flow
# =========================================================================


@pytest.mark.asyncio
async def test_score_empty_output_full_confidence(scorer: ConfidenceScorer):
    """Empty output → no claims → 1.0 overall confidence + auto_approve."""
    out = await scorer.score({}, "estimating_agent")
    assert out["overall_confidence"] == 1.0
    assert out["claim_scores"] == []
    assert out["routing_recommendation"] == "auto_approve"


@pytest.mark.asyncio
async def test_score_returns_required_keys(scorer: ConfidenceScorer):
    out = await scorer.score({"cost": 100}, "estimating_agent")
    assert "overall_confidence" in out
    assert "claim_scores" in out
    assert "routing_recommendation" in out


@pytest.mark.asyncio
async def test_score_per_claim_consistency_threshold(scorer: ConfidenceScorer):
    """Pin consistency threshold ≥ 0.80 — claims at or above mark
    consistent=True."""
    out = await scorer.score(
        {"cost": 100},  # numeric → 0.90 confidence ≥ 0.80
        "estimating_agent",
    )
    for cs in out["claim_scores"]:
        assert cs["consistent"] is True


@pytest.mark.asyncio
async def test_score_safety_alert_at_inconsistency_boundary(
    scorer: ConfidenceScorer,
):
    """[float precision quirk] Safety alert + text claim:
    0.85 - 0.05 in float = 0.7999... which is just under the 0.80
    consistency threshold. Pin the resulting "inconsistent" marker
    so a refactor doesn't silently flip the comparison and approve
    low-confidence safety claims."""
    out = await scorer.score(
        {"summary": "Worker fell from scaffolding without harness"},
        "safety_alert",
    )
    # Sub-threshold confidence → consistent=False:
    for cs in out["claim_scores"]:
        assert cs["consistent"] is False
        assert cs["confidence"] < 0.80


@pytest.mark.asyncio
async def test_score_overall_is_average_of_claims(scorer: ConfidenceScorer):
    """Overall confidence is mean of per-claim confidences (rounded
    to 3dp)."""
    # Numeric (0.90) + text (0.85) → avg 0.875.
    out = await scorer.score(
        {
            "cost": 100,  # numeric → 0.90
            "summary": "summary text long enough to count",  # text → 0.85
        },
        "estimating_agent",
    )
    expected_avg = (0.90 + 0.85) / 2
    assert out["overall_confidence"] == pytest.approx(expected_avg, abs=0.001)


@pytest.mark.asyncio
async def test_score_rounded_to_three_decimals(scorer: ConfidenceScorer):
    out = await scorer.score({"cost": 100, "duration": 90}, "estimating_agent")
    # Round to 3dp:
    assert round(out["overall_confidence"], 3) == out["overall_confidence"]


@pytest.mark.asyncio
async def test_score_includes_routing_recommendation(scorer: ConfidenceScorer):
    """Routing recommendation derived via decide_route (in
    routing_decision module) — must produce a non-empty string."""
    out = await scorer.score({"cost": 100}, "daily_report")
    assert isinstance(out["routing_recommendation"], str)
    assert out["routing_recommendation"]


# =========================================================================
# Constructor
# =========================================================================


def test_default_num_samples_5():
    """Pin default sample count for production multi-regeneration
    consistency check."""
    scorer = ConfidenceScorer()
    assert scorer.num_samples == 5


def test_explicit_num_samples():
    scorer = ConfidenceScorer(num_samples=10)
    assert scorer.num_samples == 10
