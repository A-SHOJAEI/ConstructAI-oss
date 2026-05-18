"""Phase 2: Quantity extraction tests.

Tests for BIM/IFC quantity extraction and LLM-based document extraction.
All LLM calls are mocked so that no real API requests are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.services.estimating.quantity_extractor import (
    extract_quantities_from_document,
    extract_quantities_from_ifc,
)
from tests.fixtures.precon_mock_responses import (
    MOCK_IFC_DATA,
    MOCK_LLM_QUANTITY_RESPONSE,
)


class TestQuantityExtractor:
    """Tests for the quantity extraction service."""

    async def test_extract_from_ifc_returns_quantities(self):
        """Extract quantities from IFC data."""
        result = await extract_quantities_from_ifc(MOCK_IFC_DATA)
        assert len(result) > 0
        assert all("csi_code" in item for item in result)
        assert all("quantity" in item for item in result)

    async def test_extract_from_ifc_maps_wall_to_concrete(self):
        """IfcWall elements should map to CSI 03 30 00."""
        result = await extract_quantities_from_ifc(MOCK_IFC_DATA)
        wall_items = [r for r in result if r.get("element_type") == "IfcWall"]
        assert len(wall_items) > 0
        assert wall_items[0]["csi_code"] == "03 30 00"

    async def test_extract_from_ifc_maps_beam_to_steel(self):
        """IfcBeam elements should map to CSI 05 12 00."""
        result = await extract_quantities_from_ifc(MOCK_IFC_DATA)
        beam_items = [r for r in result if r.get("element_type") == "IfcBeam"]
        assert len(beam_items) > 0
        assert beam_items[0]["csi_code"] == "05 12 00"

    async def test_extract_from_ifc_picks_best_quantity_unit(self):
        """Extractor should prefer volume over area over count."""
        result = await extract_quantities_from_ifc(MOCK_IFC_DATA)
        # Wall has both volume and area; volume (CY) should be preferred
        wall_items = [r for r in result if r.get("element_type") == "IfcWall"]
        assert len(wall_items) > 0
        assert wall_items[0]["unit"] == "CY"
        assert wall_items[0]["quantity"] == 25.5

    async def test_extract_from_document(self):
        """LLM extraction from document text."""
        # Patching `ChatOpenAI` directly is insufficient: the extractor
        # routes through the LLM gateway first (only falling back to
        # ChatOpenAI on ImportError). Patching the gateway factory captures
        # the real call path.
        gateway = AsyncMock()
        gateway.complete = AsyncMock(return_value={"content": MOCK_LLM_QUANTITY_RESPONSE})
        with patch(
            "app.services.reliability.llm_gateway.get_llm_gateway",
            new_callable=AsyncMock,
            return_value=gateway,
        ):
            result = await extract_quantities_from_document("concrete spec text", "spec.pdf")
        assert len(result) > 0
        assert result[0]["description"] == "Ready-mix concrete 4000 PSI"
        assert result[0]["quantity"] == 850
        assert result[0]["unit"] == "CY"

    async def test_extract_from_empty_ifc(self):
        """Empty IFC data should return empty list."""
        result = await extract_quantities_from_ifc({"elements": []})
        assert result == []

    async def test_extract_from_ifc_element_count(self):
        """Should extract one result per mapped IFC element."""
        result = await extract_quantities_from_ifc(MOCK_IFC_DATA)
        # All 6 elements in MOCK_IFC_DATA have types in IFC_TO_CSI mapping
        assert len(result) == 6
