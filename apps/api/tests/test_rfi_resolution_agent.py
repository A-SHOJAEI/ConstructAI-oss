"""Tests for the RFI Resolution Agent — 3-stage LangGraph pipeline.

Test categories:
1. Stage 1 — Unnecessary RFI detection
2. Stage 2 — AI-assisted response drafting
3. Stage 3 — Response verification (hallucination, contradiction, completeness)
4. Graph construction and routing
5. API endpoints
6. Webhook integration
7. Resolution log model
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_PROJECT_ID = str(uuid.uuid4())
FAKE_RFI_ID = str(uuid.uuid4())


def _make_initial_state(**overrides):
    """Build a default RFIResolutionState dict for testing."""
    from app.services.agents.rfi_resolution_agent import RFIResolutionState

    base: RFIResolutionState = {
        "rfi_id": FAKE_RFI_ID,
        "project_id": FAKE_PROJECT_ID,
        "subject": "Concrete compressive strength",
        "question": "What is the required 28-day compressive strength for the slab-on-grade in Building A?",
        "spec_section": "03 30 00",
        "drawing_reference": "S-101",
        "similar_rfis": [],
        "spec_matches": [],
        "meeting_matches": [],
        "is_unnecessary": False,
        "unnecessary_reason": None,
        "unnecessary_source": None,
        "context_chunks": [],
        "osha_chunks": [],
        "draft_response": None,
        "draft_confidence": 0.0,
        "draft_sources": [],
        "draft_model": None,
        "hallucination_flags": [],
        "contradiction_flags": [],
        "completeness_flags": [],
        "verification_passed": False,
        "final_response": None,
        "stage_reached": 0,
        "status": "processing",
        "error": None,
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════════════
# 1. Safety keyword detection & sub-question extraction
# ═══════════════════════════════════════════════════════════════════════════


class TestSafetyDetection:
    """Test _is_safety_related() keyword matching."""

    def test_detects_osha_keyword(self):
        from app.services.agents.rfi_resolution_agent import _is_safety_related

        assert _is_safety_related("What OSHA standard applies to trenching?")

    def test_detects_fall_protection(self):
        from app.services.agents.rfi_resolution_agent import _is_safety_related

        assert _is_safety_related("Do we need fall protection on the roof?")

    def test_detects_scaffold(self):
        from app.services.agents.rfi_resolution_agent import _is_safety_related

        assert _is_safety_related("Is scaffolding required for the second floor?")

    def test_non_safety_question(self):
        from app.services.agents.rfi_resolution_agent import _is_safety_related

        assert not _is_safety_related("What is the concrete mix design?")

    def test_case_insensitive(self):
        from app.services.agents.rfi_resolution_agent import _is_safety_related

        assert _is_safety_related("PPE requirements for welding operations")

    def test_confined_space(self):
        from app.services.agents.rfi_resolution_agent import _is_safety_related

        assert _is_safety_related("Confined space entry procedures for utility vaults")


class TestSubQuestionExtraction:
    """Test _extract_sub_questions() multi-part splitting."""

    def test_single_question(self):
        from app.services.agents.rfi_resolution_agent import _extract_sub_questions

        result = _extract_sub_questions("What is the concrete strength?")
        assert len(result) == 1

    def test_numbered_questions(self):
        from app.services.agents.rfi_resolution_agent import _extract_sub_questions

        q = (
            "1. What is the required compressive strength?\n"
            "2. What is the slump requirement?\n"
            "3. What admixtures are acceptable?"
        )
        result = _extract_sub_questions(q)
        assert len(result) == 3

    def test_bullet_questions(self):
        from app.services.agents.rfi_resolution_agent import _extract_sub_questions

        q = (
            "- What is the required compressive strength?\n"
            "- What is the slump requirement?\n"
            "- What admixtures are acceptable?"
        )
        result = _extract_sub_questions(q)
        assert len(result) == 3

    def test_letter_questions(self):
        from app.services.agents.rfi_resolution_agent import _extract_sub_questions

        q = (
            "a. What is the required compressive strength for footings?\n"
            "b. What is the slump requirement for pumped concrete?\n"
        )
        result = _extract_sub_questions(q)
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 2. Stage 1 — Unnecessary RFI detection
# ═══════════════════════════════════════════════════════════════════════════


class TestUnnecessaryEvaluation:
    """Test evaluate_unnecessary_node() logic."""

    @pytest.mark.asyncio
    async def test_flags_duplicate_rfi_with_answer(self):
        from app.services.agents.rfi_resolution_agent import evaluate_unnecessary_node

        state = _make_initial_state(
            similar_rfis=[
                {
                    "rfi_number": "RFI-042",
                    "subject": "Concrete strength",
                    "question": "What is the 28-day strength?",
                    "answer": "4000 psi per Section 03 30 00, 2.1.A",
                    "similarity_score": 0.95,
                }
            ],
        )
        result = await evaluate_unnecessary_node(state)
        assert result["is_unnecessary"] is True
        assert result["unnecessary_source"] == "rfi"
        assert "RFI-042" in result["unnecessary_reason"]

    @pytest.mark.asyncio
    async def test_not_unnecessary_below_threshold(self):
        from app.services.agents.rfi_resolution_agent import evaluate_unnecessary_node

        state = _make_initial_state(
            similar_rfis=[
                {
                    "rfi_number": "RFI-043",
                    "subject": "Concrete strength",
                    "question": "What is the 28-day strength?",
                    "answer": "4000 psi",
                    "similarity_score": 0.82,  # Below 0.90 threshold
                }
            ],
        )
        result = await evaluate_unnecessary_node(state)
        assert result["is_unnecessary"] is False

    @pytest.mark.asyncio
    async def test_flags_spec_match(self):
        from app.services.agents.rfi_resolution_agent import evaluate_unnecessary_node

        state = _make_initial_state(
            spec_matches=[
                {
                    "document_title": "Project Specification Book",
                    "csi_section": "03 30 00",
                    "content": "Minimum compressive strength shall be 4000 psi at 28 days...",
                    "score": 0.95,
                }
            ],
        )
        result = await evaluate_unnecessary_node(state)
        assert result["is_unnecessary"] is True
        assert result["unnecessary_source"] == "spec"

    @pytest.mark.asyncio
    async def test_flags_meeting_minutes(self):
        from app.services.agents.rfi_resolution_agent import evaluate_unnecessary_node

        state = _make_initial_state(
            meeting_matches=[
                {
                    "document_title": "OAC Meeting 2024-03-15",
                    "content": "Architect confirmed 28-day compressive strength is 4000 psi...",
                    "score": 0.90,
                }
            ],
        )
        result = await evaluate_unnecessary_node(state)
        assert result["is_unnecessary"] is True
        assert result["unnecessary_source"] == "meeting"

    @pytest.mark.asyncio
    async def test_novel_rfi_passes_through(self):
        from app.services.agents.rfi_resolution_agent import evaluate_unnecessary_node

        state = _make_initial_state(
            similar_rfis=[],
            spec_matches=[{"score": 0.5, "content": "General info"}],
            meeting_matches=[],
        )
        result = await evaluate_unnecessary_node(state)
        assert result["is_unnecessary"] is False
        assert result["unnecessary_source"] is None

    @pytest.mark.asyncio
    async def test_similar_rfi_without_answer_not_flagged(self):
        from app.services.agents.rfi_resolution_agent import evaluate_unnecessary_node

        state = _make_initial_state(
            similar_rfis=[
                {
                    "rfi_number": "RFI-044",
                    "subject": "Concrete strength",
                    "question": "What is the 28-day strength?",
                    "answer": "",  # No answer
                    "similarity_score": 0.95,
                }
            ],
        )
        result = await evaluate_unnecessary_node(state)
        assert result["is_unnecessary"] is False


# ═══════════════════════════════════════════════════════════════════════════
# 3. Stage 3 — Hallucination check
# ═══════════════════════════════════════════════════════════════════════════


class TestHallucinationCheck:
    """Test hallucination_check_node() citation verification."""

    @pytest.mark.asyncio
    async def test_valid_citations_pass(self):
        from app.services.agents.rfi_resolution_agent import hallucination_check_node

        state = _make_initial_state(
            draft_response="Per [Project Specification Book, p. 42], the strength is 4000 psi.",
            context_chunks=[
                {"document_title": "Project Specification Book", "content": "..."},
            ],
        )
        result = await hallucination_check_node(state)
        assert len(result["hallucination_flags"]) == 0

    @pytest.mark.asyncio
    async def test_hallucinated_citation_flagged(self):
        from app.services.agents.rfi_resolution_agent import hallucination_check_node

        state = _make_initial_state(
            draft_response="Per [Nonexistent Document, p. 5], the requirement is 5000 psi.",
            context_chunks=[
                {"document_title": "Project Specification Book", "content": "..."},
            ],
        )
        result = await hallucination_check_node(state)
        assert len(result["hallucination_flags"]) == 1
        assert result["hallucination_flags"][0]["type"] == "hallucinated_source"
        assert "Nonexistent Document" in result["hallucination_flags"][0]["citation"]

    @pytest.mark.asyncio
    async def test_osha_citation_valid(self):
        from app.services.agents.rfi_resolution_agent import hallucination_check_node

        state = _make_initial_state(
            draft_response="Per [OSHA 1926.502, p. 1], fall protection is required above 6 feet.",
            context_chunks=[],
            osha_chunks=[
                {"standard_number": "1926.502", "content": "Fall protection..."},
            ],
        )
        result = await hallucination_check_node(state)
        assert len(result["hallucination_flags"]) == 0

    @pytest.mark.asyncio
    async def test_no_citations_no_flags(self):
        from app.services.agents.rfi_resolution_agent import hallucination_check_node

        state = _make_initial_state(
            draft_response="The concrete strength should be 4000 psi based on typical requirements.",
        )
        result = await hallucination_check_node(state)
        assert len(result["hallucination_flags"]) == 0

    @pytest.mark.asyncio
    async def test_multiple_hallucinated_citations(self):
        from app.services.agents.rfi_resolution_agent import hallucination_check_node

        state = _make_initial_state(
            draft_response=("Per [Fake Doc A, p. 1] and [Fake Doc B, p. 2], requirements differ."),
            context_chunks=[
                {"document_title": "Real Document", "content": "..."},
            ],
        )
        result = await hallucination_check_node(state)
        assert len(result["hallucination_flags"]) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 4. Stage 3 — Contradiction check
# ═══════════════════════════════════════════════════════════════════════════


class TestContradictionCheck:
    """Test contradiction_check_node() numeric conflict detection."""

    @pytest.mark.asyncio
    async def test_no_contradiction_when_no_similar_rfis(self):
        from app.services.agents.rfi_resolution_agent import contradiction_check_node

        state = _make_initial_state(
            draft_response="The strength is 4000 psi.",
            similar_rfis=[],
        )
        result = await contradiction_check_node(state)
        assert len(result["contradiction_flags"]) == 0

    @pytest.mark.asyncio
    async def test_detects_numeric_contradiction(self):
        from app.services.agents.rfi_resolution_agent import contradiction_check_node

        state = _make_initial_state(
            draft_response="The required strength is 4000 psi per specification.",
            similar_rfis=[
                {
                    "rfi_number": "RFI-050",
                    "answer": "The compressive strength shall be 5000 psi.",
                    "similarity_score": 0.88,
                }
            ],
        )
        result = await contradiction_check_node(state)
        assert len(result["contradiction_flags"]) >= 1
        flag = result["contradiction_flags"][0]
        assert flag["type"] == "numeric_contradiction"

    @pytest.mark.asyncio
    async def test_no_contradiction_same_values(self):
        from app.services.agents.rfi_resolution_agent import contradiction_check_node

        state = _make_initial_state(
            draft_response="The required strength is 4000 psi.",
            similar_rfis=[
                {
                    "rfi_number": "RFI-050",
                    "answer": "Confirmed: 4000 psi is the requirement.",
                    "similarity_score": 0.90,
                }
            ],
        )
        result = await contradiction_check_node(state)
        assert len(result["contradiction_flags"]) == 0

    @pytest.mark.asyncio
    async def test_empty_draft_no_contradiction(self):
        from app.services.agents.rfi_resolution_agent import contradiction_check_node

        state = _make_initial_state(
            draft_response="",
            similar_rfis=[{"rfi_number": "RFI-050", "answer": "4000 psi", "similarity_score": 0.9}],
        )
        result = await contradiction_check_node(state)
        assert len(result["contradiction_flags"]) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 5. Stage 3 — Completeness check
# ═══════════════════════════════════════════════════════════════════════════


class TestCompletenessCheck:
    """Test completeness_check_node() sub-question coverage."""

    @pytest.mark.asyncio
    async def test_single_question_adequate_response(self):
        from app.services.agents.rfi_resolution_agent import completeness_check_node

        state = _make_initial_state(
            question="What is the required compressive strength?",
            draft_response=(
                "Per Section 03 30 00, the minimum 28-day compressive strength "
                "for the slab-on-grade is 4000 psi. The concrete mix shall be "
                "designed to achieve this strength."
            ),
        )
        result = await completeness_check_node(state)
        assert len(result["completeness_flags"]) == 0

    @pytest.mark.asyncio
    async def test_single_question_too_brief(self):
        from app.services.agents.rfi_resolution_agent import completeness_check_node

        state = _make_initial_state(
            question="What is the required compressive strength?",
            draft_response="4000 psi.",
        )
        result = await completeness_check_node(state)
        assert len(result["completeness_flags"]) == 1
        assert result["completeness_flags"][0]["type"] == "insufficient_response"

    @pytest.mark.asyncio
    async def test_multi_part_all_addressed(self):
        from app.services.agents.rfi_resolution_agent import completeness_check_node

        state = _make_initial_state(
            question=(
                "1. What is the required compressive strength for footings?\n"
                "2. What is the slump requirement for pumped concrete?\n"
                "3. What admixtures are acceptable for cold weather?"
            ),
            draft_response=(
                "1. The compressive strength for footings shall be 4000 psi. "
                "2. For pumped concrete, the slump requirement is 5-7 inches. "
                "3. For cold weather, acceptable admixtures include air-entraining "
                "agents and calcium chloride accelerator."
            ),
        )
        result = await completeness_check_node(state)
        # All sub-questions addressed
        assert len(result["completeness_flags"]) == 0

    @pytest.mark.asyncio
    async def test_multi_part_missing_answer(self):
        from app.services.agents.rfi_resolution_agent import completeness_check_node

        state = _make_initial_state(
            question=(
                "1. What is the required compressive strength for footings?\n"
                "2. What waterproofing membrane is specified for below-grade walls?\n"
                "3. What admixtures are acceptable for cold weather?"
            ),
            draft_response=(
                "The compressive strength for footings shall be 4000 psi. "
                "For cold weather, acceptable admixtures include air-entraining agents."
            ),
        )
        result = await completeness_check_node(state)
        # Sub-question 2 about waterproofing should be flagged
        unanswered = [
            f for f in result["completeness_flags"] if f["type"] == "unanswered_sub_question"
        ]
        assert len(unanswered) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 6. Stage 3 — Finalize verification
# ═══════════════════════════════════════════════════════════════════════════


class TestFinalizeVerification:
    """Test finalize_verification_node() label generation."""

    @pytest.mark.asyncio
    async def test_passes_with_no_flags(self):
        from app.services.agents.rfi_resolution_agent import finalize_verification_node

        state = _make_initial_state(
            draft_response="The strength is 4000 psi per spec.",
            draft_confidence=0.90,
            hallucination_flags=[],
            contradiction_flags=[],
            completeness_flags=[],
        )
        result = await finalize_verification_node(state)
        assert result["verification_passed"] is True
        assert "AI-ASSISTED DRAFT" in result["final_response"]
        assert "human review" in result["final_response"].lower()

    @pytest.mark.asyncio
    async def test_fails_with_too_many_warnings(self):
        from app.services.agents.rfi_resolution_agent import finalize_verification_node

        state = _make_initial_state(
            draft_response="Draft with issues",
            draft_confidence=0.85,
            hallucination_flags=[
                {"severity": "warning", "message": "Flag 1"},
                {"severity": "warning", "message": "Flag 2"},
                {"severity": "warning", "message": "Flag 3"},
            ],
            contradiction_flags=[],
            completeness_flags=[],
        )
        result = await finalize_verification_node(state)
        assert result["verification_passed"] is False
        assert result["draft_confidence"] < 0.85  # Confidence reduced

    @pytest.mark.asyncio
    async def test_includes_verification_notes(self):
        from app.services.agents.rfi_resolution_agent import finalize_verification_node

        state = _make_initial_state(
            draft_response="Some draft",
            draft_confidence=0.80,
            hallucination_flags=[
                {"severity": "warning", "message": "Cited source not found"},
            ],
            contradiction_flags=[],
            completeness_flags=[],
        )
        result = await finalize_verification_node(state)
        assert "VERIFICATION NOTES" in result["final_response"]
        assert "Cited source not found" in result["final_response"]

    @pytest.mark.asyncio
    async def test_no_draft_returns_none(self):
        from app.services.agents.rfi_resolution_agent import finalize_verification_node

        state = _make_initial_state(
            draft_response=None,
            draft_confidence=0.0,
        )
        result = await finalize_verification_node(state)
        assert result["final_response"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 7. Graph construction and conditional routing
# ═══════════════════════════════════════════════════════════════════════════


class TestGraphConstruction:
    """Test build_rfi_resolution_agent() and conditional routing."""

    def test_builds_compiled_graph(self):
        from app.services.agents.rfi_resolution_agent import build_rfi_resolution_agent

        graph = build_rfi_resolution_agent()
        assert graph is not None

    def test_conditional_routing_unnecessary(self):
        from app.services.agents.rfi_resolution_agent import _should_continue_to_stage2

        state = _make_initial_state(is_unnecessary=True)
        assert _should_continue_to_stage2(state) == "end_unnecessary"

    def test_conditional_routing_novel(self):
        from app.services.agents.rfi_resolution_agent import _should_continue_to_stage2

        state = _make_initial_state(is_unnecessary=False)
        assert _should_continue_to_stage2(state) == "retrieve_context"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Draft generation (mocked LLM)
# ═══════════════════════════════════════════════════════════════════════════


class TestDraftGeneration:
    """Test generate_draft_node() with mocked LLM."""

    @pytest.mark.asyncio
    async def test_generates_draft_with_context(self):
        from app.services.agents.rfi_resolution_agent import generate_draft_node

        mock_llm_response = MagicMock()
        mock_llm_response.content = (
            '{"answer": "Per Section 03 30 00, strength is 4000 psi.", '
            '"confidence": 0.88, "sources": [{"document_title": "Spec", '
            '"page_number": 42, "section": "03 30 00"}], '
            '"requires_expert": false, "expert_reason": null}'
        )

        state = _make_initial_state(
            context_chunks=[
                {
                    "document_title": "Project Specification Book",
                    "content": "Min 28-day compressive strength: 4000 psi",
                    "page_number": 42,
                    "section_hierarchy": ["Part 2", "2.1 Materials"],
                    "csi_section": "03 30 00",
                }
            ],
        )

        # Force LLMGateway to fail so we hit the LangChain fallback path
        with (
            patch(
                "app.services.reliability.llm_gateway.LLMGateway",
                side_effect=Exception("force fallback"),
            ),
            patch("langchain_openai.ChatOpenAI") as MockLLM,
        ):
            mock_instance = AsyncMock()
            mock_instance.ainvoke = AsyncMock(return_value=mock_llm_response)
            MockLLM.return_value = mock_instance

            result = await generate_draft_node(state)

        assert result["draft_response"] is not None
        assert result["draft_confidence"] == 0.88
        assert result["stage_reached"] == 2

    @pytest.mark.asyncio
    async def test_no_context_returns_insufficient(self):
        from app.services.agents.rfi_resolution_agent import generate_draft_node

        state = _make_initial_state(context_chunks=[], osha_chunks=[])
        result = await generate_draft_node(state)
        assert "Insufficient" in result["draft_response"]
        assert result["draft_confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_osha_chunks_included_for_safety(self):
        from app.services.agents.rfi_resolution_agent import generate_draft_node

        mock_llm_response = MagicMock()
        mock_llm_response.content = (
            '{"answer": "Per OSHA 1926.502, guardrails required.", '
            '"confidence": 0.90, "sources": []}'
        )

        state = _make_initial_state(
            question="What fall protection is required?",
            context_chunks=[
                {
                    "document_title": "Safety Plan",
                    "content": "Fall protection per OSHA.",
                    "page_number": 5,
                    "section_hierarchy": None,
                    "csi_section": None,
                }
            ],
            osha_chunks=[
                {
                    "standard_number": "1926.502",
                    "content": "Guardrail systems required above 6 feet...",
                    "topic": "Fall Protection",
                }
            ],
        )

        with patch("langchain_openai.ChatOpenAI") as MockLLM:
            mock_instance = AsyncMock()
            mock_instance.ainvoke = AsyncMock(return_value=mock_llm_response)
            MockLLM.return_value = mock_instance

            with patch.dict("sys.modules", {"app.services.reliability.llm_gateway": None}):
                with patch(
                    "app.services.reliability.llm_gateway.LLMGateway",
                    side_effect=ImportError("mocked"),
                    create=True,
                ):
                    result = await generate_draft_node(state)

        assert result["draft_response"] is not None
        assert result["draft_confidence"] == 0.90


# ═══════════════════════════════════════════════════════════════════════════
# 9. Full pipeline (mocked DB + LLM)
# ═══════════════════════════════════════════════════════════════════════════


class TestFullPipeline:
    """Test run_rfi_resolution() end-to-end with mocks."""

    @pytest.mark.asyncio
    async def test_spec_answered_rfi_flagged_unnecessary(self):
        """RFI whose answer is already in the specs → unnecessary."""
        from app.services.agents.rfi_resolution_agent import (
            evaluate_unnecessary_node,
        )

        # Simulate Stage 1 manually (avoid DB dependency)
        state = _make_initial_state(
            spec_matches=[
                {
                    "document_title": "Spec Book",
                    "csi_section": "03 30 00",
                    "content": "Minimum compressive strength: 4000 psi at 28 days.",
                    "score": 0.96,
                }
            ],
        )
        result = await evaluate_unnecessary_node(state)
        assert result["is_unnecessary"] is True
        assert result["stage_reached"] == 1

    @pytest.mark.asyncio
    async def test_novel_rfi_goes_through_all_stages(self):
        """Novel RFI → not unnecessary → draft → verification."""
        from app.services.agents.rfi_resolution_agent import (
            completeness_check_node,
            contradiction_check_node,
            evaluate_unnecessary_node,
            finalize_verification_node,
            generate_draft_node,
            hallucination_check_node,
        )

        # Stage 1: Not unnecessary
        state = _make_initial_state()
        s1_result = await evaluate_unnecessary_node(state)
        assert not s1_result["is_unnecessary"]

        # Stage 2: Generate draft
        mock_llm_response = MagicMock()
        mock_llm_response.content = (
            '{"answer": "Per [Spec Book, p. 42], strength is 4000 psi.", '
            '"confidence": 0.88, "sources": [{"document_title": "Spec Book", '
            '"page_number": 42, "section": "03 30 00"}]}'
        )

        state.update(s1_result)
        state["context_chunks"] = [
            {
                "document_title": "Spec Book",
                "content": "4000 psi minimum",
                "page_number": 42,
                "section_hierarchy": None,
                "csi_section": "03 30 00",
            }
        ]

        with patch("langchain_openai.ChatOpenAI") as MockLLM:
            mock_instance = AsyncMock()
            mock_instance.ainvoke = AsyncMock(return_value=mock_llm_response)
            MockLLM.return_value = mock_instance
            with patch.dict("sys.modules", {"app.services.reliability.llm_gateway": None}):
                with patch(
                    "app.services.reliability.llm_gateway.LLMGateway",
                    side_effect=ImportError("mocked"),
                    create=True,
                ):
                    s2_result = await generate_draft_node(state)

        state.update(s2_result)
        assert state["draft_confidence"] == 0.88

        # Stage 3: Verification
        h_result = await hallucination_check_node(state)
        state.update(h_result)

        c_result = await contradiction_check_node(state)
        state.update(c_result)

        comp_result = await completeness_check_node(state)
        state.update(comp_result)

        final = await finalize_verification_node(state)
        state.update(final)

        assert state["verification_passed"] is True
        assert state["stage_reached"] == 3
        assert "AI-ASSISTED DRAFT" in state["final_response"]


# ═══════════════════════════════════════════════════════════════════════════
# 10. Resolution log model
# ═══════════════════════════════════════════════════════════════════════════


class TestResolutionLogModel:
    """Test RfiResolutionLog model instantiation."""

    def test_model_creation(self):
        from app.models.communication import RfiResolutionLog

        log = RfiResolutionLog(
            rfi_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            stage_reached=3,
            was_unnecessary=False,
            draft_confidence=0.85,
            verification_passed=True,
        )
        assert log.stage_reached == 3
        assert log.draft_confidence == 0.85
        assert log.was_unnecessary is False

    def test_model_with_human_feedback(self):
        from app.models.communication import RfiResolutionLog

        log = RfiResolutionLog(
            rfi_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            stage_reached=3,
            was_unnecessary=False,
            human_accepted_draft=True,
            human_edit_distance=42,
            human_feedback="Minor edits to citations only",
            time_to_resolution_hours=2.5,
            traditional_avg_hours=48.0,
        )
        assert log.human_accepted_draft is True
        assert log.human_edit_distance == 42
        assert log.time_to_resolution_hours == 2.5


# ═══════════════════════════════════════════════════════════════════════════
# 11. Webhook integration
# ═══════════════════════════════════════════════════════════════════════════


class TestWebhookIntegration:
    """Test Procore webhook → RFI Resolution Agent connection."""

    def test_downstream_handler_registered(self):
        from app.services.integrations.procore_webhook_processor import (
            _DOWNSTREAM_HANDLERS,
        )

        assert "constructai.procore.rfi.resolution_requested" in _DOWNSTREAM_HANDLERS

    @pytest.mark.asyncio
    async def test_handle_downstream_event_routes_correctly(self):
        from app.services.integrations.procore_webhook_processor import (
            _DOWNSTREAM_HANDLERS,
            handle_downstream_event,
        )

        mock_handler = AsyncMock()
        original = _DOWNSTREAM_HANDLERS["constructai.procore.rfi.resolution_requested"]
        _DOWNSTREAM_HANDLERS["constructai.procore.rfi.resolution_requested"] = mock_handler
        try:
            await handle_downstream_event(
                "constructai.procore.rfi.resolution_requested",
                {"project_id": FAKE_PROJECT_ID, "resource_id": 12345},
            )
            mock_handler.assert_called_once_with(
                {"project_id": FAKE_PROJECT_ID, "resource_id": 12345}
            )
        finally:
            _DOWNSTREAM_HANDLERS["constructai.procore.rfi.resolution_requested"] = original

    @pytest.mark.asyncio
    async def test_handle_downstream_unknown_event(self):
        from app.services.integrations.procore_webhook_processor import (
            handle_downstream_event,
        )

        # Should not raise
        await handle_downstream_event("unknown.event.type", {})


# ═══════════════════════════════════════════════════════════════════════════
# 12. Citation regex
# ═══════════════════════════════════════════════════════════════════════════


class TestCitationRegex:
    """Test the _CITATION_RE pattern for source extraction."""

    def test_extracts_title_and_page(self):
        from app.services.agents.rfi_resolution_agent import _CITATION_RE

        matches = _CITATION_RE.findall("[Project Spec Book, p. 42]")
        assert len(matches) == 1
        assert matches[0][0] == "Project Spec Book"
        assert matches[0][1] == "42"

    def test_extracts_title_without_page(self):
        from app.services.agents.rfi_resolution_agent import _CITATION_RE

        matches = _CITATION_RE.findall("[OSHA 1926.502]")
        assert len(matches) == 1
        assert matches[0][0] == "OSHA 1926.502"

    def test_extracts_multiple_citations(self):
        from app.services.agents.rfi_resolution_agent import _CITATION_RE

        text = "Per [Spec A, p. 10] and [Spec B, p. 20], the requirement is..."
        matches = _CITATION_RE.findall(text)
        assert len(matches) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 13. Entry point functions
# ═══════════════════════════════════════════════════════════════════════════


class TestEntryPoints:
    """Test run_rfi_resolution() and run_rfi_unnecessary_check() error handling."""

    @pytest.mark.asyncio
    async def test_run_rfi_resolution_handles_graph_failure(self):
        from app.services.agents.rfi_resolution_agent import run_rfi_resolution

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("Graph exploded"))

        with (
            patch(
                "app.services.agents.checkpointer.get_checkpointer",
                return_value=None,
            ),
            patch(
                "app.services.agents.rfi_resolution_agent.build_rfi_resolution_agent",
                return_value=mock_graph,
            ),
        ):
            result = await run_rfi_resolution(
                rfi_id=FAKE_RFI_ID,
                project_id=FAKE_PROJECT_ID,
                subject="Test",
                question="Test question?",
            )

        assert result["status"] == "failed"
        assert "error" in result  # Error message is sanitized (no internal details leaked)

    @pytest.mark.asyncio
    async def test_run_rfi_unnecessary_check_handles_failure(self):
        from app.services.agents.rfi_resolution_agent import run_rfi_unnecessary_check

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("DB down"))

        with (
            patch(
                "app.services.agents.checkpointer.get_checkpointer",
                return_value=None,
            ),
            patch(
                "app.services.agents.rfi_resolution_agent.build_rfi_resolution_agent",
                return_value=mock_graph,
            ),
        ):
            result = await run_rfi_unnecessary_check(
                rfi_id=FAKE_RFI_ID,
                project_id=FAKE_PROJECT_ID,
                subject="Test",
                question="Test question?",
            )

        assert result["status"] == "error"
        assert "error" in result  # Error message is sanitized (no internal details leaked)
