"""Tests for confidence scoring and routing."""

from __future__ import annotations

from app.services.guardrails.confidence_scorer import (
    ConfidenceScorer,
)
from app.services.guardrails.routing_decision import (
    decide_route,
)


class TestConfidenceScorer:
    async def test_empty_output(self):
        scorer = ConfidenceScorer()
        result = await scorer.score({}, "daily_report")
        assert result["overall_confidence"] == 1.0
        assert result["routing_recommendation"] == "auto_approve"

    async def test_numeric_claims(self):
        scorer = ConfidenceScorer()
        result = await scorer.score(
            {"cost": 50000, "duration": 30},
            "cost_estimate",
        )
        assert 0.0 <= result["overall_confidence"] <= 1.0
        assert len(result["claim_scores"]) == 2

    async def test_text_claims(self):
        scorer = ConfidenceScorer()
        result = await scorer.score(
            {"description": "This is a detailed description of work"},
            "daily_report",
        )
        assert len(result["claim_scores"]) >= 1

    async def test_safety_lower_confidence(self):
        scorer = ConfidenceScorer()
        result = await scorer.score(
            {"severity": 3},
            "safety_alert",
        )
        safety_conf = result["overall_confidence"]

        result2 = await scorer.score(
            {"severity": 3},
            "daily_report",
        )
        report_conf = result2["overall_confidence"]
        assert safety_conf < report_conf


class TestRoutingDecision:
    def test_auto_approve_high_confidence(self):
        decision = decide_route(0.95, "daily_report", [])
        assert decision == "auto_approve"

    def test_human_review_medium_confidence(self):
        decision = decide_route(0.75, "cost_estimate", [])
        assert decision == "human_review"

    def test_expert_escalation_low_confidence(self):
        decision = decide_route(0.30, "cost_estimate", [])
        assert decision == "expert_escalation"

    def test_always_human_review(self):
        decision = decide_route(
            0.99,
            "change_order_impact",
            [],
        )
        assert decision == "human_review"

    def test_always_human_rfi(self):
        decision = decide_route(0.99, "rfi_draft", [])
        assert decision == "human_review"

    def test_errors_force_escalation(self):
        errors = [{"severity": "error", "message": "bad"}]
        decision = decide_route(0.99, "daily_report", errors)
        assert decision == "expert_escalation"

    def test_safety_high_threshold(self):
        # 0.90 is below safety auto threshold of 0.95
        decision = decide_route(0.90, "safety_alert", [])
        assert decision == "human_review"

    def test_unknown_agent_uses_default(self):
        # L-12: unknown agent types default to human review regardless of
        # confidence — never auto-approved without an explicit threshold.
        decision = decide_route(
            0.90,
            "unknown_agent_type",
            [],
        )
        assert decision == "human_review"
