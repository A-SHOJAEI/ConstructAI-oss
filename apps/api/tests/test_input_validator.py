"""Tests for the input validator (XSS / SQL pattern detection).

[security] InputValidator is a defense-in-depth gate. SQL injection
patterns are LOGGED (not blocked) because the app uses parameterized
queries everywhere — blocking single-quotes and semicolons rejected
legitimate construction-domain input like ``8" pipe`` or ``O'Brien
Construction``. XSS patterns ARE blocked since HTML injection is
possible regardless of parameterized queries.

These tests pin the documented behavior:
1. Construction-domain text (measurements, names with quotes/dashes)
   passes through.
2. XSS attempts are rejected.
3. The strict search-query path rejects SQL injection keywords.
"""

from __future__ import annotations

import pytest

from app.services.security.input_validator import InputValidator


@pytest.fixture
def validator() -> InputValidator:
    return InputValidator()


# =========================================================================
# validate_text_input — happy path / construction domain
# =========================================================================


def test_empty_text_is_safe(validator: InputValidator):
    assert validator.validate_text_input("") == (True, "")


def test_plain_text_passes_through(validator: InputValidator):
    safe, out = validator.validate_text_input("install rebar before pour")
    assert safe is True
    assert "install rebar" in out


def test_measurement_with_quotes_allowed(validator: InputValidator):
    """[M-10] An 8" pipe is a real construction term — must NOT be
    rejected as a SQL injection attempt."""
    safe, out = validator.validate_text_input('8" PVC pipe needed for sewage')
    assert safe is True
    assert '8"' in out


def test_apostrophe_in_name_allowed(validator: InputValidator):
    """[M-10] O'Brien Construction is a perfectly normal contractor
    name — single quotes must pass."""
    safe, out = validator.validate_text_input("Subcontractor: O'Brien Construction")
    assert safe is True
    assert "O'Brien" in out


def test_semicolon_in_text_allowed(validator: InputValidator):
    """Semicolons appear in addresses, lists, etc."""
    safe, _out = validator.validate_text_input("Site address: 123 Main St; Suite 4B")
    assert safe is True


def test_fraction_measurement_allowed(validator: InputValidator):
    """3/4" plywood is a real construction spec."""
    safe, _out = validator.validate_text_input('3/4" CDX plywood, 4x8 sheets')
    assert safe is True


# =========================================================================
# validate_text_input — SQL patterns LOGGED but not blocked
# =========================================================================


def test_sql_or_1_eq_1_logged_but_allowed(validator: InputValidator, caplog):
    """[M-10 documented behavior] OR 1=1 is suspicious but not blocked
    — parameterized queries make it harmless. Must log warning."""
    import logging

    with caplog.at_level(logging.WARNING):
        safe, _ = validator.validate_text_input("note: client demanded OR 1=1 clause")
    assert safe is True  # NOT blocked
    assert any("Suspicious SQL pattern" in r.message for r in caplog.records)


def test_sql_drop_table_logged_but_allowed(validator: InputValidator, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        safe, _ = validator.validate_text_input("DROP TABLE users")
    assert safe is True
    assert any("Suspicious SQL pattern" in r.message for r in caplog.records)


# =========================================================================
# validate_text_input — XSS BLOCKED
# =========================================================================


def test_script_tag_blocked(validator: InputValidator):
    """[security] <script> tags must be rejected — HTML injection
    bypasses parameterized queries."""
    safe, out = validator.validate_text_input("<script>alert('xss')</script>")
    assert safe is False
    assert out == ""


def test_javascript_url_blocked(validator: InputValidator):
    safe, _out = validator.validate_text_input("javascript:alert(1)")
    assert safe is False


def test_onclick_handler_blocked(validator: InputValidator):
    safe, _ = validator.validate_text_input('<a onclick="evil()">x</a>')
    assert safe is False


def test_onerror_handler_blocked(validator: InputValidator):
    safe, _ = validator.validate_text_input('<img src=x onerror="alert(1)">')
    assert safe is False


def test_onload_handler_blocked(validator: InputValidator):
    safe, _ = validator.validate_text_input('<body onload="evil()">')
    assert safe is False


def test_xss_with_uppercase_blocked(validator: InputValidator):
    """Patterns are case-insensitive — SCRIPT must also block."""
    safe, _ = validator.validate_text_input("<SCRIPT>alert(1)</SCRIPT>")
    assert safe is False


# =========================================================================
# sanitize_html
# =========================================================================


def test_sanitize_html_strips_tags(validator: InputValidator):
    out = validator.sanitize_html("<p>hello <b>world</b></p>")
    assert out == "hello world"


def test_sanitize_html_no_tags_unchanged(validator: InputValidator):
    out = validator.sanitize_html("plain text")
    assert out == "plain text"


def test_sanitize_html_strips_self_closing(validator: InputValidator):
    out = validator.sanitize_html("<br/>line<hr/>break")
    assert out == "linebreak"


# =========================================================================
# validate_search_query — stricter rules
# =========================================================================


def test_search_empty_is_safe(validator: InputValidator):
    assert validator.validate_search_query("") == (True, "")


def test_search_normal_query_allowed(validator: InputValidator):
    safe, _out = validator.validate_search_query("concrete foundation pour")
    assert safe is True


def test_search_blocks_union_select(validator: InputValidator):
    """[security] UNION SELECT is the canonical SQL injection
    technique — search queries must reject it."""
    safe, out = validator.validate_search_query("foo UNION SELECT password FROM users")
    assert safe is False
    assert out == ""


def test_search_blocks_drop_table(validator: InputValidator):
    safe, _ = validator.validate_search_query("DROP TABLE users")
    assert safe is False


def test_search_blocks_or_1_eq_1(validator: InputValidator):
    """In SEARCH context (stricter than text), OR 1=1 IS blocked."""
    safe, _ = validator.validate_search_query("admin OR 1=1")
    assert safe is False


def test_search_blocks_sql_comment(validator: InputValidator):
    """SQL comments (-- and /* */) often used for injection
    payloads — search query must reject them."""
    safe, _ = validator.validate_search_query("admin'-- comment")
    assert safe is False


def test_search_blocks_block_comment(validator: InputValidator):
    safe, _ = validator.validate_search_query("foo /* injected */ bar")
    assert safe is False


def test_search_blocks_xss(validator: InputValidator):
    safe, _ = validator.validate_search_query("<script>alert(1)</script>")
    assert safe is False


def test_search_strips_html_tags(validator: InputValidator):
    """Non-script HTML tags (e.g. unintended pasted markup) are
    stripped, but the query is still considered safe."""
    safe, out = validator.validate_search_query("<b>concrete</b> pour")
    assert safe is True
    assert out == "concrete pour"


# =========================================================================
# Pattern catalog invariants
# =========================================================================


def test_documented_xss_patterns_present():
    """Pin the documented XSS pattern catalog so a refactor can't
    quietly drop one of the canonical attack vectors."""
    patterns = " ".join(InputValidator.XSS_PATTERNS).lower()
    assert "script" in patterns
    assert "javascript" in patterns


def test_documented_sql_patterns_present():
    patterns = " ".join(InputValidator.SQL_INJECTION_PATTERNS).upper()
    assert "UNION" in patterns
    assert "DROP" in patterns
