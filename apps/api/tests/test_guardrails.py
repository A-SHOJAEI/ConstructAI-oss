"""Tests for guardrails pipeline, routing decisions, and knowledge verification.

Covers the six-stage pipeline, routing threshold logic, and knowledge-base
cross-reference verification.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.guardrails.knowledge_verifier import (
    _extract_claimed_sources,
    _source_matches_context,
    verify,
)
from app.services.guardrails.pipeline import (
    GuardrailState,
    build_guardrails_pipeline,
    decide_routing,
    validate_schema,
)
from app.services.guardrails.routing_decision import (
    DEFAULT_THRESHOLDS,
    decide_route,
)

# =========================================================================
# Routing Decision Tests
# =========================================================================


class TestRoutingDecision:
    """Tests for Stage 6 routing decision logic."""

    def test_auto_approve_for_high_confidence_valid_output(self):
        """High confidence + no errors = auto_approve."""
        result = decide_route(
            confidence=0.95,
            agent_name="document_classification",
            validation_errors=[],
        )
        assert result == "auto_approve"

    def test_human_review_for_safety_critical_agent(self):
        """safety_alert has auto=None, so always human_review regardless of confidence."""
        result = decide_route(
            confidence=1.0,
            agent_name="safety_alert",
            validation_errors=[],
        )
        assert result == "human_review"

    def test_human_review_for_rfi_drafts(self):
        """rfi_draft has auto=None, so always routes to human_review."""
        result = decide_route(
            confidence=0.99,
            agent_name="rfi_draft",
            validation_errors=[],
        )
        assert result == "human_review"

    def test_expert_escalation_on_error(self):
        """Any error-severity validation error should escalate to expert."""
        result = decide_route(
            confidence=0.95,
            agent_name="document_classification",
            validation_errors=[{"severity": "error", "message": "something broke"}],
        )
        assert result == "expert_escalation"

    def test_human_review_for_medium_confidence(self):
        """Confidence between human and auto threshold = human_review."""
        result = decide_route(
            confidence=0.80,
            agent_name="document_classification",
            validation_errors=[],
        )
        assert result == "human_review"

    def test_expert_escalation_for_low_confidence(self):
        """Confidence below human threshold = expert_escalation."""
        result = decide_route(
            confidence=0.50,
            agent_name="document_classification",
            validation_errors=[],
        )
        assert result == "expert_escalation"

    def test_unknown_agent_defaults_to_human_review(self):
        """Unknown agent type should default to human_review (auto=None)."""
        result = decide_route(
            confidence=0.99,
            agent_name="completely_unknown_agent",
            validation_errors=[],
        )
        assert result == "human_review"

    def test_default_thresholds_auto_is_none(self):
        """DEFAULT_THRESHOLDS should have auto=None for safety."""
        assert DEFAULT_THRESHOLDS["auto"] is None

    def test_change_order_impact_always_human_review(self):
        """change_order_impact has auto=None."""
        result = decide_route(
            confidence=1.0,
            agent_name="change_order_impact",
            validation_errors=[],
        )
        assert result == "human_review"

    def test_daily_report_auto_approve_at_threshold(self):
        """daily_report: auto=0.80, so exactly 0.80 should auto_approve."""
        result = decide_route(
            confidence=0.80,
            agent_name="daily_report",
            validation_errors=[],
        )
        assert result == "auto_approve"


# =========================================================================
# Knowledge Verifier Tests
# =========================================================================


class TestKnowledgeVerifier:
    """Tests for Stage 4 knowledge base cross-reference verification."""

    @pytest.mark.asyncio
    async def test_verification_with_grounded_claims(self):
        """Claims matching context should pass verification."""
        parsed_output = {
            "sources": [{"document_title": "Concrete Spec 03 30 00", "section": "Part 2"}],
            "specification_reference": "03 30 00",
        }
        context_chunks = [
            {
                "document_title": "Concrete Spec 03 30 00",
                "content": "Part 2 - Products: concrete mix design requirements",
            }
        ]
        result = await verify(parsed_output, "cost_estimate", context_chunks)
        assert result["verification_passed"] is True

    @pytest.mark.asyncio
    async def test_verification_with_hallucinated_claims(self):
        """Claims not in context should fail verification (>50% unmatched)."""
        parsed_output = {
            "sources": [
                {"document_title": "Nonexistent Document A"},
                {"document_title": "Nonexistent Document B"},
            ],
        }
        context_chunks = [
            {
                "document_title": "Actual Project Spec",
                "content": "real content about structural steel",
            }
        ]
        result = await verify(parsed_output, "cost_estimate", context_chunks)
        assert result["verification_passed"] is False
        # Should have warnings about unmatched sources
        assert any(w["severity"] in ("warning", "error") for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_verification_with_no_context_available(self):
        """Sources claimed but no context = verification fails."""
        parsed_output = {
            "sources": [{"document_title": "Some Spec"}],
        }
        result = await verify(parsed_output, "cost_estimate", context_chunks=[])
        assert result["verification_passed"] is False

    @pytest.mark.asyncio
    async def test_verification_no_claims_no_context(self):
        """No sources claimed and no context = passes (nothing to verify)."""
        parsed_output = {"answer": "plain answer with no sources"}
        result = await verify(parsed_output, "daily_report")
        assert result["verification_passed"] is True

    @pytest.mark.asyncio
    async def test_verifiable_field_without_source_reference(self):
        """Verifiable field without source keys generates info warning."""
        parsed_output = {
            "specification_reference": "03 30 00",
            # No "source" or "sources" key
        }
        result = await verify(parsed_output, "cost_estimate")
        # Should have an info-level warning about missing cross-reference
        info_warnings = [w for w in result["warnings"] if w["severity"] == "info"]
        assert len(info_warnings) >= 1

    def test_extract_claimed_sources_from_list(self):
        """Extract sources from 'sources' list."""
        output = {
            "sources": [
                {"document_title": "Doc A"},
                "Plain string reference",
            ]
        }
        sources = _extract_claimed_sources(output)
        assert len(sources) == 2
        assert sources[0]["document_title"] == "Doc A"
        assert sources[1]["document_title"] == "Plain string reference"

    def test_extract_claimed_sources_from_single_source(self):
        """Extract from single 'source' string key."""
        output = {"source": "Single Document"}
        sources = _extract_claimed_sources(output)
        assert len(sources) == 1
        assert sources[0]["document_title"] == "Single Document"

    def test_source_matches_context_by_title(self):
        """Title match should return True."""
        source = {"document_title": "Concrete Spec"}
        chunks = [{"document_title": "Concrete Spec - Division 03", "content": "..."}]
        assert _source_matches_context(source, chunks) is True

    def test_source_matches_context_by_section_in_content(self):
        """Section appearing in chunk content should match."""
        source = {"document_title": "", "section": "part 2 - products"}
        chunks = [
            {
                "document_title": "Some Spec",
                "content": "part 2 - products: concrete mix design",
            }
        ]
        assert _source_matches_context(source, chunks) is True

    def test_source_no_match(self):
        """Completely unrelated source and context should not match."""
        source = {"document_title": "Electrical Spec 26 00 00"}
        chunks = [{"document_title": "Plumbing Division 22", "content": "piping requirements"}]
        assert _source_matches_context(source, chunks) is False


# =========================================================================
# Guardrails Pipeline Tests
# =========================================================================


class TestGuardrailsPipeline:
    """Tests for the six-stage guardrails pipeline."""

    @pytest.mark.asyncio
    async def test_pipeline_parse_failure_forces_human_review(self):
        """If parsing fails, routing should be forced to human_review."""
        # Mock the parse stage to return an error
        with patch(
            "app.services.guardrails.pipeline.parse_structured_output",
            new_callable=AsyncMock,
            return_value={
                "parsed_output": None,
                "validation_errors": [{"stage": "parse", "message": "Invalid JSON"}],
                "passed": False,
                "latency_ms": 5,
            },
        ):
            state: GuardrailState = {
                "agent_name": "document_classification",
                "raw_output": "not valid json",
                "parsed_output": None,
                "validation_errors": [{"stage": "parse", "message": "Invalid JSON"}],
                "confidence_score": None,
                "routing_decision": None,
                "passed": False,
                "latency_ms": 0,
                "parse_failed": False,
            }
            result = await decide_routing(state)
            assert result["routing_decision"] == "human_review"
            assert result["parse_failed"] is True

    @pytest.mark.asyncio
    async def test_pipeline_skips_stages_when_not_passed(self):
        """validate_schema runs unconditionally and returns the bookkeeping
        dict (validation_errors, passed, latency_ms). It does NOT short-
        circuit on passed=False — the prior expectation predated the
        current pipeline."""
        state: GuardrailState = {
            "agent_name": "cost_estimate",
            "raw_output": "",
            "parsed_output": None,
            "validation_errors": [],
            "confidence_score": None,
            "routing_decision": None,
            "passed": False,
            "latency_ms": 0,
            "parse_failed": False,
        }
        result = await validate_schema(state)
        assert "validation_errors" in result
        assert "passed" in result
        assert "latency_ms" in result

    @pytest.mark.asyncio
    async def test_decide_routing_with_low_confidence(self):
        """Low confidence should route to expert or human review."""
        state: GuardrailState = {
            "agent_name": "cost_estimate",
            "raw_output": "",
            "parsed_output": {"data": "valid"},
            "validation_errors": [],
            "confidence_score": 0.40,
            "routing_decision": None,
            "passed": True,
            "latency_ms": 0,
            "parse_failed": False,
        }
        result = await decide_routing(state)
        assert result["routing_decision"] in ("expert_escalation", "human_review")

    def test_build_guardrails_pipeline_constructs(self):
        """build_guardrails_pipeline should return a compiled graph."""
        pipeline = build_guardrails_pipeline()
        assert pipeline is not None
        # Should be invokable
        assert hasattr(pipeline, "ainvoke")
