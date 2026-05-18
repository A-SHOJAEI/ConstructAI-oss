"""PDF parser using PyMuPDF (fitz) for text/heading extraction and pdfplumber for tables."""

from __future__ import annotations

import io
import statistics
from dataclasses import dataclass, field

import fitz  # PyMuPDF
import pdfplumber
import structlog

logger = structlog.get_logger()

# SECURITY [H-13]: Enforce file size and page count limits to prevent
# denial-of-service via oversized PDF uploads.
MAX_PDF_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_PDF_PAGE_COUNT = 500

# SECURITY: Per-page text size limit to guard against decompression bombs
# where a small PDF expands to enormous text content.
MAX_PAGE_TEXT_SIZE = 1_048_576  # 1 MB per page


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ParseError(Exception):
    """Raised when a PDF cannot be parsed."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ParsedPage:
    """Structured representation of a single PDF page."""

    page_number: int
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)
    headings: list[dict] = field(default_factory=list)
    # Each heading dict: {"text": str, "level": int, "font_size": float}


@dataclass
class PdfParseResult:
    """Aggregated result for an entire PDF document."""

    pages: list[ParsedPage]
    page_count: int
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_headings(page: fitz.Page) -> list[dict]:
    """Identify headings by finding text spans whose font size exceeds the median.

    Returns a list of ``{"text": ..., "level": ..., "font_size": ...}`` dicts
    ordered by vertical position on the page.  Level 1 is the largest font size,
    level 2 the next largest, and so on.
    """
    try:
        page_dict = page.get_text("dict")
    except Exception:
        return []

    # Collect every non-empty text span with its font size.
    spans: list[dict] = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            # type 0 = text block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if text:
                    spans.append(
                        {
                            "text": text,
                            "font_size": span.get("size", 0.0),
                            "origin_y": span.get("origin", (0, 0))[1]
                            if isinstance(span.get("origin"), list | tuple)
                            else span.get("bbox", [0, 0, 0, 0])[1],
                        }
                    )

    if not spans:
        return []

    font_sizes = [s["font_size"] for s in spans]
    if len(set(font_sizes)) <= 1:
        # All text is the same size - no headings to detect.
        return []

    median_size = statistics.median(font_sizes)

    # Only spans whose font size is notably above the median are headings.
    heading_threshold = median_size * 1.15
    heading_spans = [s for s in spans if s["font_size"] >= heading_threshold]

    if not heading_spans:
        return []

    # Determine unique font sizes present in heading spans (descending) to
    # assign heading levels.
    unique_sizes = sorted({s["font_size"] for s in heading_spans}, reverse=True)
    size_to_level = {size: idx + 1 for idx, size in enumerate(unique_sizes)}

    headings: list[dict] = []
    for span in heading_spans:
        headings.append(
            {
                "text": span["text"],
                "level": size_to_level[span["font_size"]],
                "font_size": span["font_size"],
            }
        )

    return headings


def _extract_tables_pdfplumber_page(plumber_page, page_number: int) -> list[list[list[str]]]:
    """Extract tables from a pdfplumber page object.

    Returns a list of tables; each table is a list of rows; each row is a list
    of cell strings.

    SECURITY [H-13]: Accepts a pre-opened pdfplumber page to avoid re-opening
    the PDF from bytes for every page (O(n^2) fix).
    """
    tables: list[list[list[str]]] = []
    try:
        raw_tables = plumber_page.extract_tables()
        for raw_table in raw_tables:
            cleaned_table: list[list[str]] = []
            for row in raw_table:
                cleaned_row = [cell if cell is not None else "" for cell in row]
                cleaned_table.append(cleaned_row)
            tables.append(cleaned_table)
    except Exception as exc:
        logger.warning(
            "pdfplumber_table_extraction_failed",
            page_number=page_number,
            error=str(exc),
        )
    return tables


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_pdf(file_bytes: bytes) -> PdfParseResult:
    """Parse a PDF from raw bytes and return structured content.

    Raises ``ParseError`` if the PDF is corrupted or unreadable.
    Raises ``ValueError`` if the file exceeds size or page count limits.
    """
    # SECURITY [H-13]: Enforce file size limit before any parsing.
    if len(file_bytes) > MAX_PDF_FILE_SIZE:
        raise ValueError(
            f"PDF file size ({len(file_bytes):,} bytes) exceeds the "
            f"{MAX_PDF_FILE_SIZE:,}-byte limit"
        )

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        raise ParseError(f"Failed to open PDF: {exc}") from exc

    # SECURITY [H-13]: Enforce page count limit after opening.
    if len(doc) > MAX_PDF_PAGE_COUNT:
        doc.close()
        raise ValueError(f"PDF page count ({len(doc)}) exceeds the {MAX_PDF_PAGE_COUNT}-page limit")

    # SECURITY [L-11]: Sanitize PDF metadata — strip control characters,
    # limit length, and ensure only string values are kept to prevent
    # injection or log-poisoning attacks from crafted PDF metadata fields.
    _META_MAX_LEN = 1024

    def _sanitize_meta_value(val: str) -> str:
        """Strip control characters and limit length of a metadata string."""
        import re as _re

        # Remove ASCII control characters (except common whitespace)
        sanitized = _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(val))
        return sanitized.strip()[:_META_MAX_LEN]

    metadata: dict = {}
    try:
        raw_meta = doc.metadata
        if raw_meta:
            metadata = {
                k: _sanitize_meta_value(v) for k, v in raw_meta.items() if v and isinstance(v, str)
            }
    except Exception:
        pass

    pages: list[ParsedPage] = []

    # SECURITY [H-13]: Open pdfplumber once for the entire document instead
    # of re-opening from bytes for every page (O(n^2) fix).
    plumber_pdf = None
    try:
        plumber_pdf = pdfplumber.open(io.BytesIO(file_bytes))
    except Exception as exc:
        logger.warning("pdfplumber_open_failed", error=str(exc))

    try:
        for page_idx in range(len(doc)):
            page_number = page_idx + 1
            page = doc.load_page(page_idx)

            # --- text extraction ---
            try:
                text = page.get_text()
            except Exception as exc:
                raise ParseError(f"Failed to extract text from page {page_number}: {exc}") from exc

            # SECURITY: Guard against decompression bombs where a small PDF
            # page decompresses to an enormous amount of text.
            if len(text) > MAX_PAGE_TEXT_SIZE:
                logger.warning(
                    "page_text_truncated",
                    page_number=page_number,
                    original_size=len(text),
                    max_size=MAX_PAGE_TEXT_SIZE,
                )
                text = text[:MAX_PAGE_TEXT_SIZE]

            # --- heading detection ---
            headings = _extract_headings(page)

            # --- table extraction (via pdfplumber, opened once) ---
            tables: list[list[list[str]]] = []
            if plumber_pdf and page_number <= len(plumber_pdf.pages):
                tables = _extract_tables_pdfplumber_page(
                    plumber_pdf.pages[page_number - 1], page_number
                )

            pages.append(
                ParsedPage(
                    page_number=page_number,
                    text=text,
                    tables=tables,
                    headings=headings,
                )
            )
    finally:
        if plumber_pdf:
            plumber_pdf.close()
        doc.close()

    result = PdfParseResult(
        pages=pages,
        page_count=len(pages),
        metadata=metadata,
    )
    logger.info(
        "pdf_parsed",
        page_count=result.page_count,
        metadata_keys=list(metadata.keys()),
    )
    return result
