"""Tests for auth token helpers (verification + MFA challenge tokens).

Pin documented expiries (24h email verification, 5min MFA challenge),
the token type discriminator, and the iss/aud claims.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.api.v1.auth import (
    _create_email_verification_token,
    _create_mfa_challenge_token,
)
from app.config import settings

# =========================================================================
# _create_email_verification_token
# =========================================================================


def test_email_verification_token_decodes_with_correct_claims():
    token = _create_email_verification_token("user-1", "alice@example.com")
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    assert payload["sub"] == "user-1"
    assert payload["email"] == "alice@example.com"
    assert payload["type"] == "email_verification"


def test_email_verification_token_24h_expiry():
    """[contract] Email verification expires after 24 hours.
    Pin: refactor must NOT change to a shorter window (poor UX) or
    longer (security risk — verification links should be short-lived)."""
    before = datetime.now(UTC)
    token = _create_email_verification_token("user-1", "alice@x.com")
    after = datetime.now(UTC)

    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
    # Should be 24h from now (allow small timing window):
    expected_min = before + timedelta(hours=24, seconds=-1)
    expected_max = after + timedelta(hours=24, seconds=1)
    assert expected_min <= exp <= expected_max


def test_email_verification_token_includes_jti():
    """[security] JTI included for blacklist/rotation support."""
    token = _create_email_verification_token("user-1", "alice@x.com")
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    assert "jti" in payload
    # Valid UUID format:
    import uuid as _uuid

    _uuid.UUID(payload["jti"])


def test_email_verification_token_unique_jti_per_call():
    """[security] Each call generates a fresh JTI (so a refresh
    invalidates the prior token via blacklist)."""
    t1 = _create_email_verification_token("user-1", "x@y.com")
    t2 = _create_email_verification_token("user-1", "x@y.com")
    p1 = jwt.decode(
        t1,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    p2 = jwt.decode(
        t2,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    assert p1["jti"] != p2["jti"]


def test_email_verification_token_iss_and_aud():
    """[security] iss=constructai, aud=constructai-api. Pin so a
    refactor doesn't accept tokens issued for a different audience
    or service."""
    token = _create_email_verification_token("u-1", "a@b.com")
    # Decode without validating iss/aud first to inspect:
    payload_unverified = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"verify_aud": False, "verify_iss": False},
    )
    assert payload_unverified["iss"] == "constructai"
    assert payload_unverified["aud"] == "constructai-api"


def test_email_verification_token_type_discriminator():
    """[security] type='email_verification' so the same JWT key can
    issue distinct token types (access/refresh/email/mfa) without
    cross-type confusion. Pin: refactor must NOT drop the type field."""
    token = _create_email_verification_token("u-1", "a@b.com")
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    assert payload["type"] == "email_verification"


def test_email_verification_token_rejected_by_wrong_audience():
    """[security] Token issued for 'constructai-api' should NOT
    decode as 'someone-else-api'. Pin so a refactor doesn't broaden
    the audience check."""
    token = _create_email_verification_token("u-1", "a@b.com")
    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience="someone-else-api",
            issuer="constructai",
        )


# =========================================================================
# _create_mfa_challenge_token
# =========================================================================


def test_mfa_challenge_token_decodes_with_correct_claims():
    token = _create_mfa_challenge_token("user-1", "org-xyz")
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    assert payload["sub"] == "user-1"
    assert payload["org_id"] == "org-xyz"
    assert payload["type"] == "mfa_challenge"


def test_mfa_challenge_token_5_minute_expiry():
    """[contract] MFA challenge expires in 5 minutes — short enough
    to limit replay window, long enough for user to find their
    authenticator app. Pin: refactor must NOT extend (replay risk)."""
    before = datetime.now(UTC)
    token = _create_mfa_challenge_token("u-1", "org-1")
    after = datetime.now(UTC)

    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
    expected_min = before + timedelta(minutes=5, seconds=-1)
    expected_max = after + timedelta(minutes=5, seconds=1)
    assert expected_min <= exp <= expected_max


def test_mfa_challenge_token_includes_jti():
    """JTI included for one-shot use / blacklist."""
    token = _create_mfa_challenge_token("u-1", "org-1")
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    assert "jti" in payload


def test_mfa_challenge_token_unique_per_call():
    t1 = _create_mfa_challenge_token("u-1", "org-1")
    t2 = _create_mfa_challenge_token("u-1", "org-1")
    p1 = jwt.decode(
        t1,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    p2 = jwt.decode(
        t2,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    assert p1["jti"] != p2["jti"]


def test_mfa_challenge_token_includes_iss_and_aud():
    token = _create_mfa_challenge_token("u-1", "org-1")
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"verify_aud": False, "verify_iss": False},
    )
    assert payload["iss"] == "constructai"
    assert payload["aud"] == "constructai-api"


def test_mfa_challenge_token_type_isolated_from_email():
    """[security] MFA challenge type is distinct from email_verification.
    Pin: refactor must NOT use a generic 'challenge' type that could
    be confused across flows."""
    mfa_token = _create_mfa_challenge_token("u-1", "org-1")
    email_token = _create_email_verification_token("u-1", "a@b.com")
    mfa_payload = jwt.decode(
        mfa_token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    email_payload = jwt.decode(
        email_token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    assert mfa_payload["type"] != email_payload["type"]


# =========================================================================
# Comparison: relative expiries
# =========================================================================


def test_mfa_challenge_expires_before_email_verification():
    """[invariant] MFA challenge (5 min) must always expire well
    before email verification (24h). Pin so a refactor doesn't
    invert these durations."""
    mfa_token = _create_mfa_challenge_token("u-1", "org-1")
    email_token = _create_email_verification_token("u-1", "a@b.com")
    mfa_payload = jwt.decode(
        mfa_token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    email_payload = jwt.decode(
        email_token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience="constructai-api",
        issuer="constructai",
    )
    assert mfa_payload["exp"] < email_payload["exp"]
