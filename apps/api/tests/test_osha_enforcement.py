"""Tests for OSHA enforcement data integration.

Covers:
- Name normalization and standard parsing (pure helpers)
- Construction sector filtering
- Fuzzy contractor lookup (mocked DB)
- Violation statistics aggregation (mocked DB)
- Contractor OSHA history (mocked DB)
- Compliance checker OSHA context
- Vendor manager OSHA enrichment and risk flags
- Ingestion script CSV parsing and helpers
- API endpoints (mocked service layer)
"""

from __future__ import annotations

import csv
import tempfile
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. Name normalization
# ---------------------------------------------------------------------------


class TestNameNormalization:
    """Tests for normalize_name in osha_lookup."""

    def test_basic(self):
        from app.services.safety.osha_lookup import normalize_name

        assert normalize_name("ABC CONST., INC.") == "abc const inc"

    def test_lowercase(self):
        from app.services.safety.osha_lookup import normalize_name

        assert normalize_name("ACME BUILDERS") == "acme builders"

    def test_strip_punctuation(self):
        from app.services.safety.osha_lookup import normalize_name

        assert normalize_name("Smith & Sons, LLC") == "smith sons llc"

    def test_collapse_whitespace(self):
        from app.services.safety.osha_lookup import normalize_name

        assert normalize_name("  A   B   C  ") == "a b c"

    def test_empty_string(self):
        from app.services.safety.osha_lookup import normalize_name

        assert normalize_name("") == ""

    def test_numbers_preserved(self):
        from app.services.safety.osha_lookup import normalize_name

        assert normalize_name("TEAM 360 INC.") == "team 360 inc"


# ---------------------------------------------------------------------------
# 2. Standard parsing
# ---------------------------------------------------------------------------


class TestStandardParsing:
    """Tests for parse_standard in osha_lookup."""

    def test_fall_protection(self):
        """Canonical CFR form drops leading zeros from the section
        component: "19260501" → "1926.501" (fall protection), NOT
        "1926.0501". Pin the post-fix behavior — legal citations and
        the published CFR use the unpadded form."""
        from app.services.safety.osha_lookup import parse_standard

        assert parse_standard("19260501") == "1926.501"

    def test_respirator(self):
        from app.services.safety.osha_lookup import parse_standard

        assert parse_standard("19100134") == "1910.134"

    def test_empty(self):
        from app.services.safety.osha_lookup import parse_standard

        assert parse_standard("") is None

    def test_short(self):
        from app.services.safety.osha_lookup import parse_standard

        assert parse_standard("192") is None

    def test_whitespace(self):
        from app.services.safety.osha_lookup import parse_standard

        assert parse_standard("  19260501  ") == "1926.501"

    def test_five_chars(self):
        from app.services.safety.osha_lookup import parse_standard

        assert parse_standard("19261") == "1926.1"


# ---------------------------------------------------------------------------
# 3. Construction sector filter
# ---------------------------------------------------------------------------


class TestConstructionFilter:
    """Tests for is_construction in osha_lookup."""

    def test_naics_23(self):
        from app.services.safety.osha_lookup import is_construction

        assert is_construction("236220", None) is True

    def test_naics_non_construction(self):
        from app.services.safety.osha_lookup import is_construction

        assert is_construction("531110", None) is False

    def test_sic_1521(self):
        from app.services.safety.osha_lookup import is_construction

        assert is_construction(None, "1521") is True

    def test_sic_1799(self):
        from app.services.safety.osha_lookup import is_construction

        assert is_construction(None, "1799") is True

    def test_sic_1800(self):
        from app.services.safety.osha_lookup import is_construction

        assert is_construction(None, "1800") is False

    def test_sic_1499(self):
        from app.services.safety.osha_lookup import is_construction

        assert is_construction(None, "1499") is False

    def test_both_none(self):
        from app.services.safety.osha_lookup import is_construction

        assert is_construction(None, None) is False

    def test_invalid_sic(self):
        from app.services.safety.osha_lookup import is_construction

        assert is_construction(None, "abc") is False

    def test_naics_takes_precedence(self):
        from app.services.safety.osha_lookup import is_construction

        # NAICS says construction even if SIC is non-construction
        assert is_construction("236", "9999") is True


# ---------------------------------------------------------------------------
# 4. First token extraction
# ---------------------------------------------------------------------------


class TestFirstToken:
    """Tests for _first_token helper."""

    def test_basic(self):
        from app.services.safety.osha_lookup import _first_token

        assert _first_token("acme builders") == "acme"

    def test_skip_stop_words(self):
        from app.services.safety.osha_lookup import _first_token

        assert _first_token("the acme corp") == "acme"

    def test_skip_short_tokens(self):
        from app.services.safety.osha_lookup import _first_token

        assert _first_token("a b acme") == "acme"

    def test_all_stop_words(self):
        from app.services.safety.osha_lookup import _first_token

        # Fallback to first 3 chars
        assert _first_token("the a an") == "the"

    def test_short_input(self):
        from app.services.safety.osha_lookup import _first_token

        assert _first_token("ab") == "ab"


# ---------------------------------------------------------------------------
# 5. Fuzzy matching (mocked DB)
# ---------------------------------------------------------------------------


def _make_mapping_row(**kwargs):
    """Create a dict-like object that supports both key and item access."""
    return kwargs


def _mock_db_result(rows):
    """Create a mock DB result with mappings().all() returning rows."""
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    return result


class TestFuzzyMatching:
    """Tests for lookup_contractor with mocked DB."""

    @pytest.mark.asyncio
    async def test_exact_match(self):
        from app.services.safety.osha_lookup import lookup_contractor

        db = AsyncMock()
        rows = [
            _make_mapping_row(
                activity_nr="123",
                establishment_name="ACME BUILDERS INC",
                name_normalized="acme builders inc",
                site_city="Richmond",
                site_state="VA",
                open_date=date(2024, 1, 15),
                close_date=date(2024, 6, 1),
                total_penalty=Decimal("5000.00"),
                insp_type="Planned",
            )
        ]
        db.execute.return_value = _mock_db_result(rows)

        results = await lookup_contractor(db, "ACME BUILDERS INC", state="VA")

        assert len(results) == 1
        assert results[0]["match_score"] == 1.0
        assert results[0]["establishment_name"] == "ACME BUILDERS INC"
        assert results[0]["total_penalty"] == 5000.0

    @pytest.mark.asyncio
    async def test_threshold_filtering(self):
        from app.services.safety.osha_lookup import lookup_contractor

        db = AsyncMock()
        rows = [
            _make_mapping_row(
                activity_nr="111",
                establishment_name="ACME BUILDERS",
                name_normalized="acme builders",
                site_city="DC",
                site_state="DC",
                open_date=date(2024, 3, 1),
                close_date=None,
                total_penalty=Decimal("0"),
                insp_type="Complaint",
            ),
            _make_mapping_row(
                activity_nr="222",
                establishment_name="ACORN TRADING",
                name_normalized="acorn trading",
                site_city="DC",
                site_state="DC",
                open_date=date(2024, 2, 1),
                close_date=None,
                total_penalty=Decimal("0"),
                insp_type="Planned",
            ),
        ]
        db.execute.return_value = _mock_db_result(rows)

        # High threshold should filter out weak match
        results = await lookup_contractor(db, "ACME BUILDERS INC", threshold=0.8)

        # Only exact-ish match should pass
        assert all(r["match_score"] >= 0.8 for r in results)

    @pytest.mark.asyncio
    async def test_empty_result(self):
        from app.services.safety.osha_lookup import lookup_contractor

        db = AsyncMock()
        db.execute.return_value = _mock_db_result([])

        results = await lookup_contractor(db, "NONEXISTENT CORP")
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_query(self):
        from app.services.safety.osha_lookup import lookup_contractor

        db = AsyncMock()
        results = await lookup_contractor(db, "")
        assert results == []

    @pytest.mark.asyncio
    async def test_limit(self):
        from app.services.safety.osha_lookup import lookup_contractor

        db = AsyncMock()
        # Create many similar rows
        rows = [
            _make_mapping_row(
                activity_nr=str(i),
                establishment_name=f"ACME BUILDERS {i}",
                name_normalized=f"acme builders {i}",
                site_city="City",
                site_state="VA",
                open_date=date(2024, 1, 1),
                close_date=None,
                total_penalty=Decimal("0"),
                insp_type="Planned",
            )
            for i in range(20)
        ]
        db.execute.return_value = _mock_db_result(rows)

        results = await lookup_contractor(db, "ACME BUILDERS", limit=5)
        assert len(results) <= 5


# ---------------------------------------------------------------------------
# 6. Violation statistics (mocked DB)
# ---------------------------------------------------------------------------


class TestViolationStats:
    """Tests for get_violation_stats with mocked DB."""

    @pytest.mark.asyncio
    async def test_basic_stats(self):
        from app.services.safety.osha_lookup import get_violation_stats

        db = AsyncMock()
        top_rows = [
            _make_mapping_row(
                standard="1926.501",
                count=150,
                willful_count=2,
                repeat_count=5,
                total_penalty=Decimal("250000"),
            ),
            _make_mapping_row(
                standard="1926.451",
                count=100,
                willful_count=0,
                repeat_count=3,
                total_penalty=Decimal("120000"),
            ),
        ]
        total_row = _make_mapping_row(total_inspections=5000, total_violations=8000)

        # First call returns top standards, second returns totals
        db.execute.side_effect = [
            _mock_db_result(top_rows),
            _mock_db_result_first(total_row),
        ]

        stats = await get_violation_stats(db, state="VA")

        assert stats["state"] == "VA"
        assert stats["total_inspections"] == 5000
        assert stats["total_violations"] == 8000
        assert len(stats["top_standards"]) == 2
        assert stats["top_standards"][0]["standard"] == "1926.501"
        assert stats["top_standards"][0]["count"] == 150

    @pytest.mark.asyncio
    async def test_empty_stats(self):
        from app.services.safety.osha_lookup import get_violation_stats

        db = AsyncMock()
        total_row = _make_mapping_row(total_inspections=0, total_violations=0)

        db.execute.side_effect = [
            _mock_db_result([]),
            _mock_db_result_first(total_row),
        ]

        stats = await get_violation_stats(db, state="XX")

        assert stats["top_standards"] == []
        assert stats["total_inspections"] == 0


def _mock_db_result_first(row):
    """Create mock DB result where mappings().first() returns a single row."""
    result = MagicMock()
    result.mappings.return_value.all.return_value = [row]
    result.mappings.return_value.first.return_value = row
    return result


# ---------------------------------------------------------------------------
# 7. Contractor OSHA history (mocked DB)
# ---------------------------------------------------------------------------


class TestContractorHistory:
    """Tests for get_contractor_osha_history with mocked DB."""

    @pytest.mark.asyncio
    async def test_no_match_returns_zeros(self):
        from app.services.safety.osha_lookup import get_contractor_osha_history

        db = AsyncMock()
        # lookup_contractor returns empty
        db.execute.return_value = _mock_db_result([])

        result = await get_contractor_osha_history(db, "NONEXISTENT CORP")

        assert result["matched_name"] is None
        assert result["match_score"] == 0.0
        assert result["inspection_count"] == 0
        assert result["violation_count"] == 0
        assert result["has_recent_willful_repeat"] is False

    @pytest.mark.asyncio
    async def test_with_willful_repeat(self):
        from app.services.safety.osha_lookup import get_contractor_osha_history

        db = AsyncMock()

        # Phase 1: lookup_contractor finds a match
        lookup_rows = [
            _make_mapping_row(
                activity_nr="100",
                establishment_name="BAD CONTRACTOR LLC",
                name_normalized="bad contractor llc",
                site_city="Houston",
                site_state="TX",
                open_date=date(2024, 1, 1),
                close_date=None,
                total_penalty=Decimal("50000"),
                insp_type="Complaint",
            )
        ]
        # Phase 2: inspections for this contractor
        insp_rows = [
            _make_mapping_row(activity_nr="100", total_penalty=Decimal("50000")),
            _make_mapping_row(activity_nr="101", total_penalty=Decimal("25000")),
        ]
        # Phase 3: violation aggregates
        viol_row = _make_mapping_row(violation_count=8, willful_count=2, repeat_count=1)
        # Phase 4: top cited standards
        std_rows = [
            _make_mapping_row(standard_parsed="1926.501", cnt=4),
            _make_mapping_row(standard_parsed="1926.451", cnt=2),
        ]

        db.execute.side_effect = [
            _mock_db_result(lookup_rows),  # lookup_contractor query
            _mock_db_result(insp_rows),  # inspections for contractor
            _mock_db_result_first(viol_row),  # violation aggregates
            _mock_db_result(std_rows),  # top standards
        ]

        result = await get_contractor_osha_history(db, "BAD CONTRACTOR")

        assert result["matched_name"] == "BAD CONTRACTOR LLC"
        assert result["inspection_count"] == 2
        assert result["violation_count"] == 8
        assert result["willful_count"] == 2
        assert result["repeat_count"] == 1
        assert result["total_penalty"] == 75000.0
        assert result["has_recent_willful_repeat"] is True
        assert "1926.501" in result["top_cited_standards"]


# ---------------------------------------------------------------------------
# 8. Compliance checker OSHA context
# ---------------------------------------------------------------------------


class TestComplianceContext:
    """Tests for get_osha_violation_context in compliance_checker."""

    @pytest.mark.asyncio
    async def test_context_message_contains_state(self):
        from app.services.quality.compliance_checker import get_osha_violation_context

        mock_stats = {
            "state": "VA",
            "naics_prefix": "2362",
            "since_date": "2021-03-01",
            "total_inspections": 500,
            "total_violations": 1200,
            "top_standards": [
                {
                    "standard": "1926.501",
                    "title": None,
                    "category": None,
                    "count": 100,
                    "willful_count": 1,
                    "repeat_count": 2,
                    "total_penalty": 50000.0,
                },
            ],
        }

        with patch(
            "app.services.safety.osha_lookup.get_violation_stats",
            new_callable=AsyncMock,
            return_value=mock_stats,
        ):
            db = AsyncMock()
            result = await get_osha_violation_context(db, "VA", project_type="commercial")

        assert result["state"] == "VA"
        assert result["project_type"] == "commercial"
        assert "VA" in result["context_message"]
        assert "1926.501" in result["context_message"]
        assert len(result["top_violations"]) == 1

    @pytest.mark.asyncio
    async def test_enrichment_from_osha_standards(self):
        from app.services.quality.compliance_checker import get_osha_violation_context

        mock_stats = {
            "state": "TX",
            "naics_prefix": None,
            "since_date": "2021-03-01",
            "total_inspections": 200,
            "total_violations": 400,
            "top_standards": [
                {
                    "standard": "1926.501",
                    "title": None,
                    "category": None,
                    "count": 50,
                    "willful_count": 0,
                    "repeat_count": 0,
                    "total_penalty": 10000.0,
                },
            ],
        }

        with patch(
            "app.services.safety.osha_lookup.get_violation_stats",
            new_callable=AsyncMock,
            return_value=mock_stats,
        ):
            db = AsyncMock()
            result = await get_osha_violation_context(db, "TX")

        top = result["top_violations"]
        assert len(top) == 1
        # Should be enriched from OSHA_STANDARDS dict
        assert top[0]["title"] == "Fall Protection - Duty to Have Fall Protection"
        assert top[0]["category"] == "fall_protection"

    @pytest.mark.asyncio
    async def test_no_violations(self):
        from app.services.quality.compliance_checker import get_osha_violation_context

        mock_stats = {
            "state": "WY",
            "naics_prefix": None,
            "since_date": "2021-03-01",
            "total_inspections": 0,
            "total_violations": 0,
            "top_standards": [],
        }

        with patch(
            "app.services.safety.osha_lookup.get_violation_stats",
            new_callable=AsyncMock,
            return_value=mock_stats,
        ):
            db = AsyncMock()
            result = await get_osha_violation_context(db, "WY")

        assert result["top_violations"] == []
        assert "none" in result["context_message"]


# ---------------------------------------------------------------------------
# 9. Vendor enrichment
# ---------------------------------------------------------------------------


class TestVendorEnrichment:
    """Tests for enrich_vendor_with_osha_history in vendor_manager."""

    @pytest.mark.asyncio
    async def test_no_match_returns_original_keys(self):
        from app.services.procurement.vendor_manager import enrich_vendor_with_osha_history

        vendor = {"name": "NONEXISTENT CORP", "vendor_id": "v1"}

        mock_history = {
            "matched_name": None,
            "match_score": 0.0,
            "inspection_count": 0,
            "violation_count": 0,
            "willful_count": 0,
            "repeat_count": 0,
            "total_penalty": 0.0,
            "top_cited_standards": [],
            "has_recent_willful_repeat": False,
        }

        with patch(
            "app.services.safety.osha_lookup.get_contractor_osha_history",
            new_callable=AsyncMock,
            return_value=mock_history,
        ):
            db = AsyncMock()
            enriched = await enrich_vendor_with_osha_history(vendor, db)

        # Original keys preserved
        assert enriched["vendor_id"] == "v1"
        assert enriched["name"] == "NONEXISTENT CORP"
        # OSHA keys added
        assert enriched["osha_matched_name"] is None
        assert enriched["osha_violation_count"] == 0
        assert enriched["osha_has_recent_willful_repeat"] is False

    @pytest.mark.asyncio
    async def test_original_dict_not_mutated(self):
        from app.services.procurement.vendor_manager import enrich_vendor_with_osha_history

        vendor = {"name": "TEST CORP", "vendor_id": "v2"}

        mock_history = {
            "matched_name": "TEST CORP",
            "match_score": 0.95,
            "inspection_count": 3,
            "violation_count": 5,
            "willful_count": 0,
            "repeat_count": 0,
            "total_penalty": 15000.0,
            "top_cited_standards": ["1926.501"],
            "has_recent_willful_repeat": False,
        }

        with patch(
            "app.services.safety.osha_lookup.get_contractor_osha_history",
            new_callable=AsyncMock,
            return_value=mock_history,
        ):
            db = AsyncMock()
            enriched = await enrich_vendor_with_osha_history(vendor, db)

        # Original dict should not have osha_ keys
        assert "osha_matched_name" not in vendor
        assert "osha_matched_name" in enriched

    @pytest.mark.asyncio
    async def test_empty_name_returns_unenriched(self):
        from app.services.procurement.vendor_manager import enrich_vendor_with_osha_history

        vendor = {"name": "", "vendor_id": "v3"}
        db = AsyncMock()

        enriched = await enrich_vendor_with_osha_history(vendor, db)
        assert "osha_matched_name" not in enriched


# ---------------------------------------------------------------------------
# 10. OSHA risk flags
# ---------------------------------------------------------------------------


class TestOshaRiskFlags:
    """Tests for _osha_risk_flags in vendor_manager."""

    def test_willful_repeat_flag(self):
        from app.services.procurement.vendor_manager import _osha_risk_flags

        vendor = {
            "osha_has_recent_willful_repeat": True,
            "osha_willful_count": 2,
            "osha_repeat_count": 1,
            "osha_total_penalty": 30000.0,
        }
        flags = _osha_risk_flags(vendor)
        assert len(flags) == 1
        assert "willful" in flags[0].lower()

    def test_high_penalty_flag(self):
        from app.services.procurement.vendor_manager import _osha_risk_flags

        vendor = {
            "osha_has_recent_willful_repeat": False,
            "osha_total_penalty": 150000.0,
        }
        flags = _osha_risk_flags(vendor)
        assert len(flags) == 1
        assert "$150,000" in flags[0]

    def test_both_flags(self):
        from app.services.procurement.vendor_manager import _osha_risk_flags

        vendor = {
            "osha_has_recent_willful_repeat": True,
            "osha_willful_count": 1,
            "osha_repeat_count": 0,
            "osha_total_penalty": 200000.0,
        }
        flags = _osha_risk_flags(vendor)
        assert len(flags) == 2

    def test_clean_vendor_no_flags(self):
        from app.services.procurement.vendor_manager import _osha_risk_flags

        vendor = {
            "osha_has_recent_willful_repeat": False,
            "osha_total_penalty": 5000.0,
        }
        flags = _osha_risk_flags(vendor)
        assert flags == []

    def test_no_osha_keys_no_flags(self):
        from app.services.procurement.vendor_manager import _osha_risk_flags

        vendor = {"name": "Regular Vendor"}
        flags = _osha_risk_flags(vendor)
        assert flags == []


# ---------------------------------------------------------------------------
# 11. Score vendor with OSHA
# ---------------------------------------------------------------------------


class TestScoreVendorWithOsha:
    """Tests for score_vendor_with_osha wrapper."""

    @pytest.mark.asyncio
    async def test_result_has_osha_history(self):
        from app.services.procurement.vendor_manager import score_vendor_with_osha

        vendor = {
            "name": "TEST BUILDER",
            "vendor_id": "v10",
            "on_time_delivery_pct": 90.0,
            "quality_rating": 4.0,
            "safety_record": 0.9,
            "financial_stability": "strong",
            "past_projects": 15,
            "references": 5,
            "price_competitiveness": 0.7,
        }

        mock_history = {
            "matched_name": "TEST BUILDER INC",
            "match_score": 0.92,
            "inspection_count": 2,
            "violation_count": 3,
            "willful_count": 0,
            "repeat_count": 0,
            "total_penalty": 8000.0,
            "top_cited_standards": ["1926.501"],
            "has_recent_willful_repeat": False,
        }

        with patch(
            "app.services.safety.osha_lookup.get_contractor_osha_history",
            new_callable=AsyncMock,
            return_value=mock_history,
        ):
            db = AsyncMock()
            result = await score_vendor_with_osha(vendor, db)

        assert "osha_history" in result
        assert result["osha_history"]["matched_name"] == "TEST BUILDER INC"
        assert result["osha_history"]["match_score"] == 0.92
        assert result["vendor_id"] == "v10"
        assert "overall_score" in result

    @pytest.mark.asyncio
    async def test_osha_flags_appended_not_replaced(self):
        from app.services.procurement.vendor_manager import score_vendor_with_osha

        # Vendor with existing risk (low on-time delivery)
        vendor = {
            "name": "RISKY BUILDER",
            "vendor_id": "v11",
            "on_time_delivery_pct": 60.0,  # Will trigger delivery risk flag
            "quality_rating": 3.0,
            "safety_record": 1.0,
            "financial_stability": "moderate",
            "past_projects": 5,
            "references": 2,
            "price_competitiveness": 0.5,
        }

        mock_history = {
            "matched_name": "RISKY BUILDER LLC",
            "match_score": 0.9,
            "inspection_count": 5,
            "violation_count": 10,
            "willful_count": 2,
            "repeat_count": 1,
            "total_penalty": 200000.0,
            "top_cited_standards": ["1926.501", "1926.451"],
            "has_recent_willful_repeat": True,
        }

        with patch(
            "app.services.safety.osha_lookup.get_contractor_osha_history",
            new_callable=AsyncMock,
            return_value=mock_history,
        ):
            db = AsyncMock()
            result = await score_vendor_with_osha(vendor, db)

        flags = result["risk_flags"]
        # Should have both existing flags (delivery) and OSHA flags
        flag_text = " ".join(flags).lower()
        assert "delivery" in flag_text  # Pre-existing flag
        assert "osha" in flag_text  # OSHA flag
        assert len(flags) >= 3  # delivery + willful/repeat + penalty


# ---------------------------------------------------------------------------
# 12. Ingestion script helpers
# ---------------------------------------------------------------------------


class TestIngestionHelpers:
    """Tests for helpers in ingest_osha_data.py."""

    def test_normalize_name_matches_lookup(self):
        from app.services.safety.osha_lookup import normalize_name as lookup_normalize
        from scripts.ingest_osha_data import normalize_name as ingest_normalize

        # Both should produce the same normalization
        test_name = "ABC CONST., INC."
        assert ingest_normalize(test_name) == lookup_normalize(test_name)

    def test_parse_standard_matches_lookup(self):
        from app.services.safety.osha_lookup import parse_standard as lookup_parse
        from scripts.ingest_osha_data import parse_standard as ingest_parse

        assert ingest_parse("19260501") == lookup_parse("19260501")

    def test_is_construction_matches_lookup(self):
        from app.services.safety.osha_lookup import is_construction as lookup_is
        from scripts.ingest_osha_data import is_construction as ingest_is

        assert ingest_is("236", None) == lookup_is("236", None)
        assert ingest_is(None, "1521") == lookup_is(None, "1521")
        assert ingest_is(None, "9999") == lookup_is(None, "9999")

    def test_safe_date_mm_dd_yyyy(self):
        from scripts.ingest_osha_data import _safe_date

        assert _safe_date("01/15/2024") == date(2024, 1, 15)

    def test_safe_date_iso(self):
        from scripts.ingest_osha_data import _safe_date

        assert _safe_date("2024-01-15") == date(2024, 1, 15)

    def test_safe_date_empty(self):
        from scripts.ingest_osha_data import _safe_date

        assert _safe_date("") is None

    def test_safe_date_invalid(self):
        from scripts.ingest_osha_data import _safe_date

        assert _safe_date("not-a-date") is None

    def test_safe_decimal(self):
        from scripts.ingest_osha_data import _safe_decimal

        assert _safe_decimal("1234.56") == Decimal("1234.56")

    def test_safe_decimal_empty(self):
        from scripts.ingest_osha_data import _safe_decimal

        assert _safe_decimal("") is None


# ---------------------------------------------------------------------------
# 13. CSV parsing
# ---------------------------------------------------------------------------


class TestCsvParsing:
    """Tests for CSV parsing functions in ingest_osha_data.py."""

    def _write_inspections_csv(self, rows: list[dict], path: Path):
        """Helper to write an inspections CSV file."""
        fieldnames = [
            "activity_nr",
            "estab_name",
            "site_city",
            "site_state",
            "naics_code",
            "sic_code",
            "insp_type",
            "open_date",
            "close_date",
            "total_penalty",
            "insp_scope",
        ]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def test_construction_filter(self):
        from scripts.ingest_osha_data import parse_inspections_csv

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "insp.csv"
            self._write_inspections_csv(
                [
                    {
                        "activity_nr": "1",
                        "estab_name": "CONSTRUCTION CO",
                        "site_city": "DC",
                        "site_state": "DC",
                        "naics_code": "236220",
                        "sic_code": "",
                        "insp_type": "Planned",
                        "open_date": "01/01/2024",
                        "close_date": "",
                        "total_penalty": "0",
                        "insp_scope": "",
                    },
                    {
                        "activity_nr": "2",
                        "estab_name": "RESTAURANT INC",
                        "site_city": "DC",
                        "site_state": "DC",
                        "naics_code": "722511",
                        "sic_code": "",
                        "insp_type": "Planned",
                        "open_date": "01/01/2024",
                        "close_date": "",
                        "total_penalty": "0",
                        "insp_scope": "",
                    },
                ],
                p,
            )

            since = date(2020, 1, 1)
            results = list(parse_inspections_csv(p, since))

        assert len(results) == 1
        assert results[0]["activity_nr"] == "1"
        assert results[0]["name_normalized"] == "construction co"

    def test_date_filter(self):
        from scripts.ingest_osha_data import parse_inspections_csv

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "insp.csv"
            self._write_inspections_csv(
                [
                    {
                        "activity_nr": "1",
                        "estab_name": "OLD CO",
                        "site_city": "",
                        "site_state": "VA",
                        "naics_code": "236",
                        "sic_code": "",
                        "insp_type": "Planned",
                        "open_date": "01/01/2015",
                        "close_date": "",
                        "total_penalty": "0",
                        "insp_scope": "",
                    },
                    {
                        "activity_nr": "2",
                        "estab_name": "NEW CO",
                        "site_city": "",
                        "site_state": "VA",
                        "naics_code": "236",
                        "sic_code": "",
                        "insp_type": "Planned",
                        "open_date": "06/01/2024",
                        "close_date": "",
                        "total_penalty": "0",
                        "insp_scope": "",
                    },
                ],
                p,
            )

            since = date(2023, 1, 1)
            results = list(parse_inspections_csv(p, since))

        assert len(results) == 1
        assert results[0]["activity_nr"] == "2"

    def test_limit(self):
        from scripts.ingest_osha_data import parse_inspections_csv

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "insp.csv"
            rows = [
                {
                    "activity_nr": str(i),
                    "estab_name": f"CO {i}",
                    "site_city": "",
                    "site_state": "VA",
                    "naics_code": "236",
                    "sic_code": "",
                    "insp_type": "Planned",
                    "open_date": "01/01/2024",
                    "close_date": "",
                    "total_penalty": "0",
                    "insp_scope": "",
                }
                for i in range(10)
            ]
            self._write_inspections_csv(rows, p)

            since = date(2020, 1, 1)
            results = list(parse_inspections_csv(p, since, limit=3))

        assert len(results) == 3

    def test_violations_csv_filter_by_activity_nr(self):
        from scripts.ingest_osha_data import parse_violations_csv

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "viol.csv"
            fieldnames = [
                "activity_nr",
                "citation_id",
                "standard",
                "viol_type",
                "penalty",
                "abate_date",
                "issuance_date",
            ]
            with open(p, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow(
                    {
                        "activity_nr": "100",
                        "citation_id": "01001",
                        "standard": "19260501",
                        "viol_type": "S",
                        "penalty": "5000",
                        "abate_date": "",
                        "issuance_date": "01/15/2024",
                    }
                )
                writer.writerow(
                    {
                        "activity_nr": "999",
                        "citation_id": "01002",
                        "standard": "19260451",
                        "viol_type": "O",
                        "penalty": "0",
                        "abate_date": "",
                        "issuance_date": "01/15/2024",
                    }
                )

            valid = {"100"}
            results = list(parse_violations_csv(p, valid))

        assert len(results) == 1
        assert results[0]["activity_nr"] == "100"
        # Canonical CFR form: leading zeros stripped from section.
        assert results[0]["standard_parsed"] == "1926.501"


# ---------------------------------------------------------------------------
# 14. Ingestion dry-run
# ---------------------------------------------------------------------------


class TestIngestionDryRun:
    """Test the dry-run path of ingest_database."""

    @pytest.mark.asyncio
    async def test_dry_run_no_db_writes(self):
        from scripts.ingest_osha_data import ingest_database

        with tempfile.TemporaryDirectory() as tmpdir:
            insp_path = Path(tmpdir) / "insp.csv"
            fieldnames = [
                "activity_nr",
                "estab_name",
                "site_city",
                "site_state",
                "naics_code",
                "sic_code",
                "insp_type",
                "open_date",
                "close_date",
                "total_penalty",
                "insp_scope",
            ]
            with open(insp_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow(
                    {
                        "activity_nr": "1",
                        "estab_name": "DRY RUN CO",
                        "site_city": "DC",
                        "site_state": "DC",
                        "naics_code": "236",
                        "sic_code": "",
                        "insp_type": "Planned",
                        "open_date": "01/01/2024",
                        "close_date": "",
                        "total_penalty": "0",
                        "insp_scope": "",
                    }
                )

            result = await ingest_database(
                inspections_path=insp_path,
                violations_path=None,
                db_url="",
                since=date(2020, 1, 1),
                dry_run=True,
            )

        assert result["dry_run"] is True
        assert result["inspections_parsed"] == 1
        assert result["inspections_loaded"] == 0


# ---------------------------------------------------------------------------
# 15. API endpoints (mocked service)
# ---------------------------------------------------------------------------


class TestApiEndpoints:
    """Tests for OSHA API endpoints."""

    @pytest.fixture
    def client(self):
        """Create a test client with mocked DB and auth dependencies."""
        from app.models.user import User
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.v1.osha import router
        from app.database import get_db
        from app.dependencies import get_current_user

        app = FastAPI()
        app.include_router(router, prefix="/osha")

        async def mock_db():
            yield AsyncMock()

        async def mock_user():
            return User(
                id=uuid.uuid4(),
                email="osha-test@example.com",
                full_name="OSHA Test",
                hashed_password="x",
                org_id=uuid.uuid4(),
                role="org_admin",
                email_verified=True,
            )

        app.dependency_overrides[get_db] = mock_db
        app.dependency_overrides[get_current_user] = mock_user
        return TestClient(app)

    def test_lookup_requires_company_name(self, client):
        resp = client.get("/osha/lookup")
        assert resp.status_code == 422

    def test_lookup_success(self, client):
        with patch(
            "app.api.v1.osha.lookup_contractor",
            new_callable=AsyncMock,
            return_value=[
                {
                    "activity_nr": "100",
                    "establishment_name": "TEST CO",
                    "site_city": "DC",
                    "site_state": "DC",
                    "match_score": 0.95,
                    "open_date": "2024-01-01",
                    "close_date": None,
                    "total_penalty": 5000.0,
                    "insp_type": "Planned",
                }
            ],
        ):
            resp = client.get("/osha/lookup?company_name=TEST CO")

        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "TEST CO"
        assert data["result_count"] == 1
        assert data["results"][0]["match_score"] == 0.95

    def test_stats_success(self, client):
        with patch(
            "app.api.v1.osha.get_violation_stats",
            new_callable=AsyncMock,
            return_value={
                "state": "VA",
                "naics_prefix": "236",
                "since_date": "2021-03-01",
                "total_inspections": 500,
                "total_violations": 1200,
                "top_standards": [
                    {
                        "standard": "1926.501",
                        "title": "Fall Protection",
                        "category": "fall_protection",
                        "count": 100,
                        "willful_count": 1,
                        "repeat_count": 2,
                        "total_penalty": 50000.0,
                    }
                ],
            },
        ):
            resp = client.get("/osha/stats?state=VA&naics=236")

        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "VA"
        assert data["total_inspections"] == 500
        assert len(data["top_standards"]) == 1

    def test_lookup_min_length(self, client):
        resp = client.get("/osha/lookup?company_name=X")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 16. Schema validation
# ---------------------------------------------------------------------------


class TestSchemas:
    """Test that Pydantic schemas validate correctly."""

    def test_lookup_result_schema(self):
        from app.schemas.osha import OshaLookupResult

        r = OshaLookupResult(
            activity_nr="100",
            establishment_name="TEST",
            match_score=0.95,
        )
        assert r.total_penalty == 0.0
        assert r.site_city is None

    def test_lookup_response_schema(self):
        from app.schemas.osha import OshaLookupResponse

        resp = OshaLookupResponse(
            query="TEST",
            results=[],
            result_count=0,
        )
        assert resp.state_filter is None

    def test_stats_response_schema(self):
        from app.schemas.osha import OshaStatsResponse

        resp = OshaStatsResponse(
            since_date="2021-01-01",
            total_inspections=0,
            total_violations=0,
            top_standards=[],
        )
        assert resp.state is None
        assert resp.naics_prefix is None

    def test_standard_stat_schema(self):
        from app.schemas.osha import OshaStandardStat

        s = OshaStandardStat(standard="1926.501", count=50)
        assert s.willful_count == 0
        assert s.title is None
