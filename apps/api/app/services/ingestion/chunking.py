"""CSI-aware document chunking for construction documents.

Splits parsed PDF pages into semantically meaningful chunks while respecting
CSI (Construction Specifications Institute) division boundaries, keeping
tables intact, and tracking section hierarchy.

Includes a SpecificationChunker for CSI MasterFormat specification documents
that chunks by Part/subsection boundaries with structured metadata and
cross-reference extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog
import tiktoken

from app.services.ingestion.pdf_parser import ParsedPage

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

_encoding: tiktoken.Encoding | None = None


def _get_encoding() -> tiktoken.Encoding:
    global _encoding
    if _encoding is None:
        _encoding = tiktoken.get_encoding("cl100k_base")
    return _encoding


def count_tokens(text: str) -> int:
    """Return the number of tokens in *text* using the cl100k_base encoding."""
    return len(_get_encoding().encode(text))


# ---------------------------------------------------------------------------
# CSI detection
# ---------------------------------------------------------------------------

# Matches patterns like "Section 03 30 00", "03 30 00", "Division 03", etc.
_CSI_SECTION_RE = re.compile(
    r"""
    (?:Section\s+)?          # Optional "Section " prefix
    (\d{2})\s+               # Division (2 digits)
    (\d{2})\s+               # Level-2
    (\d{2})                  # Level-3
    """,
    re.IGNORECASE | re.VERBOSE,
)

_CSI_DIVISION_RE = re.compile(
    r"Division\s+(\d{1,2})\b",
    re.IGNORECASE,
)


def _detect_csi_section(text: str) -> str | None:
    """Return the first CSI section code found in *text*, or ``None``."""
    m = _CSI_SECTION_RE.search(text)
    if m:
        return f"{m.group(1)} {m.group(2)} {m.group(3)}"
    m = _CSI_DIVISION_RE.search(text)
    if m:
        division = m.group(1).zfill(2)
        return f"{division} 00 00"
    return None


# ---------------------------------------------------------------------------
# CSI MasterFormat title lookup
# ---------------------------------------------------------------------------

_CSI_TITLES: dict[str, str] = {
    "01": "General Requirements",
    "02": "Existing Conditions",
    "03": "Concrete",
    "04": "Masonry",
    "05": "Metals",
    "06": "Wood, Plastics, and Composites",
    "07": "Thermal and Moisture Protection",
    "08": "Openings",
    "09": "Finishes",
    "10": "Specialties",
    "11": "Equipment",
    "12": "Furnishings",
    "13": "Special Construction",
    "14": "Conveying Equipment",
    "21": "Fire Suppression",
    "22": "Plumbing",
    "23": "HVAC",
    "25": "Integrated Automation",
    "26": "Electrical",
    "27": "Communications",
    "28": "Electronic Safety and Security",
    "31": "Earthwork",
    "32": "Exterior Improvements",
    "33": "Utilities",
    "34": "Transportation",
    "35": "Waterway and Marine Construction",
    "40": "Process Integration",
    "41": "Material Processing and Handling",
    "42": "Process Heating, Cooling, and Drying",
    "43": "Process Gas and Liquid Handling",
    "44": "Pollution and Waste Control Equipment",
    "46": "Water and Wastewater Equipment",
    "48": "Electrical Power Generation",
}


def _csi_title_for_section(csi_section: str) -> str:
    """Return a human-readable title for a CSI section code."""
    division = csi_section[:2]
    return _CSI_TITLES.get(division, f"Division {division}")


# ---------------------------------------------------------------------------
# Chunk data class
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    """A single document chunk with rich metadata."""

    content: str
    chunk_type: str  # "text" | "table" | "heading" | "list" | "spec_section"
    page_number: int | None = None
    section_hierarchy: list[str] = field(default_factory=list)
    csi_section: str | None = None
    token_count: int = 0
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_text_into_chunks(
    text: str,
    *,
    max_tokens: int,
    overlap_tokens: int,
    page_number: int | None,
    section_hierarchy: list[str],
    csi_section: str | None,
) -> list[Chunk]:
    """Split a block of text into token-limited chunks with overlap."""
    enc = _get_encoding()
    tokens = enc.encode(text)

    if not tokens:
        return []

    chunks: list[Chunk] = []
    start = 0

    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_text = enc.decode(chunk_tokens)

        chunks.append(
            Chunk(
                content=chunk_text,
                chunk_type="text",
                page_number=page_number,
                section_hierarchy=list(section_hierarchy),
                csi_section=csi_section,
                token_count=len(chunk_tokens),
            )
        )

        if end >= len(tokens):
            break

        # Advance with overlap.
        start = end - overlap_tokens

    return chunks


def _table_to_text(table: list[list[str]]) -> str:
    """Render a table as a pipe-delimited text block suitable for embedding."""
    rows: list[str] = []
    for row in table:
        rows.append("| " + " | ".join(row) + " |")
        # Add separator after header row.
        if len(rows) == 1:
            rows.append("| " + " | ".join("---" for _ in row) + " |")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chunk_document(
    pages: list[ParsedPage],
    *,
    max_tokens: int = 512,
    overlap_tokens: int = 50,
) -> list[Chunk]:
    """Split parsed PDF pages into semantically meaningful chunks.

    Rules
    -----
    * CSI division boundaries trigger a new chunk.
    * Tables are emitted as single chunks (never split across chunks).
    * Headings update the running section hierarchy.
    * Text blocks are split at *max_tokens* with *overlap_tokens* of context.

    Parameters
    ----------
    pages:
        Output from :func:`pdf_parser.parse_pdf`.
    max_tokens:
        Maximum tokens per chunk (default 512).
    overlap_tokens:
        Overlap between consecutive text chunks for context continuity.

    Returns
    -------
    list[Chunk]
        Ordered list of document chunks with metadata.
    """
    all_chunks: list[Chunk] = []
    section_hierarchy: list[str] = []
    current_csi: str | None = None
    pending_text: str = ""
    pending_page: int | None = None

    def _flush_pending() -> None:
        nonlocal pending_text, pending_page
        if not pending_text.strip():
            pending_text = ""
            return
        new_chunks = _split_text_into_chunks(
            pending_text.strip(),
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            page_number=pending_page,
            section_hierarchy=section_hierarchy,
            csi_section=current_csi,
        )
        all_chunks.extend(new_chunks)
        pending_text = ""

    for page in pages:
        pending_page = page.page_number

        # --- Process headings (update hierarchy, detect CSI) ---
        for heading in page.headings:
            heading_text = heading["text"]
            heading_level = heading["level"]

            # Check whether this heading introduces a new CSI section.
            detected_csi = _detect_csi_section(heading_text)
            if detected_csi and detected_csi != current_csi:
                # CSI boundary - flush the current pending text.
                _flush_pending()
                current_csi = detected_csi

            # Maintain section hierarchy: trim to current level and append.
            section_hierarchy = section_hierarchy[: heading_level - 1]
            section_hierarchy.append(heading_text)

            # Emit the heading itself as a small chunk so it is searchable.
            heading_token_count = count_tokens(heading_text)
            if heading_token_count > 0:
                all_chunks.append(
                    Chunk(
                        content=heading_text,
                        chunk_type="heading",
                        page_number=page.page_number,
                        section_hierarchy=list(section_hierarchy),
                        csi_section=current_csi,
                        token_count=heading_token_count,
                    )
                )

        # --- Process tables (keep each table as a single chunk) ---
        for table in page.tables:
            _flush_pending()
            table_text = _table_to_text(table)
            if len(table_text) > max_tokens * 8:  # ~2x max_tokens in chars
                logger.warning(
                    "large_table_detected",
                    table_chars=len(table_text),
                    max_tokens=max_tokens,
                    page=page.page_number,
                )
            token_count = count_tokens(table_text)
            all_chunks.append(
                Chunk(
                    content=table_text,
                    chunk_type="table",
                    page_number=page.page_number,
                    section_hierarchy=list(section_hierarchy),
                    csi_section=current_csi,
                    token_count=token_count,
                )
            )

        # --- Check body text for inline CSI references ---
        text = page.text
        if not text or not text.strip():
            continue

        inline_csi = _detect_csi_section(text)
        if inline_csi and inline_csi != current_csi:
            _flush_pending()
            current_csi = inline_csi

        # Accumulate body text.
        if pending_text:
            pending_text += "\n"
        pending_text += text

    # Flush any remaining text.
    _flush_pending()

    logger.info(
        "document_chunked",
        total_chunks=len(all_chunks),
        chunk_types={c.chunk_type for c in all_chunks},
    )
    return all_chunks


# ---------------------------------------------------------------------------
# Specification document detection
# ---------------------------------------------------------------------------

# Section header: "SECTION 03 30 00" or "03 30 00 CAST-IN-PLACE CONCRETE"
_SPEC_SECTION_HEADER_RE = re.compile(
    r"""
    (?:^|\n)\s*
    (?:SECTION\s+)?
    (\d{2})\s+(\d{2})\s+(\d{2})
    (?:\s*[-–—]\s*|\s+)
    ([A-Z][A-Z\s,/&\-]+?)
    \s*(?:\n|$)
    """,
    re.VERBOSE | re.MULTILINE,
)

# Alternate: title before number
_SPEC_SECTION_HEADER_ALT_RE = re.compile(
    r"""
    (?:^|\n)\s*
    ([A-Z][A-Z\s,/&\-]{3,}?)
    \s*[-–—]?\s*
    (?:SECTION\s+)?
    (\d{2})\s+(\d{2})\s+(\d{2})
    \s*(?:\n|$)
    """,
    re.VERBOSE | re.MULTILINE,
)

# Part headers: "PART 1 - GENERAL", "PART 2 - PRODUCTS", "PART 3 - EXECUTION"
_PART_HEADER_RE = re.compile(
    r"(?:^|\n)\s*PART\s+(\d+)\s*[-–—]\s*(GENERAL|PRODUCTS|EXECUTION|[A-Z\s]+?)\s*(?:\n|$)",
    re.IGNORECASE | re.MULTILINE,
)

# Subsection headers: "1.1", "2.3", "3.1 A."
_SUBSECTION_RE = re.compile(
    r"(?:^|\n)\s*(\d+\.\d+)\s+([A-Z][\w\s,/&\-]+)",
    re.MULTILINE,
)

# Drawing cross-references
_DRAWING_REF_RE = re.compile(
    r"""
    (?:See|Refer\s+to|per|as\s+shown\s+on|shown\s+on|refer)\s+
    (?:Detail\s+(\d+/[A-Z]-?\d+)|       # "Detail 5/A-301"
       Sheet\s+([A-Z][_-]?\d{1,4})|     # "Sheet M-401"
       Drawing\s+([A-Z][_-]?\d{1,4})|   # "Drawing S-101"
       (?:Dwg\.?\s+)?([A-Z][_-]?\d{1,4}))  # "Dwg A-201"
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Standalone drawing number references like "(A-301)" or "A-301"
_DRAWING_NUM_RE = re.compile(r"\b([A-Z][_-]?\d{1,4}(?:\.\d+)?)\b")

# Minimum score for a document to be classified as a specification
_SPEC_DETECTION_THRESHOLD = 3


def is_specification_document(full_text: str) -> bool:
    """Detect whether a document is a CSI MasterFormat specification.

    Looks for multiple structural indicators: section headers, part headers,
    subsection numbering, and spec-specific language.

    Parameters
    ----------
    full_text:
        Combined text of all pages.

    Returns
    -------
    bool
        True if the document appears to be a CSI specification.
    """
    score = 0

    # CSI section headers (strongest signal)
    if _SPEC_SECTION_HEADER_RE.search(full_text) or _SPEC_SECTION_HEADER_ALT_RE.search(full_text):
        score += 3

    # Part headers
    part_matches = _PART_HEADER_RE.findall(full_text)
    if part_matches:
        score += min(len(part_matches), 3)

    # Subsection numbering (1.1, 2.3, etc.)
    subsection_matches = _SUBSECTION_RE.findall(full_text)
    if len(subsection_matches) >= 3:
        score += 2

    # Spec-specific language
    spec_terms = [
        "RELATED SECTIONS",
        "REFERENCES",
        "SUBMITTALS",
        "QUALITY ASSURANCE",
        "DELIVERY, STORAGE",
        "MATERIALS",
        "EXECUTION",
        "PART 1",
        "PART 2",
        "PART 3",
        "PRODUCTS",
        "APPLICABLE PUBLICATIONS",
        "SCOPE OF WORK",
    ]
    term_hits = sum(1 for t in spec_terms if t in full_text.upper())
    if term_hits >= 3:
        score += 2

    return score >= _SPEC_DETECTION_THRESHOLD


def extract_drawing_references(text: str) -> list[dict]:
    """Extract structured drawing cross-references from text.

    Finds patterns like:
    - "See Detail 5/A-301"
    - "Refer to Sheet M-401"
    - "as shown on Drawing S-101"

    Returns
    -------
    list[dict]
        Each dict has: reference_type, reference_id, raw_text
    """
    refs: list[dict] = []
    seen: set[str] = set()

    for m in _DRAWING_REF_RE.finditer(text):
        # One of the groups will match
        detail, sheet, drawing, dwg = m.groups()
        if detail:
            ref_id = detail
            ref_type = "detail"
        elif sheet:
            ref_id = _normalize_sheet_number(sheet)
            ref_type = "sheet"
        elif drawing:
            ref_id = _normalize_sheet_number(drawing)
            ref_type = "drawing"
        elif dwg:
            ref_id = _normalize_sheet_number(dwg)
            ref_type = "drawing"
        else:
            continue

        key = f"{ref_type}:{ref_id}"
        if key not in seen:
            seen.add(key)
            refs.append(
                {
                    "reference_type": ref_type,
                    "reference_id": ref_id,
                    "raw_text": m.group(0).strip(),
                }
            )

    return refs


def _normalize_sheet_number(raw: str) -> str:
    """Normalize sheet numbers like 'A301' -> 'A-301', 'M_401' -> 'M-401'."""
    m = re.match(r"([A-Z])[-_]?(\d+)", raw, re.IGNORECASE)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    return raw.upper()


# ---------------------------------------------------------------------------
# SpecificationChunker
# ---------------------------------------------------------------------------


@dataclass
class _SpecSection:
    """Internal structure for a detected specification section."""

    csi_section: str  # "03 30 00"
    csi_title: str  # "Cast-in-Place Concrete"
    part_number: int | None = None  # 1, 2, or 3
    part_name: str = ""  # "GENERAL", "PRODUCTS", "EXECUTION"
    subsection_number: str = ""  # "1.1", "2.3"
    subsection_title: str = ""
    content: str = ""
    page_number: int | None = None
    drawing_references: list[dict] = field(default_factory=list)


class SpecificationChunker:
    """Chunker specialized for CSI MasterFormat specification documents.

    Chunks at Part level (primary) and subsection level (secondary),
    preserving structured metadata for each chunk.

    Usage
    -----
    >>> chunker = SpecificationChunker(max_tokens=1024)
    >>> chunks = chunker.chunk(full_text, pages)
    """

    def __init__(
        self,
        *,
        max_tokens: int = 1024,
        overlap_tokens: int = 50,
    ):
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(
        self,
        full_text: str,
        pages: list[ParsedPage] | None = None,
    ) -> list[Chunk]:
        """Chunk a specification document with structured metadata.

        Parameters
        ----------
        full_text:
            Combined text of all pages.
        pages:
            Optional original parsed pages for page-number tracking.

        Returns
        -------
        list[Chunk]
            Chunks with metadata including csi_section, csi_title,
            part_number, subsection_number, and drawing_references.
        """
        # Step 1: Detect the CSI section for this spec
        csi_section, csi_title = self._detect_spec_section(full_text)

        # Step 2: Split by Part headers
        parts = self._split_by_parts(full_text, csi_section, csi_title)

        # Step 3: Split each Part by subsections
        spec_sections: list[_SpecSection] = []
        for part in parts:
            subsections = self._split_by_subsections(part)
            if subsections:
                spec_sections.extend(subsections)
            else:
                spec_sections.append(part)

        # Step 4: Convert to Chunks (with token limits)
        all_chunks: list[Chunk] = []
        for sec in spec_sections:
            sec.drawing_references = extract_drawing_references(sec.content)
            chunks = self._section_to_chunks(sec)
            all_chunks.extend(chunks)

        logger.info(
            "specification_chunked",
            csi_section=csi_section,
            csi_title=csi_title,
            total_chunks=len(all_chunks),
            parts_found=len(parts),
        )
        return all_chunks

    def _detect_spec_section(self, text: str) -> tuple[str, str]:
        """Detect the CSI section number and title from spec text."""
        # Try structured header first
        m = _SPEC_SECTION_HEADER_RE.search(text)
        if m:
            section = f"{m.group(1)} {m.group(2)} {m.group(3)}"
            title = m.group(4).strip().title()
            return section, title

        m = _SPEC_SECTION_HEADER_ALT_RE.search(text)
        if m:
            section = f"{m.group(2)} {m.group(3)} {m.group(4)}"
            title = m.group(1).strip().title()
            return section, title

        # Fall back to general CSI detection
        detected = _detect_csi_section(text)
        if detected:
            title = _csi_title_for_section(detected)
            return detected, title

        return "00 00 00", "Unknown Section"

    def _split_by_parts(
        self,
        text: str,
        csi_section: str,
        csi_title: str,
    ) -> list[_SpecSection]:
        """Split specification text into Part-level sections."""
        part_positions: list[tuple[int, int, str]] = []

        for m in _PART_HEADER_RE.finditer(text):
            part_num = int(m.group(1))
            part_name = m.group(2).strip().upper()
            part_positions.append((m.start(), part_num, part_name))

        if not part_positions:
            # No parts found — return whole document as one section
            return [
                _SpecSection(
                    csi_section=csi_section,
                    csi_title=csi_title,
                    content=text,
                )
            ]

        sections: list[_SpecSection] = []

        # If there's text before the first Part, include it as a preamble
        if part_positions[0][0] > 0:
            preamble = text[: part_positions[0][0]].strip()
            if preamble:
                sections.append(
                    _SpecSection(
                        csi_section=csi_section,
                        csi_title=csi_title,
                        part_number=0,
                        part_name="PREAMBLE",
                        content=preamble,
                    )
                )

        for i, (start, part_num, part_name) in enumerate(part_positions):
            end = part_positions[i + 1][0] if i + 1 < len(part_positions) else len(text)

            content = text[start:end].strip()
            sections.append(
                _SpecSection(
                    csi_section=csi_section,
                    csi_title=csi_title,
                    part_number=part_num,
                    part_name=part_name,
                    content=content,
                )
            )

        return sections

    def _split_by_subsections(
        self,
        part: _SpecSection,
    ) -> list[_SpecSection]:
        """Split a Part into subsection-level sections."""
        subsection_positions: list[tuple[int, str, str]] = []

        for m in _SUBSECTION_RE.finditer(part.content):
            sub_num = m.group(1)
            sub_title = m.group(2).strip()
            subsection_positions.append((m.start(), sub_num, sub_title))

        if not subsection_positions:
            return []

        sections: list[_SpecSection] = []

        for i, (start, sub_num, sub_title) in enumerate(subsection_positions):
            if i + 1 < len(subsection_positions):
                end = subsection_positions[i + 1][0]
            else:
                end = len(part.content)

            content = part.content[start:end].strip()
            sections.append(
                _SpecSection(
                    csi_section=part.csi_section,
                    csi_title=part.csi_title,
                    part_number=part.part_number,
                    part_name=part.part_name,
                    subsection_number=sub_num,
                    subsection_title=sub_title,
                    content=content,
                )
            )

        return sections

    def _section_to_chunks(self, sec: _SpecSection) -> list[Chunk]:
        """Convert a _SpecSection to one or more Chunks with token limits."""
        # Build the spec section path for metadata
        path_parts = [f"Section {sec.csi_section} - {sec.csi_title}"]
        if sec.part_number is not None and sec.part_number > 0:
            path_parts.append(f"Part {sec.part_number} - {sec.part_name}")
        if sec.subsection_number:
            path_parts.append(f"{sec.subsection_number} {sec.subsection_title}")
        spec_section_path = " > ".join(path_parts)

        # Build metadata dict
        chunk_metadata: dict[str, Any] = {
            "csi_section": sec.csi_section,
            "csi_title": sec.csi_title,
            "spec_section_path": spec_section_path,
        }
        if sec.part_number is not None:
            chunk_metadata["part_number"] = sec.part_number
        if sec.subsection_number:
            chunk_metadata["subsection_number"] = sec.subsection_number
        if sec.drawing_references:
            chunk_metadata["drawing_references"] = sec.drawing_references

        token_count = count_tokens(sec.content)

        if token_count <= self.max_tokens:
            return [
                Chunk(
                    content=sec.content,
                    chunk_type="spec_section",
                    section_hierarchy=path_parts,
                    csi_section=sec.csi_section,
                    token_count=token_count,
                    metadata=chunk_metadata,
                )
            ]

        # Split oversized sections
        sub_chunks = _split_text_into_chunks(
            sec.content,
            max_tokens=self.max_tokens,
            overlap_tokens=self.overlap_tokens,
            page_number=sec.page_number,
            section_hierarchy=path_parts,
            csi_section=sec.csi_section,
        )
        for c in sub_chunks:
            c.chunk_type = "spec_section"
            c.metadata = chunk_metadata
        return sub_chunks


# ---------------------------------------------------------------------------
# Smart chunking dispatcher (auto-detects spec documents)
# ---------------------------------------------------------------------------


MAX_PAGES = 2000


def chunk_document_smart(
    pages: list[ParsedPage],
    *,
    max_tokens: int = 512,
    overlap_tokens: int = 50,
    spec_max_tokens: int = 1024,
) -> list[Chunk]:
    """Auto-detect document type and use the appropriate chunker.

    If the document is detected as a CSI specification, uses
    SpecificationChunker for structured chunking. Otherwise falls back
    to the general chunk_document() function.

    Parameters
    ----------
    pages:
        Output from pdf_parser.parse_pdf().
    max_tokens:
        Max tokens for general documents.
    overlap_tokens:
        Token overlap for context continuity.
    spec_max_tokens:
        Max tokens per chunk for specification documents (larger because
        specs benefit from keeping subsections intact).

    Returns
    -------
    list[Chunk]
        Document chunks with appropriate metadata.
    """
    # Enforce page limit to prevent excessive memory usage
    if len(pages) > MAX_PAGES:
        logger.warning(
            "document_truncated",
            original_pages=len(pages),
            max_pages=MAX_PAGES,
        )
        pages = pages[:MAX_PAGES]

    # Combine all page text for detection
    full_text = "\n".join(page.text or "" for page in pages)

    if is_specification_document(full_text):
        logger.info("detected_specification_document", pages=len(pages))
        chunker = SpecificationChunker(
            max_tokens=spec_max_tokens,
            overlap_tokens=overlap_tokens,
        )
        return chunker.chunk(full_text, pages)

    # Fall back to general chunker
    return chunk_document(
        pages,
        max_tokens=max_tokens,
        overlap_tokens=overlap_tokens,
    )
