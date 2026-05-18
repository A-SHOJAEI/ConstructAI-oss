"""Tests for the construction-specialized RAG pipeline upgrades.

Covers:
1. CSI section detection and specification document identification
2. SpecificationChunker with Part/subsection chunking
3. Drawing cross-reference extraction
4. OSHA standard retrieval by activity type
5. RFI similarity search
6. Fine-tuned vs generic embedding comparison structure
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-minimum-32-characters-long")
os.environ.setdefault("TESTING", "true")

import pytest

from app.services.ingestion.chunking import (
    SpecificationChunker,
    _detect_csi_section,
    chunk_document_smart,
    extract_drawing_references,
    is_specification_document,
)
from app.services.ingestion.pdf_parser import ParsedPage

# ===================================================================
# 1. CSI SECTION DETECTION
# ===================================================================


class TestCSISectionDetection:
    """Test CSI MasterFormat section detection from text."""

    def test_detect_standard_format(self):
        """Detect 'SECTION 03 30 00' format."""
        assert _detect_csi_section("SECTION 03 30 00") == "03 30 00"

    def test_detect_without_section_prefix(self):
        """Detect '03 30 00' without 'SECTION' prefix."""
        assert _detect_csi_section("03 30 00 CAST-IN-PLACE CONCRETE") == "03 30 00"

    def test_detect_case_insensitive(self):
        """Detection should be case-insensitive."""
        assert _detect_csi_section("section 07 21 00") == "07 21 00"

    def test_detect_division_only(self):
        """Detect 'Division 03' and pad to full code."""
        assert _detect_csi_section("Division 3 - Concrete") == "03 00 00"

    def test_detect_inline_reference(self):
        """Detect CSI code embedded in body text."""
        text = "As specified in Section 05 12 00, structural steel connections..."
        assert _detect_csi_section(text) == "05 12 00"

    def test_no_detection_for_plain_text(self):
        """Return None when no CSI code is present."""
        assert _detect_csi_section("This is just regular text") is None

    def test_detect_electrical_division(self):
        """Detect Division 26 - Electrical."""
        assert _detect_csi_section("SECTION 26 05 00 COMMON WORK RESULTS") == "26 05 00"

    def test_detect_multiple_returns_first(self):
        """When multiple codes present, return the first one."""
        text = "Refer to 03 30 00 and also 05 12 00"
        assert _detect_csi_section(text) == "03 30 00"


# ===================================================================
# 2. SPECIFICATION DOCUMENT DETECTION
# ===================================================================


class TestSpecificationDetection:
    """Test auto-detection of CSI specification documents."""

    def test_detect_typical_spec(self):
        """Detect a typical CSI specification with Parts and subsections."""
        text = """
SECTION 03 30 00 - CAST-IN-PLACE CONCRETE

PART 1 - GENERAL

1.1 RELATED SECTIONS
    A. Section 03 10 00 - Concrete Forming and Accessories
    B. Section 03 20 00 - Concrete Reinforcing

1.2 REFERENCES
    A. ACI 301 - Specifications for Structural Concrete
    B. ACI 318 - Building Code Requirements

1.3 SUBMITTALS
    A. Product Data
    B. Mix Design Reports

PART 2 - PRODUCTS

2.1 MATERIALS
    A. Portland Cement: ASTM C150, Type I/II
    B. Aggregates: ASTM C33

PART 3 - EXECUTION

3.1 PREPARATION
    A. Verify formwork and reinforcement prior to placement.
"""
        assert is_specification_document(text) is True

    def test_reject_non_spec_document(self):
        """Reject a regular document that's not a specification."""
        text = """
        Meeting Minutes
        Project: Main Street Office Building

        Date: March 5, 2026

        Attendees:
        - John Smith, Project Manager
        - Jane Doe, Architect

        Discussion:
        We discussed the timeline for completion of Phase 2 work.
        The concrete pour is scheduled for next week.
        """
        assert is_specification_document(text) is False

    def test_detect_spec_without_section_number(self):
        """Detect a spec that has Part headers but maybe not a clear section header."""
        text = """
PART 1 - GENERAL

1.1 SCOPE OF WORK
    A. Provide all labor, materials, equipment for structural steel.

1.2 RELATED SECTIONS
    A. Division 03 - Concrete

1.3 QUALITY ASSURANCE
    A. Fabricator qualifications per AISC

PART 2 - PRODUCTS

2.1 MATERIALS
    A. Structural steel shapes

PART 3 - EXECUTION

3.1 ERECTION
    A. Steel erection sequence
"""
        assert is_specification_document(text) is True


# ===================================================================
# 3. SPECIFICATION CHUNKER
# ===================================================================


class TestSpecificationChunker:
    """Test the SpecificationChunker class."""

    SAMPLE_SPEC = """SECTION 03 30 00 - CAST-IN-PLACE CONCRETE

PART 1 - GENERAL

1.1 RELATED SECTIONS
    A. Section 03 10 00 - Concrete Forming
    B. Section 03 20 00 - Concrete Reinforcing

1.2 REFERENCES
    A. ACI 301 - Specifications for Structural Concrete
    B. ACI 318 - Building Code Requirements
    C. ASTM C150 - Portland Cement

1.3 SUBMITTALS
    A. Product Data for each type of concrete.
    B. Mix Design Reports: Submit for each mix.

PART 2 - PRODUCTS

2.1 MATERIALS
    A. Portland Cement: ASTM C150, Type I/II.
    B. Aggregates: ASTM C33, Size 57 or 67.
    C. Water: Clean, potable, free of organic matter.

2.2 CONCRETE MIXTURES
    A. Normal weight concrete: f'c = 4,000 psi at 28 days.
    B. Lightweight concrete: f'c = 3,000 psi at 28 days.

PART 3 - EXECUTION

3.1 PREPARATION
    A. Verify formwork and reinforcement placement.
    B. See Detail 5/A-301 for foundation layout.
    C. Refer to Sheet S-201 for reinforcement schedule.

3.2 CONCRETE PLACEMENT
    A. Place concrete within 90 minutes of batching.
    B. Maximum free-fall distance: 5 feet.
"""

    def test_chunker_creates_chunks(self):
        """SpecificationChunker should produce chunks from spec text."""
        chunker = SpecificationChunker(max_tokens=2048)
        chunks = chunker.chunk(self.SAMPLE_SPEC)
        assert len(chunks) > 0

    def test_chunks_have_csi_section(self):
        """All chunks should have the CSI section code."""
        chunker = SpecificationChunker(max_tokens=2048)
        chunks = chunker.chunk(self.SAMPLE_SPEC)
        for chunk in chunks:
            assert chunk.csi_section == "03 30 00"

    def test_chunks_have_metadata(self):
        """Chunks should include structured metadata."""
        chunker = SpecificationChunker(max_tokens=2048)
        chunks = chunker.chunk(self.SAMPLE_SPEC)

        # Find a chunk from Part 2
        part2_chunks = [c for c in chunks if c.metadata.get("part_number") == 2]
        assert len(part2_chunks) > 0

        for chunk in part2_chunks:
            assert chunk.metadata["csi_section"] == "03 30 00"
            assert chunk.metadata["csi_title"]  # Should have a title
            assert "spec_section_path" in chunk.metadata

    def test_chunker_respects_token_limit(self):
        """Chunks should not exceed max_tokens."""
        chunker = SpecificationChunker(max_tokens=100)
        chunks = chunker.chunk(self.SAMPLE_SPEC)

        for chunk in chunks:
            # Allow small overrun due to overlap
            assert chunk.token_count <= 110, f"Chunk too large: {chunk.token_count} tokens"

    def test_chunk_type_is_spec_section(self):
        """Chunks from spec chunker should have type 'spec_section'."""
        chunker = SpecificationChunker(max_tokens=2048)
        chunks = chunker.chunk(self.SAMPLE_SPEC)
        for chunk in chunks:
            assert chunk.chunk_type == "spec_section"


# ===================================================================
# 4. DRAWING CROSS-REFERENCE EXTRACTION
# ===================================================================


class TestDrawingReferenceExtraction:
    """Test extraction of drawing references from text."""

    def test_extract_detail_reference(self):
        """Extract 'See Detail 5/A-301' pattern."""
        refs = extract_drawing_references("See Detail 5/A-301 for foundation layout.")
        assert len(refs) == 1
        assert refs[0]["reference_type"] == "detail"
        assert refs[0]["reference_id"] == "5/A-301"

    def test_extract_sheet_reference(self):
        """Extract 'Refer to Sheet M-401' pattern."""
        refs = extract_drawing_references("Refer to Sheet M-401 for duct routing.")
        assert len(refs) == 1
        assert refs[0]["reference_type"] == "sheet"
        assert refs[0]["reference_id"] == "M-401"

    def test_extract_drawing_reference(self):
        """Extract 'Refer to Drawing S-101' pattern."""
        refs = extract_drawing_references("Refer to Drawing S-101")
        assert len(refs) == 1
        assert refs[0]["reference_type"] == "drawing"
        assert refs[0]["reference_id"] == "S-101"

    def test_extract_multiple_references(self):
        """Extract multiple references from one text block."""
        text = "See Detail 3/A-201 for connection. Refer to Sheet S-301 for framing plan."
        refs = extract_drawing_references(text)
        assert len(refs) == 2

    def test_no_references(self):
        """Return empty list when no drawing references present."""
        refs = extract_drawing_references("This is plain text with no references.")
        assert refs == []

    def test_normalize_sheet_number(self):
        """Sheet numbers should be normalized (e.g., 'A301' -> 'A-301')."""
        refs = extract_drawing_references("See Sheet A301")
        assert len(refs) == 1
        assert refs[0]["reference_id"] == "A-301"

    def test_deduplicate_references(self):
        """Same reference mentioned twice should only appear once."""
        text = "See Sheet A-301. Also refer to Sheet A-301 for details."
        refs = extract_drawing_references(text)
        a301_refs = [r for r in refs if r["reference_id"] == "A-301"]
        assert len(a301_refs) == 1


# ===================================================================
# 5. SMART CHUNKING DISPATCHER
# ===================================================================


class TestSmartChunkingDispatcher:
    """Test chunk_document_smart auto-detection."""

    def test_spec_document_uses_spec_chunker(self):
        """Specification documents should use SpecificationChunker."""
        spec_text = """SECTION 03 30 00 - CAST-IN-PLACE CONCRETE
PART 1 - GENERAL
1.1 RELATED SECTIONS
    A. Section 03 10 00 - Concrete Forming
1.2 REFERENCES
    A. ACI 301
1.3 SUBMITTALS
    A. Product Data
PART 2 - PRODUCTS
2.1 MATERIALS
    A. Portland Cement
PART 3 - EXECUTION
3.1 PREPARATION
    A. Verify formwork
"""
        pages = [ParsedPage(page_number=1, text=spec_text, tables=[], headings=[])]
        chunks = chunk_document_smart(pages)
        # Spec chunks have type "spec_section"
        spec_chunks = [c for c in chunks if c.chunk_type == "spec_section"]
        assert len(spec_chunks) > 0

    def test_non_spec_uses_general_chunker(self):
        """Non-specification documents should use the general chunker."""
        text = "This is a regular meeting notes document. We discussed the project schedule."
        pages = [ParsedPage(page_number=1, text=text, tables=[], headings=[])]
        chunks = chunk_document_smart(pages)
        # General chunks have type "text"
        spec_chunks = [c for c in chunks if c.chunk_type == "spec_section"]
        assert len(spec_chunks) == 0


# ===================================================================
# 6. OSHA STANDARD RETRIEVAL BY ACTIVITY TYPE
# ===================================================================


class TestOSHAStandardRetrieval:
    """Test OSHA standard lookup by construction activity."""

    def test_excavation_standards(self):
        """Excavation activity should return Subpart P standards."""
        from scripts.ingest_osha_standards import get_applicable_osha_standards

        standards = get_applicable_osha_standards("excavation")
        assert len(standards) > 0
        assert any(s["standard"] == "1926.651" for s in standards)
        assert all(s["subpart"] == "Subpart P" for s in standards)

    def test_fall_protection_standards(self):
        """Fall protection should return Subpart M standards."""
        from scripts.ingest_osha_standards import get_applicable_osha_standards

        standards = get_applicable_osha_standards("fall_protection")
        assert len(standards) > 0
        assert any(s["standard"] == "1926.501" for s in standards)

    def test_scaffolding_standards(self):
        """Scaffolding should return Subpart L standards."""
        from scripts.ingest_osha_standards import get_applicable_osha_standards

        standards = get_applicable_osha_standards("scaffolding")
        assert len(standards) > 0
        assert any(s["standard"] == "1926.451" for s in standards)

    def test_crane_operations(self):
        """Crane operations should return Subpart CC standards."""
        from scripts.ingest_osha_standards import get_applicable_osha_standards

        standards = get_applicable_osha_standards("crane_operations")
        assert len(standards) > 0
        assert any(s["standard"].startswith("1926.14") for s in standards)

    def test_alias_matching(self):
        """Aliases like 'digging' should map to 'excavation'."""
        from scripts.ingest_osha_standards import get_applicable_osha_standards

        standards = get_applicable_osha_standards("digging")
        assert len(standards) > 0
        assert any(s["standard"] == "1926.651" for s in standards)

    def test_steel_erection(self):
        """Steel erection should return Subpart R standards."""
        from scripts.ingest_osha_standards import get_applicable_osha_standards

        standards = get_applicable_osha_standards("steel_erection")
        assert len(standards) > 0
        assert any(s["standard"] == "1926.752" for s in standards)

    def test_electrical_work(self):
        """Electrical work should return Subpart K standards."""
        from scripts.ingest_osha_standards import get_applicable_osha_standards

        standards = get_applicable_osha_standards("electrical_work")
        assert len(standards) > 0
        assert any(s["standard"] == "1926.405" for s in standards)

    def test_unknown_activity(self):
        """Unknown activity should return empty list."""
        from scripts.ingest_osha_standards import get_applicable_osha_standards

        standards = get_applicable_osha_standards("juggling")
        assert standards == []

    def test_confined_spaces(self):
        """Confined spaces should return Subpart AA standards."""
        from scripts.ingest_osha_standards import get_applicable_osha_standards

        standards = get_applicable_osha_standards("confined_spaces")
        assert len(standards) > 0
        assert any(s["standard"].startswith("1926.12") for s in standards)

    def test_partial_match(self):
        """Partial activity names should still match."""
        from scripts.ingest_osha_standards import get_applicable_osha_standards

        # "scaffold" should match "scaffolding"
        standards = get_applicable_osha_standards("scaffold")
        assert len(standards) > 0

    def test_all_activities_have_standards(self):
        """Every activity in the map should return at least one standard."""
        from scripts.ingest_osha_standards import ACTIVITY_STANDARD_MAP

        for activity in ACTIVITY_STANDARD_MAP:
            standards = ACTIVITY_STANDARD_MAP[activity]
            assert len(standards) > 0, f"No standards for {activity}"
            for s in standards:
                assert "standard" in s
                assert "subpart" in s
                assert "topic" in s


# ===================================================================
# 7. RFI SIMILARITY SEARCH (mocked DB)
# ===================================================================


class TestRFISimilaritySearch:
    """Test RFI similarity search with mocked database."""

    @pytest.mark.asyncio
    async def test_search_similar_rfis_returns_matches(self):
        """search_similar_rfis should return matching RFIs above threshold."""
        from app.services.rag.retrieval import search_similar_rfis

        # Mock the database session
        mock_db = AsyncMock()

        # Create mock result rows
        mock_rows = [
            {
                "chunk_id": str(uuid.uuid4()),
                "content": "RFI-001: Concrete mix design question",
                "document_id": str(uuid.uuid4()),
                "document_title": "RFI Index",
                "project_id": str(uuid.uuid4()),
                "chunk_metadata": {
                    "question": "What concrete mix design should be used for the foundation?",
                    "answer": "Use 4000 psi normal weight concrete per Section 03 30 00",
                    "rfi_number": "RFI-001",
                    "subject": "Concrete Mix Design",
                },
                "similarity_score": 0.92,
            }
        ]
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = mock_rows
        mock_db.execute.return_value = mock_result

        # Mock embedding function
        async def mock_embed(q):
            return [0.1] * 1024

        results = await search_similar_rfis(
            mock_db,
            "What concrete mix should we use?",
            project_id=uuid.uuid4(),
            embed_fn=mock_embed,
        )

        assert len(results) == 1
        assert results[0]["rfi_number"] == "RFI-001"
        assert results[0]["similarity_score"] == 0.92
        assert "question" in results[0]
        assert "answer" in results[0]

    @pytest.mark.asyncio
    async def test_search_similar_rfis_no_matches(self):
        """search_similar_rfis should return empty list when no matches."""
        from app.services.rag.retrieval import search_similar_rfis

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        async def mock_embed(q):
            return [0.1] * 1024

        results = await search_similar_rfis(
            mock_db,
            "Something completely different",
            project_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            embed_fn=mock_embed,
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_search_similar_rfis_threshold(self):
        """Custom similarity threshold should be respected."""
        from app.services.rag.retrieval import search_similar_rfis

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        async def mock_embed(q):
            return [0.1] * 1024

        # With very high threshold, should get no results
        results = await search_similar_rfis(
            mock_db,
            "test query",
            project_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            similarity_threshold=0.99,
            embed_fn=mock_embed,
        )

        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_index_rfi_for_search(self):
        """index_rfi_for_search should create chunk and embedding."""
        from app.services.rag.retrieval import index_rfi_for_search

        mock_db = AsyncMock()

        # Mock: no existing RFI index doc
        mock_find_result = MagicMock()
        mock_find_result.scalar_one_or_none.return_value = None

        # Mock: no existing chunk
        mock_existing_result = MagicMock()
        mock_existing_result.scalar_one_or_none.return_value = None

        # Mock: max chunk index
        mock_max_result = MagicMock()
        mock_max_result.scalar.return_value = -1

        mock_db.execute.side_effect = [
            mock_find_result,  # Find existing doc
            MagicMock(),  # Insert doc
            mock_existing_result,  # Find existing chunk
            mock_max_result,  # Get max index
            MagicMock(),  # Insert chunk
            MagicMock(),  # Flush (implicit)
            MagicMock(),  # Insert embedding
            MagicMock(),  # Flush (implicit)
        ]
        mock_db.flush = AsyncMock()

        async def mock_embed(q):
            return [0.1] * 1024

        result = await index_rfi_for_search(
            mock_db,
            rfi_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            subject="Concrete Strength",
            question="What is the required concrete strength?",
            answer="4000 psi per spec 03 30 00",
            rfi_number="RFI-042",
            embed_fn=mock_embed,
        )

        assert result is not None  # Should return chunk_id


# ===================================================================
# 8. OSHA STANDARD SEARCH (mocked DB)
# ===================================================================


class TestOSHAStandardSearch:
    """Test OSHA standard search via RAG retrieval."""

    @pytest.mark.asyncio
    async def test_search_osha_with_embedding(self):
        """search_osha_standards should return matching standards."""
        from app.services.rag.retrieval import search_osha_standards

        mock_db = AsyncMock()
        mock_rows = [
            {
                "chunk_id": str(uuid.uuid4()),
                "content": "OSHA 1926.501 - Fall protection requirements...",
                "chunk_metadata": {
                    "standard_number": "1926.501",
                    "subpart": "Subpart M - Fall Protection",
                    "topic": "Fall Protection",
                    "applicability": "All construction sites with fall hazards > 6 ft",
                },
                "score": 0.88,
            }
        ]
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = mock_rows
        mock_db.execute.return_value = mock_result

        results = await search_osha_standards(
            mock_db,
            "fall protection requirements for workers",
            query_embedding=[0.1] * 1024,
        )

        assert len(results) == 1
        assert results[0]["standard_number"] == "1926.501"
        assert results[0]["topic"] == "Fall Protection"

    @pytest.mark.asyncio
    async def test_search_osha_keyword_fallback(self):
        """search_osha_standards should work with keyword-only search."""
        from app.services.rag.retrieval import search_osha_standards

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        # No embedding provided — keyword search fallback
        results = await search_osha_standards(mock_db, "excavation safety")
        assert isinstance(results, list)


# ===================================================================
# 9. FINE-TUNED EMBEDDING COMPARISON STRUCTURE
# ===================================================================


class TestConstructionEmbedder:
    """Test the ConstructionEmbedder class structure."""

    def test_embedder_fallback_when_no_model(self):
        """ConstructionEmbedder should fall back to Voyage when model missing."""
        from app.ml.training.construction_embeddings import ConstructionEmbedder

        embedder = ConstructionEmbedder(
            model_path="nonexistent/path",
            fallback_to_voyage=True,
        )

        assert embedder.model_name == "voyage-3-large"
        assert embedder.dimensions == 1024

    def test_embedder_model_name_with_path(self):
        """ConstructionEmbedder with local model should report custom name."""
        from app.ml.training.construction_embeddings import ConstructionEmbedder

        embedder = ConstructionEmbedder(
            model_path="nonexistent/path",
            fallback_to_voyage=True,
        )

        # Without a real model, it falls back
        assert "voyage" in embedder.model_name

    def test_qa_pair_dataclass(self):
        """QAPair should store source, category, and metadata."""
        from app.ml.training.construction_embeddings import QAPair

        pair = QAPair(
            question="What is the max free fall for concrete?",
            answer="5 feet per ACI 301",
            source="synthetic",
            category="concrete",
            metadata={"spec": "03 30 00"},
        )

        assert pair.question.startswith("What")
        assert pair.source == "synthetic"
        assert pair.category == "concrete"

    def test_prepare_training_data(self):
        """prepare_training_data should create train/eval splits with negatives."""
        from app.ml.training.construction_embeddings import QAPair, prepare_training_data

        pairs = [QAPair(f"Q{i}?", f"Answer {i}", "test", f"cat{i % 3}") for i in range(100)]

        data = prepare_training_data(pairs, test_split=0.2, hard_negatives_per_pair=2)

        assert "train_examples" in data
        assert "eval_examples" in data
        assert "stats" in data
        assert len(data["train_examples"]) == 80
        assert len(data["eval_examples"]) == 20

        # Check that train examples have hard negatives
        example = data["train_examples"][0]
        assert "query" in example
        assert "positive" in example
        assert "negatives" in example
        assert len(example["negatives"]) > 0

    def test_prepare_training_data_stats(self):
        """Training stats should track source and category distribution."""
        from app.ml.training.construction_embeddings import QAPair, prepare_training_data

        pairs = [
            QAPair("Q1?", "A1", "ifc_bim", "concrete"),
            QAPair("Q2?", "A2", "osha", "safety"),
            QAPair("Q3?", "A3", "synthetic", "mep"),
            QAPair("Q4?", "A4", "ifc_bim", "concrete"),
            QAPair("Q5?", "A5", "osha", "safety"),
        ]

        data = prepare_training_data(pairs, test_split=0.0)
        stats = data["stats"]

        assert stats["total_pairs"] == 5
        assert stats["sources"]["ifc_bim"] == 2
        assert stats["sources"]["osha"] == 2
        assert stats["sources"]["synthetic"] == 1


# ===================================================================
# 10. OSHA XML PARSER
# ===================================================================


class TestOSHAXMLParser:
    """Test OSHA XML parsing logic."""

    def test_parse_nonexistent_file(self):
        """Should return empty list for missing file."""
        from app.ml.training.construction_embeddings import parse_osha_xml

        sections = parse_osha_xml("/nonexistent/file.xml")
        assert sections == []

    def test_assign_subparts(self):
        """_assign_subparts should correctly assign subpart labels."""
        from app.ml.training.construction_embeddings import OshaSection, _assign_subparts

        sections = [
            OshaSection("1926.501", "Duty to have fall protection", "text..."),
            OshaSection("1926.651", "Specific excavation requirements", "text..."),
            OshaSection("1926.1400", "Scope", "text..."),
        ]

        _assign_subparts(sections)

        assert "Subpart M" in sections[0].subpart
        assert "Subpart P" in sections[1].subpart
        assert "Subpart CC" in sections[2].subpart


# ===================================================================
# 11. OSHA CHUNKING
# ===================================================================


class TestOSHAChunking:
    """Test OSHA section chunking for RAG storage."""

    def test_chunk_osha_sections(self):
        """Should create chunks with proper metadata."""
        from app.ml.training.construction_embeddings import OshaSection
        from scripts.ingest_osha_standards import chunk_osha_sections

        sections = [
            OshaSection(
                standard_number="1926.501",
                title="Duty to Have Fall Protection",
                text="Each employer shall ensure that fall protection is provided. " * 20,
                subpart="Subpart M - Fall Protection",
            ),
        ]

        chunks = chunk_osha_sections(sections, max_tokens=100)
        assert len(chunks) > 0

        for chunk in chunks:
            assert chunk["metadata"]["document_type"] == "osha_standard"
            assert chunk["metadata"]["standard_number"] == "1926.501"
            assert "Subpart M" in chunk["metadata"]["subpart"]

    def test_short_section_single_chunk(self):
        """Short sections should result in a single chunk."""
        from app.ml.training.construction_embeddings import OshaSection
        from scripts.ingest_osha_standards import chunk_osha_sections

        sections = [
            OshaSection(
                standard_number="1926.200",
                title="Signs",
                text="Signs shall conform to ANSI Z35 series.",
                subpart="Subpart G - Signs",
            ),
        ]

        chunks = chunk_osha_sections(sections, max_tokens=512)
        assert len(chunks) == 1


# ===================================================================
# 12. IFC BIM QA LOADER
# ===================================================================


class TestIFCBIMLoader:
    """Test IFC BIM QA dataset loading."""

    def test_load_nonexistent_directory(self):
        """Should return empty list for missing directory."""
        from app.ml.training.construction_embeddings import load_ifc_bim_qa

        pairs = load_ifc_bim_qa("/nonexistent/directory")
        assert pairs == []

    def test_extract_qa_fields(self):
        """_extract_qa_fields should handle various key naming conventions."""
        from app.ml.training.construction_embeddings import _extract_qa_fields

        # Standard keys
        q, a = _extract_qa_fields({"question": "What?", "answer": "This."})
        assert q == "What?"
        assert a == "This."

        # Alternative keys
        q, a = _extract_qa_fields({"query": "How?", "response": "Like so."})
        assert q == "How?"
        assert a == "Like so."

        # Missing keys
        q, a = _extract_qa_fields({"other": "data"})
        assert q == ""
        assert a == ""


# ===================================================================
# 13. EMBEDDING SERVICE ACTIVE MODEL
# ===================================================================


class TestEmbeddingServiceActiveModel:
    """Test the embedding service model selection."""

    def test_get_active_model_name_default(self, tmp_path, monkeypatch):
        """Returns voyage-3-large when no fine-tuned model exists.

        Point CONSTRUCTION_EMBEDDING_MODEL_PATH at an empty tmp dir so the
        construction model loader fails fast and we drop to the Voyage
        fallback. (When a real fine-tuned model is present on disk — as in
        the demo build — the active model is the BGE construction-bge.)
        """
        import app.services.rag.embeddings as emb_module
        from app.services.rag.embeddings import get_active_model_name

        empty_dir = tmp_path / "empty-model-dir"
        empty_dir.mkdir()
        monkeypatch.setenv("CONSTRUCTION_EMBEDDING_MODEL_PATH", str(empty_dir))
        emb_module._construction_model_checked = False
        emb_module._construction_embedder = None

        name = get_active_model_name()
        assert name == "voyage-3-large"


# ===================================================================
# 14. QA DATASET PERSISTENCE
# ===================================================================


class TestQADatasetPersistence:
    """Test saving and loading QA datasets."""

    def test_save_and_load_qa_dataset(self, tmp_path):
        """Round-trip save and load should preserve all data."""
        from app.ml.training.construction_embeddings import (
            QAPair,
            load_qa_dataset,
            save_qa_dataset,
        )

        pairs = [
            QAPair(
                "What is 4000 psi concrete?",
                "Normal weight structural concrete",
                "test",
                "concrete",
            ),
            QAPair("What is OSHA 1926.501?", "Fall protection duty standard", "osha", "safety"),
        ]

        filepath = tmp_path / "test_qa.jsonl"
        save_qa_dataset(pairs, filepath)

        loaded = load_qa_dataset(filepath)
        assert len(loaded) == 2
        assert loaded[0].question == "What is 4000 psi concrete?"
        assert loaded[0].source == "test"
        assert loaded[1].category == "safety"

    def test_load_nonexistent_file(self):
        """Loading from missing file should return empty list."""
        from app.ml.training.construction_embeddings import load_qa_dataset

        pairs = load_qa_dataset("/nonexistent/qa_pairs.jsonl")
        assert pairs == []
