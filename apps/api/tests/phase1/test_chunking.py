"""Phase 1: Document chunking strategy tests.

These tests verify that the CSI-aware chunking service correctly splits
documents while respecting section boundaries, preserving tables, and
maintaining metadata. No external API calls needed.
"""

from __future__ import annotations

from app.services.ingestion.chunking import chunk_document, count_tokens
from app.services.ingestion.pdf_parser import ParsedPage


def _make_page(
    page_number: int,
    text: str,
    tables: list[list[list[str]]] | None = None,
    headings: list[dict] | None = None,
) -> ParsedPage:
    """Convenience factory for creating ParsedPage instances in tests."""
    return ParsedPage(
        page_number=page_number,
        text=text,
        tables=tables or [],
        headings=headings or [],
    )


class TestChunking:
    """Tests for the CSI-aware document chunking service."""

    def test_csi_aware_chunking_respects_section_boundaries(self):
        """Chunks should not merge text across different CSI division boundaries."""
        # Page 1 covers CSI Section 03 30 00 (Concrete)
        page1 = _make_page(
            page_number=1,
            text=(
                "Section 03 30 00 - Cast-in-Place Concrete\n"
                "This section covers concrete work including foundations, "
                "slabs on grade, and elevated slabs. Portland cement shall "
                "conform to ASTM C150, Type I/II."
            ),
            headings=[
                {"text": "Section 03 30 00 - Cast-in-Place Concrete", "level": 1, "font_size": 16.0}
            ],
        )

        # Page 2 covers CSI Section 09 90 00 (Painting and Coating)
        page2 = _make_page(
            page_number=2,
            text=(
                "Section 09 90 00 - Painting and Coating\n"
                "This section covers interior and exterior painting. "
                "All surfaces shall be primed before applying finish coats."
            ),
            headings=[
                {"text": "Section 09 90 00 - Painting and Coating", "level": 1, "font_size": 16.0}
            ],
        )

        chunks = chunk_document([page1, page2])

        # Collect CSI sections from non-heading text chunks
        text_chunks = [c for c in chunks if c.chunk_type == "text"]
        assert len(text_chunks) >= 2, (
            f"Expected at least 2 text chunks (one per CSI section), got {len(text_chunks)}"
        )

        # Verify no single text chunk contains content from both CSI sections.
        for chunk in text_chunks:
            has_concrete = "concrete" in chunk.content.lower()
            has_painting = "painting" in chunk.content.lower()
            assert not (has_concrete and has_painting), (
                "A single chunk should not contain content from both CSI sections"
            )

    def test_chunk_size_within_limits(self):
        """All chunks should have token_count within the configured limit plus buffer."""
        max_tokens = 512
        # The overlap can push actual chunk size slightly above max_tokens
        # when the overlap itself is large, but content should not exceed max_tokens.
        buffer = 100  # Allow some buffer for overlap edge cases

        # Create a page with a long document to force multiple chunks.
        long_text = (
            "Section 03 30 00 - Cast-in-Place Concrete. "
            "The concrete mix design shall achieve a minimum compressive "
            "strength of 4,000 psi at 28 days. "
        ) * 100  # Repeat to create substantial text

        pages = [_make_page(page_number=1, text=long_text)]
        chunks = chunk_document(pages, max_tokens=max_tokens, overlap_tokens=50)

        assert len(chunks) > 1, "Long document should produce multiple chunks"

        for i, chunk in enumerate(chunks):
            actual_tokens = count_tokens(chunk.content)
            assert actual_tokens <= max_tokens + buffer, (
                f"Chunk {i} has {actual_tokens} tokens, exceeding limit of "
                f"{max_tokens} + {buffer} buffer"
            )
            assert chunk.token_count > 0, f"Chunk {i} should have a positive token_count"

    def test_chunk_metadata_includes_section_hierarchy(self):
        """Each chunk should carry section_hierarchy metadata from headings."""
        pages = [
            _make_page(
                page_number=1,
                text=(
                    "Section 03 30 00 - Cast-in-Place Concrete\n"
                    "PART 1 - GENERAL\n"
                    "This section includes cast-in-place concrete."
                ),
                headings=[
                    {
                        "text": "Section 03 30 00 - Cast-in-Place Concrete",
                        "level": 1,
                        "font_size": 16.0,
                    },
                    {
                        "text": "PART 1 - GENERAL",
                        "level": 2,
                        "font_size": 14.0,
                    },
                ],
            )
        ]

        chunks = chunk_document(pages)
        assert len(chunks) >= 1, "Should produce at least one chunk"

        # At least one chunk should have section hierarchy populated.
        chunks_with_hierarchy = [c for c in chunks if c.section_hierarchy]
        assert len(chunks_with_hierarchy) >= 1, (
            "At least one chunk should have non-empty section_hierarchy"
        )

        # Heading chunks should carry the hierarchy they belong to.
        heading_chunks = [c for c in chunks if c.chunk_type == "heading"]
        for hc in heading_chunks:
            assert isinstance(hc.section_hierarchy, list), "section_hierarchy should be a list"

    def test_table_chunks_preserved_as_unit(self):
        """Tables should be emitted as single chunks and not split across multiple chunks."""
        table_data = [
            ["Material", "Specification", "Quantity"],
            ["Portland Cement", "ASTM C150", "500 tons"],
            ["Aggregate", "ASTM C33", "1200 tons"],
            ["Sand", "ASTM C33", "800 tons"],
        ]

        pages = [
            _make_page(
                page_number=1,
                text="Section 03 30 00 - Materials and quantities listed below.",
                tables=[table_data],
                headings=[],
            )
        ]

        chunks = chunk_document(pages)

        # Find table chunks
        table_chunks = [c for c in chunks if c.chunk_type == "table"]
        assert len(table_chunks) >= 1, "Expected at least one table chunk"

        # Verify the table chunk contains all rows.
        table_chunk = table_chunks[0]
        assert "Portland Cement" in table_chunk.content, (
            "Table chunk should contain 'Portland Cement'"
        )
        assert "Aggregate" in table_chunk.content, "Table chunk should contain 'Aggregate'"
        assert "Sand" in table_chunk.content, "Table chunk should contain 'Sand'"

    def test_empty_document_produces_no_chunks(self):
        """Empty pages should produce zero chunks."""
        # Pages with no text, no tables, no headings
        pages = [
            _make_page(page_number=1, text=""),
            _make_page(page_number=2, text="   "),  # Whitespace only
        ]

        chunks = chunk_document(pages)
        assert len(chunks) == 0, f"Empty document should produce 0 chunks, got {len(chunks)}"
