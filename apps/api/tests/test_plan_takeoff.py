"""Tests for AI Plan Takeoff (Feature 4.2).

40+ tests covering CSI mapping, LLM extraction, cost enrichment,
full pipeline, convert-to-estimate, confidence scoring, list/get,
and input validation.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Unit under test imports
# ---------------------------------------------------------------------------
from app.services.estimating.plan_takeoff_service import (
    ELEMENT_TO_CSI_MAP,
    REGIONAL_COST_FACTORS,
    _classify_element_type,
    _map_element_to_csi,
    _parse_llm_json,
    _round2,
    _validate_element,
    compute_takeoff_confidence,
)

# ===========================================================================
# TestCSIMapping — 8 tests
# ===========================================================================


class TestCSIMapping:
    """Tests for element-to-CSI MasterFormat mapping."""

    def test_exact_match_concrete_slab(self):
        elem = {"element_type": "concrete_slab", "description": "4-inch slab on grade"}
        assert _map_element_to_csi(elem) == "03 30 00"

    def test_exact_match_interior_door(self):
        elem = {"element_type": "interior_door", "description": "3-0 x 6-8 hollow core"}
        assert _map_element_to_csi(elem) == "08 11 13"

    def test_exact_match_steel_beam(self):
        elem = {"element_type": "steel_beam", "description": "W10x22 beam"}
        assert _map_element_to_csi(elem) == "05 12 00"

    def test_exact_match_drywall(self):
        elem = {"element_type": "drywall", "description": '5/8" Type X drywall'}
        assert _map_element_to_csi(elem) == "09 29 00"

    def test_fuzzy_match_via_description(self):
        """Element type is unknown but description contains concrete keywords."""
        elem = {
            "element_type": "foundation_element",
            "description": "concrete footing 24x12",
            "material": "concrete",
        }
        result = _map_element_to_csi(elem)
        # Should match to a concrete division (03 xx xx)
        assert result is not None
        assert result.startswith("03")

    def test_fuzzy_match_via_material(self):
        """Element type is generic but material gives the hint."""
        elem = {
            "element_type": "wall_assembly",
            "description": "exterior wall",
            "material": "brick veneer",
        }
        result = _map_element_to_csi(elem)
        assert result is not None
        assert result.startswith("04")

    def test_no_match_returns_none(self):
        elem = {
            "element_type": "xyzzy_unknown",
            "description": "something completely unrelated",
        }
        assert _map_element_to_csi(elem) is None

    def test_all_map_entries_have_valid_format(self):
        """Every CSI code in the map should be a valid XX XX XX format."""
        for element_type, csi_code in ELEMENT_TO_CSI_MAP.items():
            parts = csi_code.split(" ")
            assert len(parts) == 3, f"Invalid CSI format for {element_type}: {csi_code}"
            for part in parts:
                assert len(part) == 2, f"Invalid CSI part length for {element_type}: {csi_code}"


# ===========================================================================
# TestLLMExtraction — 6 tests
# ===========================================================================


class TestLLMExtraction:
    """Tests for LLM JSON parsing and element extraction."""

    def test_parse_clean_json_array(self):
        content = json.dumps(
            [
                {
                    "element_type": "window",
                    "description": "36x48 double hung",
                    "quantity": 8,
                    "unit": "EA",
                },
                {
                    "element_type": "door",
                    "description": "3-0 exterior",
                    "quantity": 2,
                    "unit": "EA",
                },
            ]
        )
        result = _parse_llm_json(content)
        assert len(result) == 2
        assert result[0]["element_type"] == "window"
        assert result[1]["quantity"] == 2

    def test_parse_json_with_markdown_fences(self):
        content = '```json\n[{"element_type": "drywall", "description": "interior", "quantity": 500, "unit": "SF"}]\n```'
        result = _parse_llm_json(content)
        assert len(result) == 1
        assert result[0]["element_type"] == "drywall"

    def test_parse_json_with_surrounding_text(self):
        content = 'Here are the elements:\n[{"element_type": "slab", "description": "concrete", "quantity": 1, "unit": "CY"}]\nTotal: 1 item'
        result = _parse_llm_json(content)
        assert len(result) == 1

    def test_parse_invalid_json_returns_empty(self):
        content = "This is not JSON at all"
        result = _parse_llm_json(content)
        assert result == []

    def test_parse_empty_content(self):
        assert _parse_llm_json("") == []
        assert _parse_llm_json(None) == []

    def test_validate_element_normalizes_fields(self):
        raw = {
            "element_type": "  Window  ",
            "description": "double hung",
            "quantity": -5,  # negative should be clamped to 0
            "unit": "ea",
            "dimensions": {"width": 3, "height": 4},
            "material": "vinyl",
        }
        result = _validate_element(raw)
        assert result["element_type"] == "Window"
        assert result["quantity"] == 0  # clamped
        assert result["unit"] == "EA"  # uppercased
        assert result["dimensions"] == {"width": 3, "height": 4}
        assert result["material"] == "vinyl"


# ===========================================================================
# TestCostEnrichment — 6 tests
# ===========================================================================


class TestCostEnrichment:
    """Tests for cost database enrichment of takeoff items."""

    @pytest.mark.asyncio
    async def test_enrich_with_cost_data(self):
        """Items with CSI codes should get cost data populated."""
        mock_enriched = [
            {
                "csi_code": "09 29 00",
                "description": "drywall",
                "quantity": 500,
                "unit": "SF",
                "unit_cost": 12.50,
                "material_cost": 8.00,
                "labor_cost": 4.50,
            },
        ]

        items = [
            {
                "csi_code": "09 29 00",
                "description": "drywall",
                "quantity": 500,
                "unit": "SF",
                "source": "llm_extracted",
            },
        ]

        with patch(
            "app.services.estimating.cost_database.match_costs",
            new_callable=AsyncMock,
            return_value=mock_enriched,
        ):
            from app.services.estimating.plan_takeoff_service import _enrich_with_costs

            result = await _enrich_with_costs(None, items, None)

        assert len(result) == 1
        assert result[0]["unit_cost"] == Decimal("12.50")
        assert result[0]["total_cost"] == Decimal("6250.00")
        assert result[0]["source"] == "cost_db"

    @pytest.mark.asyncio
    async def test_enrich_applies_regional_factor(self):
        """Regional cost factor should be applied via match_costs region param."""
        mock_enriched = [
            {
                "unit_cost": 115.00,  # already adjusted by match_costs with region
                "material_cost": 69.00,
                "labor_cost": 46.00,
            },
        ]

        items = [
            {
                "csi_code": "03 30 00",
                "description": "concrete slab",
                "quantity": 10,
                "unit": "CY",
                "source": "llm_extracted",
            },
        ]

        with patch(
            "app.services.estimating.cost_database.match_costs",
            new_callable=AsyncMock,
            return_value=mock_enriched,
        ) as mock_match:
            from app.services.estimating.plan_takeoff_service import _enrich_with_costs

            result = await _enrich_with_costs(None, items, {"state": "NY"})

        # NY is northeast - verify the region was passed
        call_kwargs = mock_match.call_args
        assert (
            call_kwargs.kwargs.get("region") == "northeast"
            or call_kwargs[1].get("region") == "northeast"
        )
        assert result[0]["unit_cost"] == Decimal("115.00")
        assert result[0]["total_cost"] == Decimal("1150.00")

    @pytest.mark.asyncio
    async def test_enrich_skips_items_without_cost_result(self):
        """Items whose match_costs result has zero cost should not be enriched."""
        mock_enriched = [
            {"unit_cost": 0, "material_cost": 0, "labor_cost": 0},
        ]

        items = [
            {
                "csi_code": None,
                "description": "misc item",
                "quantity": 1,
                "unit": "EA",
                "source": "llm_extracted",
            },
        ]

        with patch(
            "app.services.estimating.cost_database.match_costs",
            new_callable=AsyncMock,
            return_value=mock_enriched,
        ):
            from app.services.estimating.plan_takeoff_service import _enrich_with_costs

            result = await _enrich_with_costs(None, items, None)

        assert result[0].get("source") == "llm_extracted"

    @pytest.mark.asyncio
    async def test_enrich_handles_cost_lookup_failure(self):
        """Cost lookup exceptions should be caught gracefully."""
        items = [
            {
                "csi_code": "09 29 00",
                "description": "drywall",
                "quantity": 100,
                "unit": "SF",
                "source": "llm_extracted",
            },
        ]

        with patch(
            "app.services.estimating.cost_database.match_costs",
            new_callable=AsyncMock,
            side_effect=Exception("API unavailable"),
        ):
            from app.services.estimating.plan_takeoff_service import _enrich_with_costs

            result = await _enrich_with_costs(None, items, None)

        # Should not crash, item should remain unenriched
        assert result[0].get("source") == "llm_extracted"

    @pytest.mark.asyncio
    async def test_enrich_handles_zero_cost(self):
        """Items with zero cost from DB should not be enriched."""
        mock_enriched = [
            {"unit_cost": 0, "material_cost": 0, "labor_cost": 0},
        ]

        items = [
            {
                "csi_code": "09 29 00",
                "description": "drywall",
                "quantity": 100,
                "unit": "SF",
                "source": "llm_extracted",
            },
        ]

        with patch(
            "app.services.estimating.cost_database.match_costs",
            new_callable=AsyncMock,
            return_value=mock_enriched,
        ):
            from app.services.estimating.plan_takeoff_service import _enrich_with_costs

            result = await _enrich_with_costs(None, items, None)

        assert result[0].get("source") == "llm_extracted"

    @pytest.mark.asyncio
    async def test_enrich_uses_material_plus_labor_fallback(self):
        """If adjusted_unit_cost is 0 but material + labor > 0, use sum."""
        mock_enriched = [
            {"unit_cost": 0, "material_cost": 5.00, "labor_cost": 3.00},
        ]

        items = [
            {
                "csi_code": "09 29 00",
                "description": "drywall",
                "quantity": 100,
                "unit": "SF",
                "source": "llm_extracted",
            },
        ]

        with patch(
            "app.services.estimating.cost_database.match_costs",
            new_callable=AsyncMock,
            return_value=mock_enriched,
        ):
            from app.services.estimating.plan_takeoff_service import _enrich_with_costs

            result = await _enrich_with_costs(None, items, None)

        assert result[0]["unit_cost"] == Decimal("8.00")
        assert result[0]["total_cost"] == Decimal("800.00")


# ===========================================================================
# TestFullPipeline — 5 tests
# ===========================================================================


class TestFullPipeline:
    """Tests for the full process_plan_upload pipeline."""

    @pytest.mark.asyncio
    async def test_pipeline_creates_takeoff_record(self):
        """Pipeline should create a PlanTakeoff with line items."""
        mock_pdf_result = MagicMock()
        mock_pdf_result.pages = [
            MagicMock(
                text="FLOOR PLAN\nRoom 101: 12x15 with 2 windows\nConcrete slab 4 inch",
                page_number=1,
            ),
        ]
        mock_pdf_result.page_count = 1

        mock_elements = [
            {
                "element_type": "concrete_slab",
                "description": "4 inch slab",
                "quantity": 180,
                "unit": "SF",
                "material": "concrete",
            },
            {
                "element_type": "window",
                "description": "3x4 double hung",
                "quantity": 2,
                "unit": "EA",
                "material": "vinyl",
            },
        ]

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)

        # Track objects added to DB
        added_objects = []
        mock_db.add = lambda obj: added_objects.append(obj)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        with (
            patch("app.services.ingestion.pdf_parser.parse_pdf", return_value=mock_pdf_result),
            patch(
                "app.services.estimating.plan_takeoff_service._extract_elements_from_page",
                new_callable=AsyncMock,
                return_value=mock_elements,
            ),
            patch(
                "app.services.estimating.plan_takeoff_service._enrich_with_costs",
                new_callable=AsyncMock,
                side_effect=lambda db, items, loc: items,
            ),
        ):
            from app.services.estimating.plan_takeoff_service import process_plan_upload

            await process_plan_upload(
                db=mock_db,
                project_id=uuid.uuid4(),
                file_bytes=b"fake-pdf-bytes",
                file_name="floor_plan.pdf",
                drawing_type="floor_plan",
            )

        # Should have added: 1 takeoff + 2 line items = objects added
        assert len(added_objects) >= 2  # line items

    @pytest.mark.asyncio
    async def test_pipeline_handles_parse_failure(self):
        """Pipeline should mark takeoff as failed on PDF parse error."""
        mock_db = AsyncMock()
        added_objects = []
        mock_db.add = lambda obj: added_objects.append(obj)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        with patch(
            "app.services.ingestion.pdf_parser.parse_pdf",
            side_effect=Exception("Corrupt PDF"),
        ):
            from app.services.estimating.plan_takeoff_service import process_plan_upload

            result = await process_plan_upload(
                db=mock_db,
                project_id=uuid.uuid4(),
                file_bytes=b"corrupt-data",
                file_name="bad.pdf",
            )

        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_pipeline_handles_no_elements(self):
        """Pipeline should mark takeoff as failed when no elements extracted."""
        mock_pdf_result = MagicMock()
        mock_pdf_result.pages = [MagicMock(text="", page_number=1)]
        mock_pdf_result.page_count = 1

        mock_db = AsyncMock()
        added_objects = []
        mock_db.add = lambda obj: added_objects.append(obj)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        with (
            patch("app.services.ingestion.pdf_parser.parse_pdf", return_value=mock_pdf_result),
            patch(
                "app.services.estimating.plan_takeoff_service._extract_elements_from_page",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            from app.services.estimating.plan_takeoff_service import process_plan_upload

            result = await process_plan_upload(
                db=mock_db,
                project_id=uuid.uuid4(),
                file_bytes=b"empty-plan",
                file_name="empty.pdf",
            )

        assert result.status == "failed"
        assert "No construction elements" in result.extraction_metadata.get("error", "")

    @pytest.mark.asyncio
    async def test_pipeline_with_location(self):
        """Pipeline should pass location through to cost enrichment."""
        mock_pdf_result = MagicMock()
        mock_pdf_result.pages = [MagicMock(text="concrete slab", page_number=1)]
        mock_pdf_result.page_count = 1

        mock_elements = [
            {"element_type": "concrete_slab", "description": "slab", "quantity": 100, "unit": "SF"},
        ]

        mock_db = AsyncMock()
        mock_db.add = lambda obj: None
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        enrich_calls = []

        async def mock_enrich(db, items, loc):
            enrich_calls.append(loc)
            return items

        with (
            patch("app.services.ingestion.pdf_parser.parse_pdf", return_value=mock_pdf_result),
            patch(
                "app.services.estimating.plan_takeoff_service._extract_elements_from_page",
                new_callable=AsyncMock,
                return_value=mock_elements,
            ),
            patch(
                "app.services.estimating.plan_takeoff_service._enrich_with_costs",
                new_callable=AsyncMock,
                side_effect=mock_enrich,
            ),
        ):
            from app.services.estimating.plan_takeoff_service import process_plan_upload

            await process_plan_upload(
                db=mock_db,
                project_id=uuid.uuid4(),
                file_bytes=b"pdf-bytes",
                file_name="plan.pdf",
                location={"state": "CA"},
            )

        assert len(enrich_calls) == 1
        assert enrich_calls[0] == {"state": "CA"}

    @pytest.mark.asyncio
    async def test_pipeline_multi_page(self):
        """Pipeline should extract elements from all pages."""
        mock_pdf_result = MagicMock()
        mock_pdf_result.pages = [
            MagicMock(text="Page 1 text", page_number=1),
            MagicMock(text="Page 2 text", page_number=2),
            MagicMock(text="Page 3 text", page_number=3),
        ]
        mock_pdf_result.page_count = 3

        extract_calls = []

        async def mock_extract(text, page_num, drawing_type):
            extract_calls.append(page_num)
            return [
                {
                    "element_type": "drywall",
                    "description": f"page {page_num}",
                    "quantity": 100,
                    "unit": "SF",
                }
            ]

        mock_db = AsyncMock()
        mock_db.add = lambda obj: None
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        with (
            patch("app.services.ingestion.pdf_parser.parse_pdf", return_value=mock_pdf_result),
            patch(
                "app.services.estimating.plan_takeoff_service._extract_elements_from_page",
                new_callable=AsyncMock,
                side_effect=mock_extract,
            ),
            patch(
                "app.services.estimating.plan_takeoff_service._enrich_with_costs",
                new_callable=AsyncMock,
                side_effect=lambda db, items, loc: items,
            ),
        ):
            from app.services.estimating.plan_takeoff_service import process_plan_upload

            await process_plan_upload(
                db=mock_db,
                project_id=uuid.uuid4(),
                file_bytes=b"multi-page-pdf",
                file_name="multipage.pdf",
            )

        assert extract_calls == [1, 2, 3]


# ===========================================================================
# TestConvertToEstimate — 5 tests
# ===========================================================================


class TestConvertToEstimate:
    """Tests for converting takeoff to cost estimate."""

    @pytest.mark.asyncio
    async def test_convert_creates_estimate(self):
        """Should create a CostEstimate with line items from priced takeoff items."""
        takeoff_id = uuid.uuid4()
        project_id = uuid.uuid4()

        mock_line1 = MagicMock()
        mock_line1.id = uuid.uuid4()
        mock_line1.cost_item_id = None
        mock_line1.csi_code = "09 29 00"
        mock_line1.description = "Drywall"
        mock_line1.quantity = Decimal("500")
        mock_line1.unit = "SF"
        mock_line1.unit_cost = Decimal("12.50")
        mock_line1.total_cost = Decimal("6250.00")
        mock_line1.material_cost = Decimal("8.00")
        mock_line1.labor_cost = Decimal("4.50")
        mock_line1.confidence = Decimal("0.900")
        mock_line1.source = "cost_db"
        mock_line1.element_type = "finish"

        mock_takeoff = MagicMock()
        mock_takeoff.id = takeoff_id
        mock_takeoff.project_id = project_id
        mock_takeoff.name = "Test Takeoff"
        mock_takeoff.status = "completed"
        mock_takeoff.confidence_score = Decimal("0.850")
        mock_takeoff.created_by = None
        mock_takeoff.line_items = [mock_line1]

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_takeoff)
        added_objects = []
        mock_db.add = lambda obj: added_objects.append(obj)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        from app.services.estimating.plan_takeoff_service import convert_takeoff_to_estimate

        await convert_takeoff_to_estimate(
            db=mock_db,
            takeoff_id=takeoff_id,
            estimate_name="My Estimate",
            contingency_pct=Decimal("15.0"),
        )

        # Should have created: 1 estimate + 1 line item + 1 contingency
        assert len(added_objects) >= 2
        assert mock_takeoff.status == "converted"

    @pytest.mark.asyncio
    async def test_convert_not_found_raises(self):
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)

        from app.services.estimating.plan_takeoff_service import convert_takeoff_to_estimate

        with pytest.raises(ValueError, match="not found"):
            await convert_takeoff_to_estimate(mock_db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_convert_wrong_status_raises(self):
        mock_takeoff = MagicMock()
        mock_takeoff.status = "processing"

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_takeoff)

        from app.services.estimating.plan_takeoff_service import convert_takeoff_to_estimate

        with pytest.raises(ValueError, match="must be 'completed'"):
            await convert_takeoff_to_estimate(mock_db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_convert_no_priced_items_raises(self):
        mock_line = MagicMock()
        mock_line.total_cost = Decimal("0")

        mock_takeoff = MagicMock()
        mock_takeoff.status = "completed"
        mock_takeoff.line_items = [mock_line]

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_takeoff)

        from app.services.estimating.plan_takeoff_service import convert_takeoff_to_estimate

        with pytest.raises(ValueError, match="no priced items"):
            await convert_takeoff_to_estimate(mock_db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_convert_no_line_items_raises(self):
        mock_takeoff = MagicMock()
        mock_takeoff.status = "completed"
        mock_takeoff.line_items = []

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_takeoff)

        from app.services.estimating.plan_takeoff_service import convert_takeoff_to_estimate

        with pytest.raises(ValueError, match="no line items"):
            await convert_takeoff_to_estimate(mock_db, uuid.uuid4())


# ===========================================================================
# TestConfidence — 4 tests
# ===========================================================================


class TestConfidence:
    """Tests for takeoff confidence computation."""

    def test_all_cost_db_items(self):
        items = [
            {"total_cost": 1000, "source": "cost_db", "csi_code": "03 30 00"},
            {"total_cost": 2000, "source": "cost_db", "csi_code": "09 29 00"},
        ]
        conf = compute_takeoff_confidence(items)
        assert conf == Decimal("0.900")

    def test_mixed_sources(self):
        items = [
            {"total_cost": 1000, "source": "cost_db", "csi_code": "03 30 00"},
            {"total_cost": 1000, "source": "llm_extracted", "csi_code": "09 29 00"},
        ]
        conf = compute_takeoff_confidence(items)
        # Weighted: (0.9 * 1000 + 0.6 * 1000) / 2000 = 0.75
        assert conf == Decimal("0.750")

    def test_no_csi_items(self):
        items = [
            {"total_cost": 100, "source": "llm_extracted", "csi_code": None},
        ]
        conf = compute_takeoff_confidence(items)
        assert conf == Decimal("0.300")

    def test_empty_items(self):
        conf = compute_takeoff_confidence([])
        assert conf == Decimal("0.00")


# ===========================================================================
# TestListTakeoffs — 4 tests
# ===========================================================================


class TestListTakeoffs:
    """Tests for takeoff listing and retrieval."""

    @pytest.mark.asyncio
    async def test_get_takeoff_found(self):
        mock_takeoff = MagicMock()
        mock_takeoff.id = uuid.uuid4()
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_takeoff)

        from app.services.estimating.plan_takeoff_service import get_takeoff

        result = await get_takeoff(mock_db, mock_takeoff.id)
        assert result is not None
        assert result.id == mock_takeoff.id

    @pytest.mark.asyncio
    async def test_get_takeoff_not_found(self):
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)

        from app.services.estimating.plan_takeoff_service import get_takeoff

        result = await get_takeoff(mock_db, uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_list_takeoffs_returns_results(self):
        mock_takeoffs = [MagicMock(), MagicMock()]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_takeoffs

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.estimating.plan_takeoff_service import list_takeoffs

        results = await list_takeoffs(mock_db, uuid.uuid4())
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_takeoffs_empty(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.estimating.plan_takeoff_service import list_takeoffs

        results = await list_takeoffs(mock_db, uuid.uuid4())
        assert results == []


# ===========================================================================
# TestInputValidation — 4 tests
# ===========================================================================


class TestInputValidation:
    """Tests for input validation and edge cases."""

    def test_element_type_classification_structural(self):
        assert _classify_element_type("concrete_slab") == "structural"
        assert _classify_element_type("steel_beam") == "structural"
        assert _classify_element_type("wood_framing") == "structural"

    def test_element_type_classification_finish(self):
        assert _classify_element_type("painting") == "finish"
        assert _classify_element_type("carpet") == "finish"
        assert _classify_element_type("ceramic_tile") == "finish"

    def test_element_type_classification_mep(self):
        assert _classify_element_type("hvac_ductwork") == "mechanical"
        assert _classify_element_type("electrical_panel") == "electrical"
        assert _classify_element_type("plumbing_fixture") == "plumbing"

    def test_element_type_classification_default(self):
        """Unknown types should default to 'material'."""
        assert _classify_element_type("something_unknown") == "material"

    def test_regional_cost_factors_complete(self):
        """All defined regions should have factors between 0.8 and 1.3."""
        assert len(REGIONAL_COST_FACTORS) >= 9
        for region, factor in REGIONAL_COST_FACTORS.items():
            assert Decimal("0.8") <= factor <= Decimal("1.3"), f"Bad factor for {region}: {factor}"

    def test_round2_precision(self):
        assert _round2(Decimal("1.005")) == Decimal("1.01")
        assert _round2(Decimal("1.004")) == Decimal("1.00")
        assert _round2(Decimal("99.999")) == Decimal("100.00")

    def test_parse_llm_json_non_array(self):
        """A JSON object (not array) should return empty list."""
        result = _parse_llm_json('{"element_type": "slab"}')
        assert result == []
