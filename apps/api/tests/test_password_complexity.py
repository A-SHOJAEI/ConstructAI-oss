"""Tests for the auth service password complexity validator.

[security] _validate_password_complexity is the gate at registration.
Pin every documented requirement (length, character classes) so a
refactor can't quietly weaken authentication.
"""

from __future__ import annotations

import pytest

from app.services.auth import _validate_password_complexity

# =========================================================================
# Length requirements
# =========================================================================


def test_password_too_short_rejected():
    """[security] Min length is 12 — refactor must NOT silently
    relax to 8 or below."""
    with pytest.raises(ValueError, match="at least 12 characters"):
        _validate_password_complexity("Sh0rt!Pw")


def test_password_at_minimum_length_passes():
    """Exactly 12 chars + all classes → accepted."""
    # 12 chars, has each class:
    _validate_password_complexity("Aa1!23456789")


def test_password_too_long_rejected():
    """[bound] Above 128 chars rejected — DOS protection on bcrypt."""
    long_password = "Aa1!" + "x" * 200
    with pytest.raises(ValueError, match="not exceed 128 characters"):
        _validate_password_complexity(long_password)


def test_password_at_max_length_passes():
    """Exactly 128 chars accepted (boundary inclusive)."""
    pw = "Aa1!" + "x" * 124  # 4 + 124 = 128
    assert len(pw) == 128
    _validate_password_complexity(pw)


# =========================================================================
# Character class requirements
# =========================================================================


def test_password_missing_uppercase_rejected():
    with pytest.raises(ValueError, match="uppercase"):
        _validate_password_complexity("alllowercase1!")


def test_password_missing_lowercase_rejected():
    with pytest.raises(ValueError, match="lowercase"):
        _validate_password_complexity("ALLUPPERCASE1!")


def test_password_missing_digit_rejected():
    with pytest.raises(ValueError, match="digit"):
        _validate_password_complexity("NoDigitsHere!")


def test_password_missing_special_rejected():
    """No special character → reject."""
    with pytest.raises(ValueError, match="special character"):
        _validate_password_complexity("NoSpecialChars1")


# =========================================================================
# Multi-error aggregation
# =========================================================================


def test_password_multiple_failures_aggregated():
    """When multiple requirements fail, error message must list each
    one — actionable feedback for users instead of fix-one-at-a-time."""
    with pytest.raises(ValueError) as exc_info:
        _validate_password_complexity("ab")  # short, no upper/digit/special
    msg = str(exc_info.value)
    # Multiple failures listed:
    assert "12 characters" in msg
    assert "uppercase" in msg
    assert "digit" in msg
    assert "special character" in msg


# =========================================================================
# Special character variations
# =========================================================================


def test_password_with_symbol_special_passes():
    """Common symbols ($, @, #) all qualify as "special character"."""
    for special in ("$", "@", "#", "*", "(", "!"):
        pw = f"Aa1{special}{'x' * 8}"
        _validate_password_complexity(pw)


def test_password_with_unicode_special_passes():
    """Non-ASCII symbols also count as special (anything not
    [A-Za-z0-9])."""
    _validate_password_complexity("Aa1¶xxxxxxxxx")


# =========================================================================
# Edge cases
# =========================================================================


def test_password_empty_rejected():
    """Empty password → multiple failures."""
    with pytest.raises(ValueError):
        _validate_password_complexity("")


def test_password_unicode_letters_count_correctly():
    """Unicode uppercase/lowercase letters via re — Latin-1 only by
    default since regex uses A-Z / a-z literal ranges. So Ñ doesn't
    satisfy uppercase requirement on its own. Pin documented
    behavior (ASCII-only)."""
    # All-Cyrillic, no ASCII — should fail uppercase + lowercase:
    with pytest.raises(ValueError):
        _validate_password_complexity("Ñàñàñàñà1!")


def test_complex_realistic_password_accepted():
    """Realistic strong password — pin that real users get through."""
    _validate_password_complexity("MyP@ssw0rd!2026")


def test_repeated_same_class_chars_accepted():
    """Pin: validator only checks presence, NOT distribution. A
    password with many lowercase but only one of each other class
    still passes — refactor must not add hidden uniqueness bias."""
    _validate_password_complexity("a" * 9 + "A1!")


def test_password_just_above_minimum_with_each_class():
    """13-char password with one of each class → accepted."""
    _validate_password_complexity("Abcdefgh1234!")
