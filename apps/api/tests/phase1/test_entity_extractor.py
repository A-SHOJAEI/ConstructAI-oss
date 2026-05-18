"""Phase 1: Entity extraction tests.

Tests for the LLM-based entity extraction service. All LLM calls are mocked
so that no real API requests are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from tests.fixtures.mock_responses import MOCK_LLM_ENTITY_RESPONSE, MOCK_PDF_TEXT

VALID_ENTITY_TYPES = {
    "product",
    "manufacturer",
    "standard",
    "requirement",
    "submittal_required",
    "test_required",
    "risk_clause",
}


class TestEntityExtractor:
    """Tests for the entity extraction service."""

    @patch("app.services.agents.entity_extractor.ChatOpenAI")
    async def test_extract_entities_from_spec(self, mock_chat_class):
        """Entity extractor should return entities with expected fields from spec text."""
        from app.services.agents.entity_extractor import extract_entities

        # Configure mock LLM to return a valid entity extraction response.
        mock_llm_instance = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_LLM_ENTITY_RESPONSE
        mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)
        mock_chat_class.return_value = mock_llm_instance

        entities = await extract_entities(MOCK_PDF_TEXT)

        assert isinstance(entities, list)
        assert len(entities) >= 1, "Should extract at least one entity"

        # Verify each entity has the required keys.
        for entity in entities:
            assert "entity_type" in entity
            assert "entity_value" in entity
            assert "confidence" in entity
            assert len(entity["entity_value"]) > 0, "entity_value should not be empty"

        # Verify we got expected entity types from the mock response.
        extracted_types = {e["entity_type"] for e in entities}
        assert "product" in extracted_types, "Should extract at least one product entity"
        assert "standard" in extracted_types, "Should extract at least one standard entity"

        # Verify specific entities were extracted.
        entity_values = [e["entity_value"] for e in entities]
        assert any("ASTM C150" in v for v in entity_values), (
            "Should extract ASTM C150 standard reference"
        )

    @patch("app.services.agents.entity_extractor.ChatOpenAI")
    async def test_entity_types_are_valid(self, mock_chat_class):
        """All returned entity types should be from the allowed set."""
        from app.services.agents.entity_extractor import extract_entities

        mock_llm_instance = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_LLM_ENTITY_RESPONSE
        mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)
        mock_chat_class.return_value = mock_llm_instance

        entities = await extract_entities(MOCK_PDF_TEXT)

        assert len(entities) >= 1, "Should extract at least one entity"

        for entity in entities:
            assert entity["entity_type"] in VALID_ENTITY_TYPES, (
                f"Entity type '{entity['entity_type']}' is not in the allowed set: "
                f"{VALID_ENTITY_TYPES}"
            )
            # Confidence should be a float between 0 and 1.
            assert isinstance(entity["confidence"], float)
            assert 0.0 <= entity["confidence"] <= 1.0, (
                f"Entity confidence {entity['confidence']} should be between 0 and 1"
            )
