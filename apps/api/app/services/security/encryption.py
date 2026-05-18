"""Field-level encryption for PII using Fernet (AES-128-CBC + HMAC)."""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, ClassVar

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import settings

logger = logging.getLogger(__name__)

# SECURITY: Legacy fixed salt - scheduled for removal after batch re-encryption migration.
# All new encryptions use per-value random salts (line ~90).
# TODO: Run one-time batch re-encryption job, then delete _LEGACY_SALT and legacy decrypt path.
#
# SECURITY [M-09]: A fixed salt enables precomputation attacks (rainbow tables)
# against the KDF.  The migration path:
# 1. New encrypt() calls already use random per-value salts (os.urandom).
# 2. decrypt() tries random-salt format first, then falls back to legacy salt.
# 3. To fully migrate: run a one-time batch job that reads all encrypted fields,
#    decrypts with legacy salt, and re-encrypts with random salt.
# 4. Once all legacy ciphertext is re-encrypted, remove _LEGACY_SALT and the
#    legacy fallback path in decrypt().
_LEGACY_SALT = b"constructai-field-encryption-salt"

# Length of the random salt prepended to each new ciphertext.
_SALT_LENGTH = 16


def _derive_fernet_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a URL-safe base64 Fernet key from *passphrase* and *salt*."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    raw = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


class FieldEncryptor:
    """Field-level encryption for PII fields.

    Uses ``cryptography.fernet.Fernet`` (AES-128-CBC with HMAC-SHA256)
    for simplicity and safe defaults.

    The encryption key is sourced exclusively from
    ``settings.ENCRYPTION_KEY``.  In non-test environments the config
    validation (``Settings.validate_production_config``) already raises
    a ``ValueError`` when ``ENCRYPTION_KEY`` is empty, so the
    constructor will never silently fall back to an insecure key.
    """

    PII_FIELDS: ClassVar[set[str]] = {
        "email",
        "phone",
        "ssn",
        "address",
        "emergency_contact",
        "medical_info",
    }

    def __init__(self, encryption_key: str | None = None) -> None:
        # Use the explicitly passed key, otherwise read from settings.
        self._passphrase = encryption_key or settings.ENCRYPTION_KEY
        if not self._passphrase:
            raise ValueError(
                "ENCRYPTION_KEY is required. Set it via the "
                "ENCRYPTION_KEY environment variable or pass it "
                "explicitly to FieldEncryptor()."
            )
        # Legacy Fernet instance for backward-compatible decryption of
        # values encrypted with the old fixed salt.
        legacy_key = _derive_fernet_key(self._passphrase, _LEGACY_SALT)
        self._legacy_fernet = Fernet(legacy_key)

        # Previous encryption key for key rotation support.
        self._previous_passphrase = getattr(settings, "ENCRYPTION_KEY_PREVIOUS", "")

    # -------------------------------------------------------------- #
    # Core encrypt / decrypt
    # -------------------------------------------------------------- #

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string value.

        A random 16-byte salt is generated per call and prepended to the
        Fernet token.  The combined payload is base64-encoded so the
        output remains a safe ASCII string.
        """
        salt = os.urandom(_SALT_LENGTH)
        key = _derive_fernet_key(self._passphrase, salt)
        f = Fernet(key)
        token = f.encrypt(plaintext.encode("utf-8"))
        # Prepend the salt to the token and base64-encode the whole thing
        combined = salt + token
        return base64.urlsafe_b64encode(combined).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a ciphertext string.

        Tries the new per-value salt format first.  If that fails, falls
        back to the legacy fixed-salt format for backward compatibility.
        """
        # --- Try new format: base64(salt + fernet_token) ---
        try:
            combined = base64.urlsafe_b64decode(ciphertext.encode("utf-8"))
            if len(combined) > _SALT_LENGTH:
                salt = combined[:_SALT_LENGTH]
                token = combined[_SALT_LENGTH:]
                key = _derive_fernet_key(self._passphrase, salt)
                f = Fernet(key)
                plaintext_bytes = f.decrypt(token)
                return plaintext_bytes.decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError):
            pass

        # --- Fallback: legacy fixed-salt format ---
        try:
            plaintext_bytes = self._legacy_fernet.decrypt(
                ciphertext.encode("utf-8"),
            )
            return plaintext_bytes.decode("utf-8")
        except InvalidToken:
            pass

        # --- Key rotation fallback: try ENCRYPTION_KEY_PREVIOUS ---
        if self._previous_passphrase:
            try:
                combined = base64.urlsafe_b64decode(ciphertext.encode("utf-8"))
                if len(combined) > _SALT_LENGTH:
                    salt_prev = combined[:_SALT_LENGTH]
                    token_prev = combined[_SALT_LENGTH:]
                    key_prev = _derive_fernet_key(self._previous_passphrase, salt_prev)
                    return Fernet(key_prev).decrypt(token_prev).decode("utf-8")
            except (InvalidToken, ValueError, UnicodeDecodeError):
                pass
            try:
                prev_key = _derive_fernet_key(self._previous_passphrase, _LEGACY_SALT)
                return Fernet(prev_key).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
            except (InvalidToken, ValueError, UnicodeDecodeError):
                pass

        logger.error("Decryption failed: invalid token or wrong key")
        raise ValueError("Decryption failed: invalid token or wrong key") from None

    # -------------------------------------------------------------- #
    # Dict helpers
    # -------------------------------------------------------------- #

    def encrypt_dict(self, data: dict[str, Any], _depth: int = 0) -> dict[str, Any]:
        """Encrypt PII fields in a dictionary.

        Non-PII fields and non-string values are passed through
        unchanged.  Nested dicts are processed recursively.
        """
        if _depth > 20:
            raise ValueError("Nested dict too deep for encryption")
        result: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                result[key] = self.encrypt_dict(value, _depth=_depth + 1)
            elif key in self.PII_FIELDS and isinstance(value, str):
                result[key] = self.encrypt(value)
            else:
                result[key] = value
        return result

    def decrypt_dict(self, data: dict[str, Any], _depth: int = 0) -> dict[str, Any]:
        """Decrypt PII fields in a dictionary.

        Non-PII fields and non-string values are passed through
        unchanged.  Nested dicts are processed recursively.
        """
        if _depth > 20:
            raise ValueError("Nested dict too deep for decryption")
        result: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                result[key] = self.decrypt_dict(value, _depth=_depth + 1)
            elif key in self.PII_FIELDS and isinstance(value, str):
                try:
                    result[key] = self.decrypt(value)
                except ValueError:
                    logger.warning(
                        "Could not decrypt field '%s'; "
                        "returning placeholder instead of raw ciphertext",
                        key,
                    )
                    result[key] = "[ENCRYPTED]"
            else:
                result[key] = value
        return result
