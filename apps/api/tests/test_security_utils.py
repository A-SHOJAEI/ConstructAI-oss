"""Tests for the auth/security utility module.

Most tests don't need a DB or Redis — bcrypt + JWT round-trips are pure.
The Redis-backed key rotation paths are exercised with mocks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import jwt
import pytest

from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    get_jwt_key_version,
    hash_password,
    rotate_jwt_key,
    verify_password,
)

# ---- bcrypt --------------------------------------------------------------


def test_hash_password_produces_different_hashes_each_call():
    """Same password → different hashes (gensalt randomness). Both must
    verify against the original password."""
    h1 = hash_password("CorrectHorseBatteryStaple!1")
    h2 = hash_password("CorrectHorseBatteryStaple!1")
    assert h1 != h2
    assert verify_password("CorrectHorseBatteryStaple!1", h1)
    assert verify_password("CorrectHorseBatteryStaple!1", h2)


def test_verify_password_rejects_wrong_password():
    h = hash_password("right-password")
    assert not verify_password("wrong-password", h)


def test_hash_password_uses_settings_bcrypt_rounds():
    """The configured rounds must reach bcrypt — speeds up tests
    without weakening production hashes."""
    with patch("app.config.settings.BCRYPT_ROUNDS", 4):
        h = hash_password("test")
    # bcrypt encodes the cost in the hash prefix: $2b$04$...
    assert h.startswith("$2b$04$")


# ---- access token -------------------------------------------------------


def test_create_access_token_round_trips():
    token = create_access_token({"sub": "user-1", "org_id": "org-1"})
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "user-1"
    assert payload["org_id"] == "org-1"
    assert payload["type"] == "access"
    assert payload["iss"] == "constructai"
    assert payload["aud"] == "constructai-api"
    assert "jti" in payload  # uuid
    assert "exp" in payload


def test_access_token_jti_is_unique_per_call():
    """Each token gets a fresh JTI — that's the property the blacklist
    relies on."""
    a = create_access_token({"sub": "user-1"})
    b = create_access_token({"sub": "user-1"})
    assert decode_access_token(a)["jti"] != decode_access_token(b)["jti"]


def test_access_token_respects_custom_expiry():
    expires = timedelta(seconds=30)
    token = create_access_token({"sub": "u"}, expires_delta=expires)
    payload = decode_access_token(token)
    exp = datetime.fromtimestamp(payload["exp"], UTC)
    delta = (exp - datetime.now(UTC)).total_seconds()
    # Should be ~30s in the future, allow ±2s slack for execution time.
    assert 28 <= delta <= 32


def test_decode_access_token_rejects_garbage():
    assert decode_access_token("not.a.token") is None


def test_decode_access_token_rejects_wrong_type():
    """A refresh token shouldn't decode as an access token (and vice
    versa) even though they share signing key — the type guard catches
    misuse."""
    refresh = create_refresh_token({"sub": "u"})
    assert decode_access_token(refresh) is None


def test_decode_access_token_rejects_expired():
    token = create_access_token(
        {"sub": "u"},
        expires_delta=timedelta(seconds=-10),  # already expired
    )
    assert decode_access_token(token) is None


def test_decode_access_token_rejects_wrong_issuer():
    """Tokens minted by a different issuer must not validate."""
    payload = {
        "sub": "u",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "type": "access",
        "iss": "evil-issuer",
        "aud": "constructai-api",
    }
    from app.config import settings as _settings

    bad_token = jwt.encode(payload, _settings.JWT_SECRET_KEY, algorithm=_settings.JWT_ALGORITHM)
    assert decode_access_token(bad_token) is None


def test_decode_access_token_rejects_wrong_audience():
    payload = {
        "sub": "u",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "type": "access",
        "iss": "constructai",
        "aud": "someone-else",
    }
    from app.config import settings as _settings

    bad_token = jwt.encode(payload, _settings.JWT_SECRET_KEY, algorithm=_settings.JWT_ALGORITHM)
    assert decode_access_token(bad_token) is None


def test_decode_access_token_uses_previous_key_during_rotation():
    """During a JWT_SECRET_KEY rollover, tokens signed with the previous
    key must still validate — that's the entire point of having a
    JWT_SECRET_KEY_PREVIOUS setting."""
    from app.config import settings as _settings

    old_key = "old-secret-key-minimum-32-characters-long"
    payload = {
        "sub": "u",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "type": "access",
        "iss": "constructai",
        "aud": "constructai-api",
    }
    old_token = jwt.encode(payload, old_key, algorithm=_settings.JWT_ALGORITHM)

    # Without the fallback, decode fails:
    assert decode_access_token(old_token) is None

    # With JWT_SECRET_KEY_PREVIOUS configured, it works:
    with patch("app.config.settings.JWT_SECRET_KEY_PREVIOUS", old_key, create=True):
        decoded = decode_access_token(old_token)
        assert decoded is not None
        assert decoded["sub"] == "u"


def test_decode_access_token_previous_key_still_rejects_wrong_type():
    """Even via the previous-key fallback, type guard wins — a refresh
    token signed with the previous key must NOT decode as an access
    token."""
    from app.config import settings as _settings

    old_key = "old-secret-key-minimum-32-characters-long"
    payload = {
        "sub": "u",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "type": "refresh",
        "iss": "constructai",
        "aud": "constructai-api",
    }
    old_token = jwt.encode(payload, old_key, algorithm=_settings.JWT_ALGORITHM)
    with patch("app.config.settings.JWT_SECRET_KEY_PREVIOUS", old_key, create=True):
        assert decode_access_token(old_token) is None


# ---- refresh token ------------------------------------------------------


def test_create_refresh_token_round_trips():
    token = create_refresh_token({"sub": "user-1", "token_version": 0})
    payload = decode_refresh_token(token)
    assert payload is not None
    assert payload["sub"] == "user-1"
    assert payload["type"] == "refresh"


def test_decode_refresh_token_rejects_access_token_type():
    access = create_access_token({"sub": "u"})
    assert decode_refresh_token(access) is None


def test_decode_refresh_token_rejects_garbage():
    assert decode_refresh_token("garbage") is None


def test_decode_refresh_token_uses_previous_key_during_rotation():
    from app.config import settings as _settings

    old_key = "old-secret-key-minimum-32-characters-long"
    payload = {
        "sub": "u",
        "exp": datetime.now(UTC) + timedelta(days=1),
        "type": "refresh",
        "iss": "constructai",
        "aud": "constructai-api",
    }
    old_token = jwt.encode(payload, old_key, algorithm=_settings.JWT_ALGORITHM)
    with patch("app.config.settings.JWT_SECRET_KEY_PREVIOUS", old_key, create=True):
        assert decode_refresh_token(old_token) is not None


# ---- key rotation Redis paths -------------------------------------------


async def test_rotate_jwt_key_requires_redis():
    """Without Redis, rotation must fail loudly — silent failure would
    leave clients unable to validate tokens minted with the new key."""
    with patch("app.services.security.redis_state._get_redis", new=AsyncMock(return_value=None)):
        with pytest.raises(RuntimeError, match="Redis required"):
            await rotate_jwt_key("brand-new-key")


async def test_rotate_jwt_key_pipelines_set_set_incr():
    """Rotation atomically writes prev/current and increments version
    via a single Redis pipeline so other workers see a consistent
    snapshot."""
    fake = AsyncMock()
    pipe = AsyncMock()
    pipe.set = AsyncMock()
    pipe.incr = AsyncMock()
    pipe.execute = AsyncMock(return_value=["OK", "OK", 7])
    fake.pipeline = lambda: pipe

    with patch("app.services.security.redis_state._get_redis", new=AsyncMock(return_value=fake)):
        version = await rotate_jwt_key("new-key")
    assert version == 7
    pipe.execute.assert_awaited_once()


async def test_get_jwt_key_version_defaults_to_one_when_unset():
    """A fresh deployment has no version key → assume version 1, not 0
    (avoid off-by-one when comparing against persisted token versions)."""
    fake = AsyncMock()
    fake.get = AsyncMock(return_value=None)
    with patch("app.services.security.redis_state._get_redis", new=AsyncMock(return_value=fake)):
        assert await get_jwt_key_version() == 1


async def test_get_jwt_key_version_returns_int_from_redis():
    fake = AsyncMock()
    fake.get = AsyncMock(return_value="42")
    with patch("app.services.security.redis_state._get_redis", new=AsyncMock(return_value=fake)):
        assert await get_jwt_key_version() == 42


async def test_get_jwt_key_version_returns_one_when_redis_unreachable():
    """A Redis outage shouldn't crash callers — the function returns the
    safe default."""
    with patch("app.services.security.redis_state._get_redis", new=AsyncMock(return_value=None)):
        assert await get_jwt_key_version() == 1
