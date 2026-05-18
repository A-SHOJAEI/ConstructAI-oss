"""Tests for drawing service filename parsing + discipline inference.

Pin DISCIPLINE_MAP (10 industry-standard discipline letters), the
sheet-number regex normalization (e.g., 'A101' -> 'A-101'), and
the documented 500MB drawing file size cap.
"""

from __future__ import annotations

from app.services.communication.drawing_service import (
    ALLOWED_DRAWING_EXTENSIONS,
    CONTENT_TYPE_MAP,
    DISCIPLINE_MAP,
    MAX_DRAWING_FILE_SIZE_BYTES,
    infer_discipline,
    parse_sheet_number,
)

# =========================================================================
# Constants
# =========================================================================


def test_allowed_extensions_canonical():
    """[contract] Only PDF/DWG/DXF accepted. Pin: refactor must NOT
    add new extensions silently (security — DWG/DXF need parser)."""
    assert {".pdf", ".dwg", ".dxf"} == ALLOWED_DRAWING_EXTENSIONS


def test_max_file_size_500mb():
    """[contract] 500MB max for drawings. Pin: refactor must NOT
    raise without explicit storage/quota review."""
    assert MAX_DRAWING_FILE_SIZE_BYTES == 500 * 1024 * 1024


def test_discipline_map_canonical_10_letters():
    """[contract] Pin all 10 industry-standard discipline prefix
    letters. Refactor must NOT add a new discipline silently —
    affects sheet-set organization in UI."""
    expected = {
        "A": "architectural",
        "S": "structural",
        "M": "mechanical",
        "E": "electrical",
        "P": "plumbing",
        "C": "civil",
        "L": "landscape",
        "G": "general",
        "F": "fire_protection",
        "T": "telecom",
    }
    assert expected == DISCIPLINE_MAP


def test_content_type_map_canonical():
    """[contract] Content-Type for each extension. Pin: refactor must
    NOT change MIME types — affects browser download behavior."""
    assert CONTENT_TYPE_MAP == {
        ".pdf": "application/pdf",
        ".dwg": "application/acad",
        ".dxf": "application/dxf",
    }


def test_content_type_map_keys_match_allowed_extensions():
    """[invariant] Every allowed extension must have a content-type
    mapping (avoid defaults that break downloads)."""
    assert set(CONTENT_TYPE_MAP.keys()) == ALLOWED_DRAWING_EXTENSIONS


# =========================================================================
# parse_sheet_number — regex normalization
# =========================================================================


def test_parse_sheet_dash_separator():
    """'A-101 Floor Plan.pdf' -> 'A-101' (canonical form preserved)."""
    assert parse_sheet_number("A-101 Floor Plan.pdf") == "A-101"


def test_parse_sheet_underscore_normalized_to_dash():
    """[contract] Underscore -> dash separator (canonical form).
    Pin: 'M_301' -> 'M-301'."""
    assert parse_sheet_number("M_301 HVAC Plan.dxf") == "M-301"


def test_parse_sheet_no_separator_inserts_dash():
    """[contract] 'S200' -> 'S-200' (insert separator)."""
    assert parse_sheet_number("S200_Foundation.dwg") == "S-200"


def test_parse_sheet_decimal_subsheet():
    """[contract] 'E-1.1' allows 1-2 decimals (sub-sheets)."""
    assert parse_sheet_number("E-1.1 Power Riser.pdf") == "E-1.1"


def test_parse_sheet_lowercase_letter_normalized_uppercase():
    """[contract] 'a-101' -> 'A-101' (case-insensitive parse,
    uppercase output)."""
    assert parse_sheet_number("a-101.pdf") == "A-101"


def test_parse_sheet_no_match_returns_none():
    """No discipline-prefix pattern -> None (not an empty string)."""
    assert parse_sheet_number("notes.pdf") is None
    assert parse_sheet_number("123-456.pdf") is None


def test_parse_sheet_strips_leading_underscore():
    """[robustness] Filename with leading underscore -> strips before regex."""
    assert parse_sheet_number("_A-101.pdf") == "A-101"


def test_parse_sheet_long_number():
    """4-digit sheet numbers supported (e.g., A-1000)."""
    assert parse_sheet_number("A-1000 Title.pdf") == "A-1000"


def test_parse_sheet_extension_stripped():
    """[contract] Multiple extensions don't break parsing."""
    assert parse_sheet_number("A-101.pdf") == "A-101"
    assert parse_sheet_number("S-200.dwg") == "S-200"


# =========================================================================
# infer_discipline
# =========================================================================


def test_infer_discipline_each_canonical_letter():
    """[contract] Every documented letter resolves to its
    canonical discipline name."""
    assert infer_discipline("A-101") == "architectural"
    assert infer_discipline("S-200") == "structural"
    assert infer_discipline("M-301") == "mechanical"
    assert infer_discipline("E-401") == "electrical"
    assert infer_discipline("P-501") == "plumbing"
    assert infer_discipline("C-601") == "civil"
    assert infer_discipline("L-701") == "landscape"
    assert infer_discipline("G-001") == "general"
    assert infer_discipline("F-801") == "fire_protection"
    assert infer_discipline("T-901") == "telecom"


def test_infer_discipline_unknown_letter_returns_general():
    """[fallback] Unknown discipline letter -> 'general' (not None,
    not raise — UI expects a string)."""
    assert infer_discipline("Z-101") == "general"
    assert infer_discipline("X-200") == "general"


def test_infer_discipline_lowercase_normalized():
    """[case-insensitive] Lowercase input still resolves."""
    assert infer_discipline("a-101") == "architectural"
    assert infer_discipline("s-200") == "structural"


def test_infer_discipline_empty_returns_general():
    """[edge case] Empty string -> 'general' default (no IndexError)."""
    assert infer_discipline("") == "general"


def test_infer_discipline_none_string_safe():
    """[contract] Sheet number with no leading letter still returns
    'general' (don't crash)."""
    # Anything starting with a non-letter character — hits the
    # 'general' fallback because the letter isn't in DISCIPLINE_MAP:
    assert infer_discipline("1-101") == "general"
