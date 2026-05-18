"""Tests for password reset API routes.

Covers: forgot-password (existing/non-existing email), rate limiting,
reset with valid/expired/used/invalid token, and token_version increment.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.password_reset import (
    _RESET_REQUEST_LIMIT,
    _create_reset_token,
    _decode_reset_token,
    _reset_attempt_tracker,
    _reset_request_tracker,
)


@pytest.fixture(autouse=True)
def _clear_reset_trackers():
    """Clear the module-level rate-limit dicts between tests.

    Each test gets a fresh backend, but ``_reset_request_tracker`` and
    ``_reset_attempt_tracker`` are module-level dicts in
    ``password_reset.py`` that persist for the lifetime of the process.
    Without clearing, attempts/requests from previous tests stay on the
    counter and trip 429 in unrelated tests.
    """
    _reset_request_tracker.clear()
    _reset_attempt_tracker.clear()
    yield
    _reset_request_tracker.clear()
    _reset_attempt_tracker.clear()


from app.models.organization import Organization
from app.models.user import User

from app.config import settings
from app.utils.security import hash_password


@pytest_asyncio.fixture(scope="function")
async def reset_user(db_session: AsyncSession, test_org: Organization) -> User:
    """Create a dedicated user for password reset tests."""
    user = User(
        email=f"reset-user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("OldPassword123!!"),
        full_name="Reset User",
        org_id=test_org.id,
        role="field_engineer",
        email_verified=True,
        token_version=0,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


class TestForgotPassword:
    """Tests for POST /api/v1/auth/forgot-password."""

    @pytest.mark.asyncio
    async def test_forgot_password_existing_email(self, client, reset_user):
        """Existing email returns 200 with generic message."""
        # Clear rate limit state for this email
        _reset_request_tracker.pop(reset_user.email, None)

        with patch("app.api.v1.password_reset.send_password_reset_email") as mock_send:
            resp = await client.post(
                "/api/v1/auth/forgot-password",
                json={"email": reset_user.email},
            )
            assert resp.status_code == 200
            assert "reset link" in resp.json()["detail"].lower()
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_forgot_password_nonexistent_email(self, client):
        """Non-existing email should still return 200 (prevent enumeration)."""
        fake_email = f"nonexistent-{uuid.uuid4().hex[:8]}@example.com"
        _reset_request_tracker.pop(fake_email, None)

        resp = await client.post(
            "/api/v1/auth/forgot-password",
            json={"email": fake_email},
        )
        assert resp.status_code == 200
        assert "reset link" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_rate_limiting(self, client, reset_user):
        """More than 3 requests per 15min for same email should return 429."""
        email = reset_user.email
        _reset_request_tracker.pop(email, None)

        with patch("app.api.v1.password_reset.send_password_reset_email"):
            for _ in range(_RESET_REQUEST_LIMIT):
                resp = await client.post(
                    "/api/v1/auth/forgot-password",
                    json={"email": email},
                )
                assert resp.status_code == 200

            # 4th request should be rate limited
            resp = await client.post(
                "/api/v1/auth/forgot-password",
                json={"email": email},
            )
            assert resp.status_code == 429

        _reset_request_tracker.pop(email, None)


class TestResetPassword:
    """Tests for POST /api/v1/auth/reset-password."""

    @pytest.mark.asyncio
    async def test_reset_with_valid_token(self, client, db_session, reset_user):
        """Valid token should reset password and increment token_version."""
        token = _create_reset_token(
            user_id=str(reset_user.id),
            email=reset_user.email,
            token_version=reset_user.token_version or 0,
        )

        resp = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": "NewSecurePass1!!"},
        )
        assert resp.status_code == 200
        assert "reset successfully" in resp.json()["detail"].lower()

        # Verify token_version was incremented
        await db_session.refresh(reset_user)
        assert reset_user.token_version == 1

    @pytest.mark.asyncio
    async def test_reset_with_expired_token(self, client, reset_user):
        """Expired reset token should be rejected."""
        payload = {
            "sub": str(reset_user.id),
            "email": reset_user.email,
            "exp": datetime.now(UTC) - timedelta(hours=2),
            "type": "password_reset",
            "token_version": reset_user.token_version or 0,
            "jti": str(uuid.uuid4()),
            "iss": "constructai",
            "aud": "constructai-api",
        }
        expired_token = jwt.encode(
            payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
        )

        resp = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": expired_token, "new_password": "NewSecurePass1!!"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_reset_with_already_used_token(self, client, db_session, reset_user):
        """A token used once should be rejected on second use."""
        token = _create_reset_token(
            user_id=str(reset_user.id),
            email=reset_user.email,
            token_version=reset_user.token_version or 0,
        )

        # First use
        resp = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": "NewSecurePass1!!"},
        )
        assert resp.status_code == 200

        # Second use — should be rejected
        resp = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": "AnotherPass123!!"},
        )
        assert resp.status_code == 400
        assert "already been used" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_reset_with_invalid_token(self, client):
        """Completely invalid token should be rejected."""
        resp = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": "invalid.token.string", "new_password": "NewSecurePass1!!"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_reset_increments_token_version(self, client, db_session, reset_user):
        """After reset, token_version should be incremented."""
        initial_version = reset_user.token_version or 0

        token = _create_reset_token(
            user_id=str(reset_user.id),
            email=reset_user.email,
            token_version=initial_version,
        )

        await client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": "NewSecurePass1!!"},
        )

        await db_session.refresh(reset_user)
        assert reset_user.token_version == initial_version + 1

    @pytest.mark.asyncio
    async def test_reset_with_wrong_token_version(self, client, db_session, reset_user):
        """Token bound to old token_version should be rejected after password change."""
        # Create token with current version
        token = _create_reset_token(
            user_id=str(reset_user.id),
            email=reset_user.email,
            token_version=0,
        )

        # Simulate a prior password change that incremented token_version
        reset_user.token_version = 1
        db_session.add(reset_user)
        await db_session.flush()

        resp = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": token, "new_password": "NewSecurePass1!!"},
        )
        assert resp.status_code == 400
        assert "invalidated" in resp.json()["detail"].lower()


class TestResetTokenHelpers:
    """Tests for the token helper functions."""

    def test_create_reset_token_returns_decodable_jwt(self):
        user_id = str(uuid.uuid4())
        email = "test@example.com"
        token = _create_reset_token(user_id, email)
        payload = _decode_reset_token(token)
        assert payload is not None
        assert payload["sub"] == user_id
        assert payload["email"] == email
        assert payload["type"] == "password_reset"
        assert "jti" in payload

    def test_decode_wrong_type_returns_none(self):
        """Token with wrong type should return None."""
        payload = {
            "sub": str(uuid.uuid4()),
            "email": "test@example.com",
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "type": "access",  # wrong type
            "jti": str(uuid.uuid4()),
            "iss": "constructai",
            "aud": "constructai-api",
        }
        token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        assert _decode_reset_token(token) is None

    def test_decode_invalid_string_returns_none(self):
        assert _decode_reset_token("not-a-jwt") is None
