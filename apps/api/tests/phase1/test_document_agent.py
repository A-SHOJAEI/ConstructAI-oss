"""Phase 1: Document agent pipeline tests.

Tests for the high-level document agent that orchestrates classification,
entity extraction, and risk identification. All LLM calls are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from tests.fixtures.mock_responses import (
    MOCK_LLM_CLASSIFICATION_RESPONSE,
    MOCK_LLM_ENTITY_RESPONSE,
    MOCK_PDF_TEXT,
)


class TestDocumentAgent:
    """Tests for the document agent pipeline."""

    @patch("app.services.agents.entity_extractor.ChatOpenAI")
    @patch("app.services.agents.classifier.ChatOpenAI")
    async def test_document_agent_full_pipeline(self, mock_classifier_chat, mock_extractor_chat):
        """The document agent should produce classification, entities, and
        risk-related entities when running the full pipeline."""
        from app.services.agents.classifier import classify_document
        from app.services.agents.entity_extractor import extract_entities

        # Mock the classifier LLM.
        mock_clf_instance = AsyncMock()
        mock_clf_response = MagicMock()
        mock_clf_response.content = MOCK_LLM_CLASSIFICATION_RESPONSE
        mock_clf_instance.ainvoke = AsyncMock(return_value=mock_clf_response)
        mock_classifier_chat.return_value = mock_clf_instance

        # Mock the entity extractor LLM.
        mock_ext_instance = AsyncMock()
        mock_ext_response = MagicMock()
        mock_ext_response.content = MOCK_LLM_ENTITY_RESPONSE
        mock_ext_instance.ainvoke = AsyncMock(return_value=mock_ext_response)
        mock_extractor_chat.return_value = mock_ext_instance

        # Run classification.
        classification = await classify_document(
            text_sample=MOCK_PDF_TEXT,
            filename="concrete_spec.pdf",
        )

        # Run entity extraction.
        entities = await extract_entities(MOCK_PDF_TEXT)

        # Verify classification output.
        assert classification["classified_type"] == "specification"
        assert 0.0 <= classification["confidence"] <= 1.0
        assert classification["model_used"] is not None

        # Verify entity extraction output.
        assert isinstance(entities, list)
        assert len(entities) >= 1

        # Verify pipeline produces risk-relevant information.
        # Entities that might indicate risk: test_required, requirement, risk_clause
        entity_types = {e["entity_type"] for e in entities}
        risk_related_types = entity_types & {"test_required", "requirement", "risk_clause"}
        assert len(risk_related_types) >= 1, (
            f"Pipeline should produce at least one risk-related entity type. "
            f"Found types: {entity_types}"
        )

        # Verify all entities have non-empty values.
        for entity in entities:
            assert entity["entity_value"], "Entity value should not be empty"
            assert entity["entity_type"], "Entity type should not be empty"

    @patch("app.services.agents.classifier.ChatOpenAI")
    async def test_document_agent_handles_error(self, mock_chat_class):
        """When the LLM fails, the classifier should return a graceful fallback
        result rather than raising an unhandled exception."""
        from app.services.agents.classifier import classify_document

        # Configure mock LLM to raise an exception.
        mock_llm_instance = AsyncMock()
        mock_llm_instance.ainvoke = AsyncMock(side_effect=Exception("LLM service unavailable"))
        mock_chat_class.return_value = mock_llm_instance

        # The classifier should handle the error gracefully.
        result = await classify_document(
            text_sample=MOCK_PDF_TEXT,
            filename="test.pdf",
        )

        # Should return a fallback classification, not raise.
        assert "classified_type" in result
        assert result["classified_type"] == "other"
        assert result["confidence"] == 0.0
        assert result["model_used"] is not None

    @patch("app.services.agents.entity_extractor.ChatOpenAI")
    async def test_entity_extractor_handles_error(self, mock_chat_class):
        """When the LLM fails, the entity extractor should return an empty list
        rather than raising an unhandled exception."""
        from app.services.agents.entity_extractor import extract_entities

        # Configure mock LLM to raise an exception.
        mock_llm_instance = AsyncMock()
        mock_llm_instance.ainvoke = AsyncMock(side_effect=Exception("LLM service unavailable"))
        mock_chat_class.return_value = mock_llm_instance

        result = await extract_entities(MOCK_PDF_TEXT)

        # Should return empty list, not raise.
        assert isinstance(result, list)
        assert len(result) == 0
