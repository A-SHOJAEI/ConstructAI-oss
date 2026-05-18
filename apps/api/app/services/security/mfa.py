"""Multi-Factor Authentication via TOTP (RFC 6238).

Uses pyotp for TOTP generation/verification and qrcode for QR code
generation to simplify authenticator app setup.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets

import pyotp
import qrcode


def generate_totp_secret() -> str:
    """Generate a random base32 TOTP secret."""
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str) -> str:
    """Get an otpauth:// URI for authenticator app setup."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="ConstructAI")


def verify_totp(secret: str, code: str) -> bool:
    """Verify a TOTP code against the secret.

    Allows +-1 time step window for clock drift.
    """
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def generate_backup_codes(
    count: int = 10, salt: str | None = None
) -> tuple[list[str], list[str], str]:
    """Generate one-time backup codes with a per-user salt.

    Parameters
    ----------
    count : int
        Number of backup codes to generate.
    salt : str, optional
        Per-user salt. If not provided, a random 16-byte hex salt is generated.

    Returns
    -------
    tuple[list[str], list[str], str]
        ``(plaintext_codes, hashed_codes, salt)`` -- show plaintext to user once,
        store hashed codes and salt in the database.
    """
    if salt is None:
        salt = secrets.token_hex(16)
    codes = [secrets.token_hex(6) for _ in range(count)]  # 12-char hex codes (48-bit entropy)
    salt_bytes = salt.encode()
    hashed = [hashlib.sha256(salt_bytes + c.encode()).hexdigest() for c in codes]
    return codes, hashed, salt


def verify_backup_code(code: str, hashed_codes: list[str], salt: str = "") -> int | None:
    """Check a backup code against stored hashes.

    Parameters
    ----------
    code : str
        The plaintext backup code to verify.
    hashed_codes : list[str]
        The stored hashed backup codes.
    salt : str
        The per-user salt used during hashing. Defaults to empty string
        for backward compatibility with pre-salt codes.

    Returns the index of the matched code (for removal), or ``None``.
    """
    # SECURITY [M-1/M-08]: Use constant-time comparison AND iterate every
    # stored hash without early break, so the time-to-respond can't leak
    # which code index matched (or whether any matched at all, for a
    # distributed attacker with aligned timing observations).
    h = hashlib.sha256(salt.encode() + code.encode()).hexdigest()
    match_idx: int | None = None
    for idx, stored_hash in enumerate(hashed_codes):
        if hmac.compare_digest(h, stored_hash) and match_idx is None:
            match_idx = idx
    return match_idx


def generate_qr_code_data_uri(uri: str) -> str:
    """Generate a QR code as a base64 data URI (PNG).

    The returned string can be used directly in an ``<img src="...">`` tag.
    """
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
