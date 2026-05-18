"""Tests for SSO API routes.

Covers: authorize generates state/redirect URL, callback with valid/invalid state,
provider mismatch, auth code exchange, single-use codes, and redirect URI validation.
"""

import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.sso import (
    _SSO_STATE_TTL,
    _pop_sso_auth_code,
    _pop_sso_state,
    _sso_states,
    _store_sso_auth_code,
    _store_sso_state,
    _validate_redirect_uri,
)
from app.config import settings

# =========================================================================
# Redirect URI validation
# =========================================================================


class TestRedirectURIValidation:
    """Tests for _validate_redirect_uri — prevent open redirects."""

    def test_valid_redirect_uri(self):
        """URI matching FRONTEND_URL domain should be accepted."""
        with patch.object(settings, "FRONTEND_URL", "http://localhost:3000"):
            result = _validate_redirect_uri("http://localhost:3000/callback")
            assert result == "http://localhost:3000/callback"

    def test_external_domain_rejected(self):
        """URI pointing to a different domain should be rejected."""
        from fastapi import HTTPException

        with patch.object(settings, "FRONTEND_URL", "http://localhost:3000"):
            with pytest.raises(HTTPException) as exc_info:
                _validate_redirect_uri("https://evil.com/steal-tokens")
            assert exc_info.value.status_code == 400

    def test_mismatched_port_rejected(self):
        """URI with a different port should be rejected."""
        from fastapi import HTTPException

        with patch.object(settings, "FRONTEND_URL", "http://localhost:3000"):
            with pytest.raises(HTTPException) as exc_info:
                _validate_redirect_uri("http://localhost:9999/callback")
            assert exc_info.value.status_code == 400

    def test_invalid_scheme_rejected(self):
        """Non-http/https scheme should be rejected."""
        from fastapi import HTTPException

        with patch.object(settings, "FRONTEND_URL", "http://localhost:3000"):
            with pytest.raises(HTTPException) as exc_info:
                _validate_redirect_uri("javascript://localhost:3000/callback")
            assert exc_info.value.status_code == 400


# =========================================================================
# SSO State management (in-memory path)
# =========================================================================


class TestSSOStateManagement:
    """Tests for SSO state store/pop operations (in-memory fallback)."""

    @pytest.mark.asyncio
    async def test_store_and_pop_state(self):
        """Stored state should be retrievable and consumed."""
        state_key = f"test-state-{uuid.uuid4().hex[:8]}"
        state_data = {"provider": "google", "redirect_uri": "http://localhost:3000/cb"}

        with patch(
            "app.api.v1.sso._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await _store_sso_state(state_key, state_data)
            retrieved = await _pop_sso_state(state_key)

        assert retrieved is not None
        assert retrieved["provider"] == "google"

        # Clean up
        _sso_states.pop(state_key, None)

    @pytest.mark.asyncio
    async def test_pop_removes_state(self):
        """After popping, the state should no longer exist."""
        state_key = f"test-state-pop-{uuid.uuid4().hex[:8]}"

        with patch(
            "app.api.v1.sso._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await _store_sso_state(state_key, {"provider": "google"})
            await _pop_sso_state(state_key)
            second_pop = await _pop_sso_state(state_key)

        assert second_pop is None

    @pytest.mark.asyncio
    async def test_expired_state_returns_none(self):
        """State older than _SSO_STATE_TTL should return None."""
        state_key = f"expired-state-{uuid.uuid4().hex[:8]}"
        _sso_states[state_key] = {
            "provider": "google",
            "created_at": time.monotonic() - _SSO_STATE_TTL - 100,
        }

        with patch(
            "app.api.v1.sso._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await _pop_sso_state(state_key)

        assert result is None
        _sso_states.pop(state_key, None)

    @pytest.mark.asyncio
    async def test_invalid_state_key_returns_none(self):
        """A state key that was never stored should return None."""
        with patch(
            "app.api.v1.sso._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await _pop_sso_state("never-stored-key")
        assert result is None


# =========================================================================
# Auth code exchange (in-memory path)
# =========================================================================


class TestSSOAuthCodeExchange:
    """Tests for SSO authorization code store/exchange."""

    @pytest.mark.asyncio
    async def test_store_and_pop_auth_code(self):
        """Auth code should be retrievable once."""
        code = f"test-code-{uuid.uuid4().hex[:8]}"
        data = {"access_token": "tok_abc", "refresh_token": "ref_xyz"}

        with patch(
            "app.api.v1.sso._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await _store_sso_auth_code(code, data)
            result = await _pop_sso_auth_code(code)

        assert result is not None
        assert result["access_token"] == "tok_abc"

    @pytest.mark.asyncio
    async def test_auth_code_single_use(self):
        """After popping, the code should be consumed and unavailable."""
        code = f"single-use-{uuid.uuid4().hex[:8]}"
        data = {"access_token": "tok_once"}

        with patch(
            "app.api.v1.sso._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await _store_sso_auth_code(code, data)
            first = await _pop_sso_auth_code(code)
            second = await _pop_sso_auth_code(code)

        assert first is not None
        assert second is None

    @pytest.mark.asyncio
    async def test_invalid_auth_code_returns_none(self):
        """Unknown code should return None."""
        with patch(
            "app.api.v1.sso._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await _pop_sso_auth_code("unknown-code")
        assert result is None


# =========================================================================
# SSO API route tests (via client)
# =========================================================================


class TestSSOAuthorizeEndpoint:
    """Tests for GET /{provider}/authorize."""

    @pytest.mark.asyncio
    async def test_authorize_generates_state_and_redirect(self, client, auth_headers):
        """authorize endpoint should return authorize_url and state."""
        with (
            patch.object(settings, "GOOGLE_CLIENT_ID", "fake-client-id"),
            patch(
                "app.api.v1.sso._get_redis",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            resp = await client.get(
                "/api/v1/auth/sso/google/authorize",
                headers=auth_headers,
            )
        # Should return authorize_url and state
        assert resp.status_code == 200
        data = resp.json()
        assert "authorize_url" in data
        assert "state" in data
        assert "accounts.google.com" in data["authorize_url"]

    @pytest.mark.asyncio
    async def test_unknown_provider_rejected(self, client, auth_headers):
        """Unknown SSO provider should return 400."""
        resp = await client.get(
            "/api/v1/auth/sso/unknown_provider/authorize",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_unconfigured_provider_rejected(self, client, auth_headers):
        """Provider with no client_id configured should return 400."""
        with patch.object(settings, "GOOGLE_CLIENT_ID", ""):
            resp = await client.get(
                "/api/v1/auth/sso/google/authorize",
                headers=auth_headers,
            )
        assert resp.status_code == 400


class TestSSOExchangeEndpoint:
    """Tests for POST /exchange."""

    @pytest.mark.asyncio
    async def test_exchange_with_invalid_code(self, client):
        """Invalid/expired authorization code should return 400."""
        resp = await client.post(
            "/api/v1/auth/sso/exchange",
            json={"code": "bogus-code"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_exchange_with_valid_code(self, client):
        """Valid authorization code should return tokens via cookies."""
        code = f"valid-exchange-{uuid.uuid4().hex[:8]}"

        with patch(
            "app.api.v1.sso._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await _store_sso_auth_code(
                code,
                {
                    "access_token": "at_123",
                    "refresh_token": "rt_456",
                },
            )

            resp = await client.post(
                "/api/v1/auth/sso/exchange",
                json={"code": code},
            )

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_exchange_code_consumed_after_use(self, client):
        """Auth code should be single-use — second exchange fails."""
        code = f"consume-test-{uuid.uuid4().hex[:8]}"

        with patch(
            "app.api.v1.sso._get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await _store_sso_auth_code(
                code,
                {
                    "access_token": "at_once",
                    "refresh_token": "rt_once",
                },
            )

            # First exchange succeeds
            resp1 = await client.post(
                "/api/v1/auth/sso/exchange",
                json={"code": code},
            )
            assert resp1.status_code == 200

            # Second exchange fails
            resp2 = await client.post(
                "/api/v1/auth/sso/exchange",
                json={"code": code},
            )
            assert resp2.status_code == 400
