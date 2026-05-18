"""Phase 1: Document classifier tests.

Tests for the LLM-based document classification service. All LLM calls
are mocked so that no real API requests are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from tests.fixtures.mock_responses import MOCK_LLM_CLASSIFICATION_RESPONSE, MOCK_PDF_TEXT

VALID_DOCUMENT_TYPES = {
    "specification",
    "drawing",
    "contract",
    "rfi",
    "submittal",
    "daily_log",
    "meeting_minutes",
    "change_order",
    "schedule",
    "bim_model",
    "other",
}


class TestClassifier:
    """Tests for the document classification service."""

    @patch("app.services.agents.classifier.ChatOpenAI")
    async def test_classify_specification(self, mock_chat_class):
        """Classifier should return the correct type for a specification document."""
        from app.services.agents.classifier import classify_document

        # Configure mock LLM to return a valid classification response.
        mock_llm_instance = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_LLM_CLASSIFICATION_RESPONSE
        mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)
        mock_chat_class.return_value = mock_llm_instance

        result = await classify_document(
            text_sample=MOCK_PDF_TEXT,
            filename="concrete_spec_03_30_00.pdf",
        )

        assert "classified_type" in result
        assert result["classified_type"] == "specification"
        assert result["classified_type"] in VALID_DOCUMENT_TYPES
        assert "model_used" in result
        assert result["model_used"] == "gpt-4o-mini"

        # Verify CSI division was detected.
        assert result.get("csi_division") is not None
        assert "03" in result["csi_division"] or "Concrete" in result["csi_division"]

        # Verify discipline was detected.
        assert result.get("discipline") == "structural"

    @patch("app.services.agents.classifier.ChatOpenAI")
    async def test_classify_with_confidence(self, mock_chat_class):
        """Classification confidence score should be a float between 0 and 1."""
        from app.services.agents.classifier import classify_document

        mock_llm_instance = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_LLM_CLASSIFICATION_RESPONSE
        mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)
        mock_chat_class.return_value = mock_llm_instance

        result = await classify_document(
            text_sample=MOCK_PDF_TEXT,
            filename="test_spec.pdf",
        )

        assert "confidence" in result
        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0, (
            f"Confidence {result['confidence']} should be between 0 and 1"
        )
