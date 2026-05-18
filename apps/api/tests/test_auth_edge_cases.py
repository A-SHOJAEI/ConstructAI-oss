"""Tests for authentication edge cases not covered by test_auth.py.

Covers: expired tokens, malformed tokens, missing JTI, token_version mismatch,
account lockout, locked account login, lockout window expiry, token revocation,
concurrent session handling, and password complexity validation.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import settings
from app.services.auth import (
    _validate_password_complexity,
    refresh_tokens,
)
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(payload: dict, secret: str | None = None, algorithm: str | None = None) -> str:
    """Create a raw JWT with arbitrary payload (no safety defaults)."""
    return jwt.encode(
        payload,
        secret or settings.JWT_SECRET_KEY,
        algorithm=algorithm or settings.JWT_ALGORITHM,
    )


# ---------------------------------------------------------------------------
# Token validation edge cases
# ---------------------------------------------------------------------------


class TestExpiredToken:
    """Expired JWT should be rejected by decode_access_token."""

    def test_expired_access_token_returns_none(self):
        payload = {
            "sub": str(uuid.uuid4()),
            "org_id": str(uuid.uuid4()),
            "exp": datetime.now(UTC) - timedelta(hours=1),
            "type": "access",
            "jti": str(uuid.uuid4()),
            "iss": "constructai",
            "aud": "constructai-api",
        }
        token = _make_token(payload)
        assert decode_access_token(token) is None


class TestMalformedToken:
    """Invalid/malformed JWTs should be rejected."""

    def test_garbage_string_returns_none(self):
        assert decode_access_token("not-a-jwt") is None

    def test_wrong_secret_returns_none(self):
        payload = {
            "sub": str(uuid.uuid4()),
            "org_id": str(uuid.uuid4()),
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "type": "access",
            "jti": str(uuid.uuid4()),
            "iss": "constructai",
            "aud": "constructai-api",
        }
        token = _make_token(payload, secret="wrong-secret-key-that-is-very-long")
        assert decode_access_token(token) is None

    def test_missing_sub_field(self):
        """Token without 'sub' should decode but downstream checks reject it."""
        payload = {
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "type": "access",
            "jti": str(uuid.uuid4()),
            "iss": "constructai",
            "aud": "constructai-api",
        }
        token = _make_token(payload)
        result = decode_access_token(token)
        # Decodes successfully, but no 'sub' — dependency layer rejects it
        assert result is not None
        assert result.get("sub") is None

    def test_wrong_token_type_rejected(self):
        """A refresh token should not be accepted as an access token."""
        payload = {
            "sub": str(uuid.uuid4()),
            "org_id": str(uuid.uuid4()),
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "type": "refresh",
            "jti": str(uuid.uuid4()),
            "iss": "constructai",
            "aud": "constructai-api",
        }
        token = _make_token(payload)
        assert decode_access_token(token) is None

    def test_wrong_issuer_rejected(self):
        payload = {
            "sub": str(uuid.uuid4()),
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "type": "access",
            "jti": str(uuid.uuid4()),
            "iss": "evil-issuer",
            "aud": "constructai-api",
        }
        token = _make_token(payload)
        assert decode_access_token(token) is None

    def test_wrong_audience_rejected(self):
        payload = {
            "sub": str(uuid.uuid4()),
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "type": "access",
            "jti": str(uuid.uuid4()),
            "iss": "constructai",
            "aud": "wrong-audience",
        }
        token = _make_token(payload)
        assert decode_access_token(token) is None


class TestTokenMissingJTI:
    """Tokens without JTI should be rejected by get_current_user dependency."""

    @pytest.mark.asyncio
    async def test_missing_jti_returns_401(self, client, test_user):
        """Token without JTI should be rejected (can't be blacklisted)."""
        payload = {
            "sub": str(test_user.id),
            "org_id": str(test_user.org_id),
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "type": "access",
            "iss": "constructai",
            "aud": "constructai-api",
            # Deliberately no "jti" key
        }
        token = _make_token(payload)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 401
        assert "JTI" in resp.json()["detail"]


class TestTokenVersionMismatch:
    """Token issued before password change should be rejected."""

    @pytest.mark.asyncio
    async def test_old_token_version_returns_401(self, client, db_session, test_user):
        """After password change (token_version incremented), old tokens rejected."""
        # Create token with current token_version (0)
        token = create_access_token(
            data={
                "sub": str(test_user.id),
                "org_id": str(test_user.org_id),
                "token_version": 0,
            }
        )
        # Simulate password change: increment token_version
        test_user.token_version = 1
        db_session.add(test_user)
        await db_session.flush()

        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 401
        assert (
            "password change" in resp.json()["detail"].lower()
            or "invalidated" in resp.json()["detail"].lower()
        )


class TestAccountLockout:
    """After 5 failed attempts within the lockout window, account should be locked."""

    @pytest.mark.asyncio
    async def test_lockout_after_5_failed_attempts(self, client, db_session, test_user):
        """5 failed login attempts should trigger account lockout."""
        email = test_user.email
        # Make 5 failed login attempts
        for _ in range(5):
            await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "WrongPassword123!"},
            )

        # 6th attempt should get 429
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "WrongPassword123!"},
        )
        assert resp.status_code == 429
        assert "locked" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_locked_account_cannot_login_with_correct_password(
        self, client, db_session, test_user
    ):
        """Even correct credentials should fail when account is locked."""
        email = test_user.email
        for _ in range(5):
            await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "WrongPassword123!"},
            )

        # Try with correct password — should still be locked
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "TestPassword123!"},
        )
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_lockout_window_expiry(self):
        """Failed attempts outside the window should not count."""
        import time

        from app.services.security.redis_state import (
            _LOCKOUT_WINDOW,
            _memory_failed_attempts,
            is_locked_out,
            record_failed_attempt,
        )

        test_email = f"lockout-expiry-{uuid.uuid4().hex[:8]}@example.com"
        # Clear any prior state
        _memory_failed_attempts.pop(test_email, None)

        # Record 4 attempts (just under threshold)
        for _ in range(4):
            await record_failed_attempt(test_email)

        assert not await is_locked_out(test_email)

        # Manually expire all attempts by setting timestamps in the past
        _memory_failed_attempts[test_email] = [
            time.monotonic() - _LOCKOUT_WINDOW - 10 for _ in _memory_failed_attempts[test_email]
        ]

        # After expiry, should not be locked out
        assert not await is_locked_out(test_email)

        # Clean up
        _memory_failed_attempts.pop(test_email, None)


class TestTokenRevocation:
    """Token blacklisting via logout should prevent reuse."""

    @pytest.mark.asyncio
    async def test_blacklisted_token_rejected(self, client, test_user):
        """After blacklisting a JTI, subsequent requests with that token fail."""
        from app.dependencies import blacklist_token

        token = create_access_token(
            data={
                "sub": str(test_user.id),
                "org_id": str(test_user.org_id),
                "token_version": test_user.token_version or 0,
            }
        )
        headers = {"Authorization": f"Bearer {token}"}

        # Verify token works initially
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 200

        # Blacklist the token's JTI
        payload = decode_access_token(token)
        assert payload is not None
        await blacklist_token(payload["jti"])

        # Now the token should be rejected
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 401
        assert "revoked" in resp.json()["detail"].lower()


class TestConcurrentSessions:
    """Multiple valid tokens for the same user should work until revoked."""

    @pytest.mark.asyncio
    async def test_multiple_tokens_valid(self, client, test_user):
        """Two tokens issued to the same user should both work."""
        token_data = {
            "sub": str(test_user.id),
            "org_id": str(test_user.org_id),
            "token_version": test_user.token_version or 0,
        }
        token_a = create_access_token(data=token_data)
        token_b = create_access_token(data=token_data)

        resp_a = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_a}"})
        resp_b = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_b}"})
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200

    @pytest.mark.asyncio
    async def test_revoking_one_token_does_not_affect_other(self, client, test_user):
        """Blacklisting token A should not affect token B."""
        from app.dependencies import blacklist_token

        token_data = {
            "sub": str(test_user.id),
            "org_id": str(test_user.org_id),
            "token_version": test_user.token_version or 0,
        }
        token_a = create_access_token(data=token_data)
        token_b = create_access_token(data=token_data)

        # Blacklist token A
        payload_a = decode_access_token(token_a)
        await blacklist_token(payload_a["jti"])

        # Token A should fail
        resp_a = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_a}"})
        assert resp_a.status_code == 401

        # Token B should still work
        resp_b = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_b}"})
        assert resp_b.status_code == 200


class TestPasswordComplexity:
    """Password complexity enforcement at the service layer."""

    def test_short_password_rejected(self):
        with pytest.raises(ValueError, match="at least 12"):
            _validate_password_complexity("Short1!")

    def test_no_uppercase_rejected(self):
        with pytest.raises(ValueError, match="uppercase"):
            _validate_password_complexity("nouppercase123!!")

    def test_no_lowercase_rejected(self):
        with pytest.raises(ValueError, match="lowercase"):
            _validate_password_complexity("NOLOWERCASE123!!")

    def test_no_digit_rejected(self):
        with pytest.raises(ValueError, match="digit"):
            _validate_password_complexity("NoDigitsHere!!!!")

    def test_no_special_char_rejected(self):
        with pytest.raises(ValueError, match="special"):
            _validate_password_complexity("NoSpecialChar123")

    def test_too_long_rejected(self):
        with pytest.raises(ValueError, match="128"):
            _validate_password_complexity("A" * 129 + "a1!")

    def test_valid_password_accepted(self):
        # Should not raise
        _validate_password_complexity("ValidPassword1!!")


class TestRefreshTokenEdgeCases:
    """Edge cases for the refresh_tokens service function."""

    @pytest.mark.asyncio
    async def test_refresh_with_invalid_token_returns_none(self, db_session):
        result = await refresh_tokens(db_session, refresh_token="invalid.token.here")
        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_with_expired_token_returns_none(self, db_session, test_user):
        """Expired refresh token should be rejected."""
        payload = {
            "sub": str(test_user.id),
            "org_id": str(test_user.org_id),
            "token_version": test_user.token_version or 0,
            "exp": datetime.now(UTC) - timedelta(days=1),
            "type": "refresh",
            "jti": str(uuid.uuid4()),
            "iss": "constructai",
            "aud": "constructai-api",
        }
        expired_refresh = _make_token(payload)
        result = await refresh_tokens(db_session, refresh_token=expired_refresh)
        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_with_mismatched_token_version(self, db_session, test_user):
        """Refresh token with old token_version should be rejected."""
        token = create_refresh_token(
            data={
                "sub": str(test_user.id),
                "org_id": str(test_user.org_id),
                "token_version": 0,
            }
        )
        # Simulate password change
        test_user.token_version = 1
        db_session.add(test_user)
        await db_session.flush()

        result = await refresh_tokens(db_session, refresh_token=token)
        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_replay_rejected(self, db_session, test_user):
        """Using the same refresh token twice should be rejected."""
        token = create_refresh_token(
            data={
                "sub": str(test_user.id),
                "org_id": str(test_user.org_id),
                "token_version": test_user.token_version or 0,
            }
        )
        # First use should succeed
        result1 = await refresh_tokens(db_session, refresh_token=token)
        assert result1 is not None

        # Second use should be rejected (JTI already consumed)
        result2 = await refresh_tokens(db_session, refresh_token=token)
        assert result2 is None


class TestInactiveUserToken:
    """Deactivated users should be rejected even with valid tokens."""

    @pytest.mark.asyncio
    async def test_inactive_user_rejected(self, client, db_session, test_user):
        token = create_access_token(
            data={
                "sub": str(test_user.id),
                "org_id": str(test_user.org_id),
                "token_version": test_user.token_version or 0,
            }
        )
        # Deactivate the user
        test_user.is_active = False
        db_session.add(test_user)
        await db_session.flush()

        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 401
