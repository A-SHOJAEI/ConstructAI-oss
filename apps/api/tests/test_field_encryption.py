"""Tests for the FieldEncryptor — PII at rest.

Each branch of the encrypt/decrypt + dict helper logic is pinned by a
positive or attack-flavoured scenario, so a regression in PII handling
becomes an immediate test failure.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.security.encryption import FieldEncryptor

_PASS = "test-encryption-passphrase-32-chars-min"


@pytest.fixture
def encryptor() -> FieldEncryptor:
    return FieldEncryptor(_PASS)


# ---- constructor ---------------------------------------------------------


def test_constructor_requires_passphrase():
    """An empty passphrase is a refuse-to-start condition — without it
    encryption would silently fall through to a default and PII would
    be readable to anyone with the ciphertext."""
    with patch("app.config.settings.ENCRYPTION_KEY", ""):
        with pytest.raises(ValueError, match="ENCRYPTION_KEY is required"):
            FieldEncryptor()


def test_constructor_uses_settings_key_when_none_passed():
    with patch("app.config.settings.ENCRYPTION_KEY", "from-settings-32-chars-minimum-len"):
        e = FieldEncryptor()
        # Round-trip proves the key was actually used:
        assert e.decrypt(e.encrypt("hello")) == "hello"


# ---- encrypt / decrypt ---------------------------------------------------


def test_encrypt_decrypt_round_trip(encryptor: FieldEncryptor):
    plaintext = "user@example.com"
    ciphertext = encryptor.encrypt(plaintext)
    assert ciphertext != plaintext
    assert encryptor.decrypt(ciphertext) == plaintext


def test_encrypt_uses_random_salt_per_call(encryptor: FieldEncryptor):
    """Different calls with the same plaintext must produce different
    ciphertexts — that's the whole point of the per-value salt fix."""
    a = encryptor.encrypt("same-value")
    b = encryptor.encrypt("same-value")
    assert a != b
    # Both must still decrypt to the original:
    assert encryptor.decrypt(a) == "same-value"
    assert encryptor.decrypt(b) == "same-value"


def test_decrypt_rejects_garbage(encryptor: FieldEncryptor):
    with pytest.raises(ValueError, match="Decryption failed"):
        encryptor.decrypt("not-real-ciphertext")


def test_decrypt_rejects_wrong_key():
    """Ciphertext from one key must NOT decrypt with a different key —
    that's the integrity property."""
    a = FieldEncryptor("passphrase-A-with-32-chars-padding!")
    b = FieldEncryptor("passphrase-B-with-32-chars-padding!")
    ciphertext = a.encrypt("secret")
    with pytest.raises(ValueError):
        b.decrypt(ciphertext)


def test_decrypt_uses_previous_key_for_rotation():
    """After rotation, ciphertext minted with the old passphrase must
    still decrypt — there's a window where new and old coexist."""
    old = FieldEncryptor("old-passphrase-with-32-chars-pad!!")
    ciphertext = old.encrypt("legacy-record")

    with patch(
        "app.config.settings.ENCRYPTION_KEY_PREVIOUS",
        "old-passphrase-with-32-chars-pad!!",
        create=True,
    ):
        new = FieldEncryptor("new-passphrase-with-32-chars-pad!!")
        # New encryptor can decrypt old ciphertext via the previous-key fallback.
        assert new.decrypt(ciphertext) == "legacy-record"


def test_decrypt_legacy_fixed_salt_still_works():
    """Backward-compat: ciphertext minted before the random-salt rollout
    used the fixed _LEGACY_SALT and must remain readable."""
    from cryptography.fernet import Fernet

    from app.services.security.encryption import _LEGACY_SALT, _derive_fernet_key

    legacy_key = _derive_fernet_key(_PASS, _LEGACY_SALT)
    legacy_token = Fernet(legacy_key).encrypt(b"legacy-pii").decode()

    e = FieldEncryptor(_PASS)
    assert e.decrypt(legacy_token) == "legacy-pii"


# ---- encrypt_dict / decrypt_dict ----------------------------------------


def test_encrypt_dict_only_encrypts_pii_fields(encryptor: FieldEncryptor):
    data = {
        "email": "alice@example.com",  # PII — encrypt
        "phone": "+1-555-1234",  # PII — encrypt
        "name": "Alice",  # NOT PII (per the configured set) — pass through
        "id": 42,  # non-string — pass through
    }
    out = encryptor.encrypt_dict(data)
    assert out["email"] != "alice@example.com"
    assert out["phone"] != "+1-555-1234"
    assert out["name"] == "Alice"  # untouched
    assert out["id"] == 42


def test_decrypt_dict_round_trip(encryptor: FieldEncryptor):
    data = {"email": "u@x.com", "ssn": "111-22-3333", "name": "Plain"}
    enc = encryptor.encrypt_dict(data)
    dec = encryptor.decrypt_dict(enc)
    assert dec == data


def test_encrypt_dict_recurses_into_nested():
    e = FieldEncryptor(_PASS)
    nested = {
        "user": {
            "email": "a@b.com",
            "address": "123 Main St",
            "name": "x",
        },
        "ssn": "no-pii-key-at-top",
    }
    enc = e.encrypt_dict(nested)
    assert enc["user"]["email"] != "a@b.com"
    assert enc["user"]["address"] != "123 Main St"
    assert enc["user"]["name"] == "x"  # not PII
    # The top-level "ssn" is in PII_FIELDS, so it gets encrypted:
    assert enc["ssn"] != "no-pii-key-at-top"


def test_encrypt_dict_rejects_excessive_nesting(encryptor: FieldEncryptor):
    """Defensive limit — protects against algorithmic-complexity DoS via
    deeply nested user input."""
    deep: dict = {}
    cursor = deep
    for _ in range(25):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    with pytest.raises(ValueError, match="too deep"):
        encryptor.encrypt_dict(deep)


def test_decrypt_dict_returns_placeholder_on_corruption(encryptor: FieldEncryptor):
    """If the ciphertext for a PII field is corrupted, the decryptor
    must NOT leak the raw ciphertext to the caller — it returns a
    placeholder instead."""
    enc = encryptor.encrypt_dict({"email": "real@example.com"})
    # Corrupt the ciphertext:
    enc["email"] = enc["email"][:-5] + "XXXXX"
    out = encryptor.decrypt_dict(enc)
    assert out["email"] == "[ENCRYPTED]"


def test_pii_fields_set_pinned():
    """Document the canonical PII set — protects against accidental
    deletions on a refactor."""
    expected = {"email", "phone", "ssn", "address", "emergency_contact", "medical_info"}
    assert expected == FieldEncryptor.PII_FIELDS


def test_encrypt_dict_passes_through_non_string_pii_value(encryptor: FieldEncryptor):
    """If a PII-named field somehow holds a non-string (legacy data,
    bug upstream), pass it through rather than crash. Encryption will
    happen the next time the value is normalised to str."""
    out = encryptor.encrypt_dict({"phone": 1234567890})
    assert out["phone"] == 1234567890
