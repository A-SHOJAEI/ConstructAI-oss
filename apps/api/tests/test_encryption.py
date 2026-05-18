from __future__ import annotations

import pytest

from app.services.security.encryption import FieldEncryptor


class TestFieldEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        enc = FieldEncryptor(encryption_key="test-key-for-unit-tests")
        plaintext = "sensitive@email.com"
        ciphertext = enc.encrypt(plaintext)
        assert ciphertext != plaintext
        decrypted = enc.decrypt(ciphertext)
        assert decrypted == plaintext

    def test_different_key_fails(self):
        enc1 = FieldEncryptor(encryption_key="key-one-for-testing")
        enc2 = FieldEncryptor(encryption_key="key-two-for-testing")
        ciphertext = enc1.encrypt("secret")
        with pytest.raises(ValueError):
            enc2.decrypt(ciphertext)

    def test_encrypt_dict_pii_fields(self):
        enc = FieldEncryptor(encryption_key="test-key-for-unit-tests")
        data = {
            "email": "test@example.com",
            "name": "John",
            "phone": "555-1234",
        }
        encrypted = enc.encrypt_dict(data)
        assert encrypted["email"] != "test@example.com"
        assert encrypted["name"] == "John"  # not a PII field
        assert encrypted["phone"] != "555-1234"

    def test_decrypt_dict_pii_fields(self):
        enc = FieldEncryptor(encryption_key="test-key-for-unit-tests")
        data = {"email": "test@example.com", "name": "John"}
        encrypted = enc.encrypt_dict(data)
        decrypted = enc.decrypt_dict(encrypted)
        assert decrypted["email"] == "test@example.com"
        assert decrypted["name"] == "John"

    def test_pii_fields_defined(self):
        assert "email" in FieldEncryptor.PII_FIELDS
        assert "phone" in FieldEncryptor.PII_FIELDS
        assert "ssn" in FieldEncryptor.PII_FIELDS

    def test_empty_string_encrypt(self):
        enc = FieldEncryptor(encryption_key="test-key-for-unit-tests")
        ciphertext = enc.encrypt("")
        decrypted = enc.decrypt(ciphertext)
        assert decrypted == ""
