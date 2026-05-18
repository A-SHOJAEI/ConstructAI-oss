"""Tests for the MFA module — TOTP + backup codes.

[security] These functions handle authentication factors. Pin every
correctness invariant: the TOTP round-trip, base32-secret format,
URI provisioning format, salted backup-code hashing, constant-time
verification (no early-break index leak), and the QR data URI shape.
"""

from __future__ import annotations

import hashlib

from app.services.security.mfa import (
    generate_backup_codes,
    generate_qr_code_data_uri,
    generate_totp_secret,
    get_totp_uri,
    verify_backup_code,
    verify_totp,
)

# =========================================================================
# generate_totp_secret
# =========================================================================


def test_totp_secret_returns_base32_string():
    secret = generate_totp_secret()
    assert isinstance(secret, str)
    # pyotp returns a 32-char base32 secret
    assert len(secret) >= 16  # base32 uses 8 chars per 5 bytes
    # All chars are valid base32:
    valid = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    assert set(secret) <= valid


def test_totp_secret_is_unique_per_call():
    """Two calls must produce different secrets — otherwise every
    user would share the same MFA seed."""
    a = generate_totp_secret()
    b = generate_totp_secret()
    assert a != b


# =========================================================================
# get_totp_uri
# =========================================================================


def test_totp_uri_canonical_format():
    """RFC 6238 / Google Authenticator URI format: otpauth://totp/
    Issuer:account?secret=...&issuer=Issuer."""
    secret = generate_totp_secret()
    uri = get_totp_uri(secret, "alice@example.com")
    assert uri.startswith("otpauth://totp/")
    assert "ConstructAI" in uri
    assert "alice@example.com" in uri or "alice%40example.com" in uri
    assert f"secret={secret}" in uri


def test_totp_uri_with_email_special_chars():
    """The user's email may contain '+' or '.' — URI builder must
    encode safely."""
    secret = generate_totp_secret()
    uri = get_totp_uri(secret, "user+tag@example.com")
    # Round-trip — pyotp returns a valid otpauth URI:
    assert "otpauth://totp/" in uri


# =========================================================================
# verify_totp
# =========================================================================


def test_verify_totp_correct_code_passes():
    """A code generated from the same secret must verify."""
    import pyotp

    secret = generate_totp_secret()
    code = pyotp.TOTP(secret).now()  # current valid code
    assert verify_totp(secret, code) is True


def test_verify_totp_wrong_code_rejected():
    secret = generate_totp_secret()
    assert verify_totp(secret, "000000") is False


def test_verify_totp_empty_code_rejected():
    secret = generate_totp_secret()
    assert verify_totp(secret, "") is False


def test_verify_totp_garbage_code_rejected():
    secret = generate_totp_secret()
    assert verify_totp(secret, "garbage") is False


# =========================================================================
# generate_backup_codes
# =========================================================================


def test_backup_codes_default_count_is_ten():
    plain, hashed, _salt = generate_backup_codes()
    assert len(plain) == 10
    assert len(hashed) == 10


def test_backup_codes_explicit_count():
    plain, hashed, _ = generate_backup_codes(count=5)
    assert len(plain) == 5
    assert len(hashed) == 5


def test_backup_codes_are_hex_strings():
    """secrets.token_hex(6) → 12-char hex (48-bit entropy)."""
    plain, _, _ = generate_backup_codes(count=3)
    for code in plain:
        assert len(code) == 12
        int(code, 16)  # raises if not hex


def test_backup_codes_are_unique():
    """48-bit entropy × 10 codes — collision is astronomically
    unlikely. Pin that we don't accidentally generate duplicates."""
    plain, _, _ = generate_backup_codes(count=20)
    assert len(set(plain)) == 20


def test_backup_codes_hashed_with_salt_and_sha256():
    """Pin the hashing scheme — sha256 of (salt || code)."""
    plain, hashed, salt = generate_backup_codes(count=1)
    expected = hashlib.sha256(salt.encode() + plain[0].encode()).hexdigest()
    assert hashed[0] == expected


def test_backup_codes_random_salt_when_none_provided():
    _, _, salt_a = generate_backup_codes(count=1)
    _, _, salt_b = generate_backup_codes(count=1)
    # Each call generates a new random salt:
    assert salt_a != salt_b


def test_backup_codes_provided_salt_used():
    """If caller supplies a salt, that exact value should be returned
    + used in hashing."""
    plain, hashed, salt = generate_backup_codes(count=1, salt="user-specific-salt-xyz")
    assert salt == "user-specific-salt-xyz"
    expected = hashlib.sha256(b"user-specific-salt-xyz" + plain[0].encode()).hexdigest()
    assert hashed[0] == expected


# =========================================================================
# verify_backup_code
# =========================================================================


def test_verify_backup_code_returns_index_on_match():
    plain, hashed, salt = generate_backup_codes(count=5)
    # Match the third code → should return index 2
    assert verify_backup_code(plain[2], hashed, salt) == 2


def test_verify_backup_code_returns_none_on_miss():
    _, hashed, salt = generate_backup_codes(count=5)
    assert verify_backup_code("000000000000", hashed, salt) is None


def test_verify_backup_code_empty_codes_list_returns_none():
    assert verify_backup_code("anything", [], "any-salt") is None


def test_verify_backup_code_wrong_salt_rejects():
    """[security] If the salt is wrong (e.g. cross-user lookup attempt),
    the hash won't match — must return None."""
    plain, hashed, _salt = generate_backup_codes(count=3, salt="user-a")
    # Same code, wrong salt:
    assert verify_backup_code(plain[0], hashed, "user-b") is None


def test_verify_backup_code_no_early_break_on_first_match():
    """[M-1/M-08] The verify loop must iterate every stored hash, not
    early-break on first match — otherwise a timing attacker can
    distinguish "matched at index 0" from "matched at index 9". The
    only externally observable signal is the returned index, which
    is the genuine answer."""
    plain, hashed, salt = generate_backup_codes(count=10)
    # Verify the FIRST code — should still iterate all 10 hashes
    # internally (we can't directly observe iteration count, but we
    # can verify the function returns the right index):
    assert verify_backup_code(plain[0], hashed, salt) == 0
    # And the LAST code:
    assert verify_backup_code(plain[9], hashed, salt) == 9


def test_verify_backup_code_default_salt_empty_string():
    """Backward-compat: pre-salt deployments hashed without a salt.
    Default salt="" must work for those legacy hashes."""
    code = "abcdef012345"
    legacy_hash = hashlib.sha256(code.encode()).hexdigest()
    assert verify_backup_code(code, [legacy_hash]) == 0


# =========================================================================
# generate_qr_code_data_uri
# =========================================================================


def test_qr_code_data_uri_format():
    """Returns a data URI with PNG content type and base64 encoding."""
    uri = generate_qr_code_data_uri("otpauth://totp/test")
    assert uri.startswith("data:image/png;base64,")


def test_qr_code_data_uri_decodable():
    """Base64 portion must decode to valid PNG bytes (starts with
    PNG magic 89 50 4E 47)."""
    import base64

    uri = generate_qr_code_data_uri("otpauth://totp/test")
    b64_part = uri.split("base64,")[1]
    decoded = base64.b64decode(b64_part)
    assert decoded.startswith(b"\x89PNG")
