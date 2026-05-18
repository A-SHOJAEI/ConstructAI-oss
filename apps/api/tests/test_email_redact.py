"""Tests for the _redact_email helper.

Pin the privacy-preserving redaction format used in log output:
'a***@domain.com' so domain is preserved (for routing/debug) but
the local part is masked (PII).
"""

from __future__ import annotations

from app.services.email.service import _redact_email

# =========================================================================
# _redact_email — privacy-preserving redaction
# =========================================================================


def test_redact_normal_email():
    """[contract] 'alice@example.com' -> 'a***@example.com' (first
    char + *** + full domain)."""
    assert _redact_email("alice@example.com") == "a***@example.com"


def test_redact_short_local_part():
    """Single-char local part -> 'a***@domain'."""
    assert _redact_email("a@example.com") == "a***@example.com"


def test_redact_long_local_part():
    """Long local part -> still just first char + *** (no length leak)."""
    assert _redact_email("alongusername@example.com") == "a***@example.com"


def test_redact_uppercase_first_char():
    """Case preserved on first char."""
    assert _redact_email("Bob@example.com") == "B***@example.com"


def test_redact_subdomain():
    """Subdomain in domain -> preserved fully."""
    assert _redact_email("user@mail.example.com") == "u***@mail.example.com"


def test_redact_plus_addressing():
    """Plus addressing in local part -> first char only, plus and
    suffix masked."""
    assert _redact_email("user+tag@example.com") == "u***@example.com"


def test_redact_dotted_local_part():
    """Dot in local part -> only first char retained."""
    assert _redact_email("first.last@example.com") == "f***@example.com"


def test_redact_no_at_sign():
    """[edge case] String without @ -> partition treats it all as
    local with empty domain. Output: 'x***@'. Pin the documented
    behavior so logs still mark this as a redaction (not crash)."""
    out = _redact_email("not-an-email")
    # Empty local would yield "***", but "not-an-email" is non-empty
    # and treated as the local part:
    assert out == "n***@"


def test_redact_empty_string_returns_triple_star():
    """[edge case] Empty input -> '***' (no local, no domain).
    Pin so logs visibly indicate 'something was here but redacted'."""
    assert _redact_email("") == "***"


def test_redact_at_only():
    """[edge case] '@' alone -> empty local + empty domain ->
    '***' fallback (since local[0] would IndexError)."""
    out = _redact_email("@")
    # local is "", local[0] raises IndexError -> falls into else "***"
    assert out == "***"


def test_redact_starts_with_at():
    """[edge case] '@example.com' (no local part) -> '***'."""
    out = _redact_email("@example.com")
    # local is "" -> falsy -> "***"
    assert out == "***"


def test_redact_ends_with_at():
    """[edge case] 'user@' (no domain) -> 'u***@'."""
    assert _redact_email("user@") == "u***@"


def test_redact_double_at_uses_first_split():
    """[edge case] 'a@b@c' -> partition gives ('a', '@', 'b@c').
    Pin so a malformed double-@ input doesn't cause IndexError."""
    out = _redact_email("a@b@c")
    assert out == "a***@b@c"


def test_redact_does_not_leak_local_length():
    """[security] Output length is constant regardless of local
    part length. Pin so an attacker can't infer email length from
    log fragments."""
    short = _redact_email("a@x.com")
    long = _redact_email("aaaaaaaaaaaaaaaaa@x.com")
    # Both have format '<char>***@x.com':
    assert len(short) == len(long)


def test_redact_preserves_domain_for_routing():
    """[contract] Full domain preserved so logs can be filtered/
    routed by domain (e.g., 'all errors for @customer.com')."""
    assert _redact_email("user@customer.com") == "u***@customer.com"
    assert _redact_email("user@another.org") == "u***@another.org"
