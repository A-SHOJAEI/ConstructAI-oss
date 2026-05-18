"""Phase 1: PDF parsing tests.

These tests verify that the PDF parser correctly extracts text, tables,
headings, and handles corrupted files using PyMuPDF and pdfplumber.
No external API calls are needed for these tests.
"""

from __future__ import annotations

import pytest

from app.services.ingestion.pdf_parser import ParseError, parse_pdf
from tests.fixtures.sample_documents import (
    create_corrupted_pdf,
    create_multi_page_pdf,
    create_sample_pdf,
)


class TestPdfParser:
    """Tests for the PDF parsing service."""

    def test_extract_text_from_digital_pdf(self):
        """PyMuPDF should extract text from a digitally-created PDF with high accuracy."""
        pdf_bytes = create_sample_pdf()
        result = parse_pdf(pdf_bytes)

        assert result.page_count >= 1
        assert len(result.pages) >= 1

        # Combine all page text into a single string for keyword checks.
        full_text = " ".join(page.text for page in result.pages)

        # Verify that key construction terms from the generated PDF are present.
        expected_keywords = [
            "Concrete",
            "ASTM",
            "Portland Cement",
            "compressive strength",
            "4,000 psi",  # or "4000 psi" depending on formatting
        ]
        for keyword in expected_keywords:
            # Case-insensitive check to account for formatting variations
            assert keyword.lower() in full_text.lower(), (
                f"Expected keyword '{keyword}' not found in extracted text"
            )

    def test_extract_tables_from_pdf(self):
        """pdfplumber should extract tables with correct column headers."""
        pdf_bytes = create_sample_pdf()
        result = parse_pdf(pdf_bytes)

        # The sample PDF contains a materials schedule table.
        all_tables = []
        for page in result.pages:
            all_tables.extend(page.tables)

        assert len(all_tables) >= 1, "Expected at least one table to be extracted"

        # Check that the table has the expected structure.
        # The first table should have header row with Material, Specification, Quantity.
        first_table = all_tables[0]
        assert len(first_table) >= 2, "Table should have at least a header and one data row"

        # Flatten all cells to check for expected content.
        flat_cells = [cell for row in first_table for cell in row if cell]
        flat_text = " ".join(flat_cells).lower()

        # Check for expected column headers or cell values.
        assert any(
            term in flat_text for term in ["material", "specification", "quantity", "portland"]
        ), f"Expected table content not found. Cells: {flat_cells}"

    def test_extract_headings_by_font_size(self):
        """Parser identifies headings based on font size differences."""
        pdf_bytes = create_sample_pdf()
        result = parse_pdf(pdf_bytes)

        # Collect all headings across pages.
        all_headings = []
        for page in result.pages:
            all_headings.extend(page.headings)

        assert len(all_headings) >= 1, "Expected at least one heading to be detected"

        # Each heading should have the required keys.
        for heading in all_headings:
            assert "text" in heading, "Heading must have 'text' key"
            assert "level" in heading, "Heading must have 'level' key"
            assert "font_size" in heading, "Heading must have 'font_size' key"
            assert heading["level"] >= 1, "Heading level must be >= 1"
            assert heading["font_size"] > 0, "Heading font_size must be positive"

        # The title should be detected as a heading (largest font).
        heading_texts = [h["text"].lower() for h in all_headings]
        assert any(
            "construction specification" in t or "concrete" in t or "materials" in t
            for t in heading_texts
        ), (
            f"Expected heading related to 'construction specification' or 'concrete'. "
            f"Found: {heading_texts}"
        )

    def test_handle_corrupted_pdf(self):
        """Corrupted PDF bytes should raise ParseError, not an unhandled crash."""
        corrupted_bytes = create_corrupted_pdf()

        with pytest.raises(ParseError):
            parse_pdf(corrupted_bytes)

    def test_extract_page_count(self):
        """Parser returns correct page count for multi-page PDFs."""
        num_pages = 3
        pdf_bytes = create_multi_page_pdf(num_pages=num_pages)
        result = parse_pdf(pdf_bytes)

        assert result.page_count == num_pages, (
            f"Expected {num_pages} pages, got {result.page_count}"
        )
        assert len(result.pages) == num_pages, (
            f"Expected {num_pages} ParsedPage objects, got {len(result.pages)}"
        )

        # Verify page numbers are sequential starting from 1.
        for i, page in enumerate(result.pages):
            assert page.page_number == i + 1, (
                f"Page {i} should have page_number={i + 1}, got {page.page_number}"
            )
