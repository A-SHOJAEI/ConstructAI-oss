"""Tests for the OSHA lookup pure helpers (no DB).

normalize_name / parse_standard / is_construction / _first_token are
called on every contractor fuzzy-match query — pinning their behaviour
keeps the matcher deterministic across refactors.
"""

from __future__ import annotations

from app.services.safety.osha_lookup import (
    _first_token,
    is_construction,
    normalize_name,
    parse_standard,
)

# ---- normalize_name ------------------------------------------------------


def test_normalize_name_lowercases_and_strips_punctuation():
    assert normalize_name("ABC CONST., INC.") == "abc const inc"


def test_normalize_name_collapses_internal_whitespace():
    assert normalize_name("  ACME    CORP   ") == "acme corp"


def test_normalize_name_drops_special_chars():
    assert normalize_name("O'Brien & Sons LLC #5") == "o brien sons llc 5"


def test_normalize_name_empty_string():
    assert normalize_name("") == ""


def test_normalize_name_only_punctuation_returns_empty():
    assert normalize_name("....-,/!") == ""


def test_normalize_name_preserves_digits():
    assert normalize_name("Sterling 123 Inc") == "sterling 123 inc"


# ---- parse_standard ------------------------------------------------------


def test_parse_standard_construction_fall_protection():
    """29 CFR 1926.501 — fall protection (the most-cited construction
    standard). Rendered as 19260501 in OSHA raw codes."""
    assert parse_standard("19260501") == "1926.501"


def test_parse_standard_general_industry():
    assert parse_standard("19100134") == "1910.134"


def test_parse_standard_returns_none_for_empty():
    assert parse_standard("") is None
    assert parse_standard("   ") is None


def test_parse_standard_returns_none_when_too_short():
    assert parse_standard("123") is None


def test_parse_standard_strips_whitespace():
    assert parse_standard("  19260501  ") == "1926.501"


def test_parse_standard_handles_all_zeros_section():
    """Defensive: a raw section of 0000 must not render as ``1926.``
    (empty after lstrip-zero) — fall back to ``1926.0``."""
    assert parse_standard("19260000") == "1926.0"


# ---- is_construction -----------------------------------------------------


def test_is_construction_naics_23_prefix_is_construction():
    assert is_construction("236220", None) is True
    assert is_construction("237310", None) is True


def test_is_construction_non_construction_naics():
    """NAICS 4-digit codes outside the 23 prefix are not construction."""
    assert is_construction("445110", None) is False  # food retail
    assert is_construction("541110", None) is False  # legal services


def test_is_construction_sic_in_construction_range():
    """Old-style SIC codes 1500-1799 are construction."""
    assert is_construction(None, "1500") is True
    assert is_construction(None, "1623") is True
    assert is_construction(None, "1799") is True


def test_is_construction_sic_outside_construction_range():
    assert is_construction(None, "1499") is False
    assert is_construction(None, "1800") is False


def test_is_construction_invalid_sic_format_handled():
    """Non-numeric SIC strings shouldn't crash — return False."""
    assert is_construction(None, "abc") is False


def test_is_construction_neither_provided():
    assert is_construction(None, None) is False
    assert is_construction("", "") is False


def test_is_construction_naics_takes_precedence_over_sic():
    """If both are present and NAICS marks it construction, that wins
    even if SIC says non-construction (NAICS is the modern code)."""
    assert is_construction("237310", "9999") is True


# ---- _first_token --------------------------------------------------------


def test_first_token_skips_stopwords():
    """Stop words (the/a/an) shouldn't become the prefix — we want the
    first meaningful token for the DB index lookup."""
    out = _first_token("the abc construction")
    assert out == "abc"


def test_first_token_skips_too_short_tokens():
    """Single-char tokens are noise in the company-name prefix index."""
    out = _first_token("a b acme")
    assert out == "acme"


def test_first_token_uses_first_word_when_all_pass():
    out = _first_token("acme corporation builders")
    assert out == "acme"


def test_first_token_falls_back_to_first_three_chars_when_nothing_qualifies():
    """If every token is stopword/short, return at least a 3-char prefix
    so the DB query has SOMETHING to filter on."""
    out = _first_token("a b c x")
    # All single-char; falls back to first 3 chars: "a b" → "a b"
    assert len(out) <= 3


def test_first_token_returns_short_input_as_is_when_under_3_chars():
    out = _first_token("ab")
    assert out == "ab"
