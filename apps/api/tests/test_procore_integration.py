"""Comprehensive tests for Procore OAuth integration.

Tests cover:
- OAuth flow (state generation, code exchange, token storage)
- Token refresh (auto-refresh, expired token handling)
- Rate limiting (sliding window)
- Token encryption (encrypt/decrypt round-trip)
- ProcoreAPI methods (companies, projects, RFIs, etc.)
- Error handling (invalid state, API failures)
- Connection status and disconnect
- ProcoreConnection model
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.integrations.procore_api import (
    ProcoreAPI,
    ProcoreBudgetLineItem,
    ProcoreChangeOrder,
    ProcoreCompany,
    ProcoreProject,
    ProcoreRFI,
    ProcoreSubmittal,
    _rate_limit_wait,
    _rate_timestamps,
)
from app.services.integrations.procore_oauth import (
    _STATE_PREFIX,
    ProcoreOAuthError,
    disconnect_procore,
    exchange_code,
    generate_auth_url,
    get_connection_status,
    get_valid_access_token,
    refresh_access_token,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings():
    """Mock settings with Procore config."""
    s = MagicMock()
    s.PROCORE_CLIENT_ID = "test-client-id"
    s.PROCORE_CLIENT_SECRET = "test-client-secret"
    s.PROCORE_REDIRECT_URI = "http://localhost:8000/api/v1/integrations/procore/callback"
    s.PROCORE_LOGIN_URL = "https://login.procore.com"
    s.PROCORE_BASE_URL = "https://sandbox.procore.com"
    s.PROCORE_API_URL = "https://sandbox.procore.com/rest/v1.0"
    s.ENCRYPTION_KEY = "test-encryption-key-minimum-32-characters"
    return s


@pytest.fixture
def mock_cache():
    """Mock CacheService that stores data in-memory."""
    store: dict[str, object] = {}

    cache = AsyncMock()
    cache.get = AsyncMock(side_effect=lambda k: store.get(k))
    cache.set = AsyncMock(side_effect=lambda k, v, ttl=3600: store.update({k: v}) or True)
    cache.delete = AsyncMock(side_effect=lambda k: bool(store.pop(k, None)))
    cache._store = store  # expose for assertions
    return cache


@pytest.fixture
def mock_encryptor():
    """Mock FieldEncryptor that does identity transform (no real encryption)."""
    enc = MagicMock()
    enc.encrypt = MagicMock(side_effect=lambda x: f"ENC:{x}")
    enc.decrypt = MagicMock(side_effect=lambda x: x.replace("ENC:", ""))
    return enc


@pytest.fixture
def mock_db():
    """Mock AsyncSession."""
    db = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def sample_connection(mock_encryptor):
    """Create a sample ProcoreConnection-like mock."""
    conn = MagicMock()
    conn.id = uuid.uuid4()
    conn.organization_id = uuid.uuid4()
    conn.procore_company_id = "12345"
    conn.access_token_encrypted = "ENC:test-access-token"
    conn.refresh_token_encrypted = "ENC:test-refresh-token"
    conn.token_expires_at = datetime.now(UTC) + timedelta(hours=2)
    conn.connected_by_user_id = uuid.uuid4()
    conn.connected_at = datetime.now(UTC)
    conn.last_sync_at = None
    conn.sync_status = "connected"
    return conn


# ---------------------------------------------------------------------------
# Test: OAuth state generation
# ---------------------------------------------------------------------------


class TestOAuthStateGeneration:
    """Tests for generate_auth_url()."""

    async def test_generates_valid_url(self, mock_settings, mock_cache):
        with (
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch("app.services.integrations.procore_oauth._get_cache", return_value=mock_cache),
        ):
            url = await generate_auth_url(uuid.uuid4(), uuid.uuid4())

        assert "login.procore.com/oauth/authorize" in url
        assert "response_type=code" in url
        assert f"client_id={mock_settings.PROCORE_CLIENT_ID}" in url
        assert "state=" in url

    async def test_state_stored_in_cache(self, mock_settings, mock_cache):
        user_id = uuid.uuid4()
        org_id = uuid.uuid4()

        with (
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch("app.services.integrations.procore_oauth._get_cache", return_value=mock_cache),
        ):
            await generate_auth_url(user_id, org_id)

        # Verify state was stored
        mock_cache.set.assert_called_once()
        call_args = mock_cache.set.call_args
        key = call_args[0][0]
        assert key.startswith(_STATE_PREFIX)
        state_data = call_args[0][1]
        assert state_data["user_id"] == str(user_id)
        assert state_data["org_id"] == str(org_id)

    async def test_raises_when_no_client_id(self, mock_settings):
        mock_settings.PROCORE_CLIENT_ID = ""
        with patch("app.services.integrations.procore_oauth.settings", mock_settings):
            with pytest.raises(ProcoreOAuthError, match="CLIENT_ID"):
                await generate_auth_url(uuid.uuid4(), uuid.uuid4())


# ---------------------------------------------------------------------------
# Test: Code exchange
# ---------------------------------------------------------------------------


class TestCodeExchange:
    """Tests for exchange_code()."""

    async def test_exchanges_code_for_tokens(
        self,
        mock_settings,
        mock_cache,
        mock_encryptor,
        mock_db,
    ):
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        state = "test-state-token"

        # Pre-populate cache with state
        mock_cache._store[f"{_STATE_PREFIX}{state}"] = {
            "user_id": str(user_id),
            "org_id": str(org_id),
        }

        # Mock DB query returning no existing connection
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 7200,
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch("app.services.integrations.procore_oauth._get_cache", return_value=mock_cache),
            patch(
                "app.services.integrations.procore_oauth._get_encryptor",
                return_value=mock_encryptor,
            ),
            patch("app.services.integrations.procore_oauth.httpx") as mock_httpx,
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            await exchange_code(code="auth-code", state=state, db=mock_db)

        # Verify tokens were encrypted
        mock_encryptor.encrypt.assert_any_call("new-access-token")
        mock_encryptor.encrypt.assert_any_call("new-refresh-token")
        # Verify connection was added to DB
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called()

    async def test_invalid_state_raises_error(
        self,
        mock_settings,
        mock_cache,
        mock_db,
    ):
        with (
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch("app.services.integrations.procore_oauth._get_cache", return_value=mock_cache),
        ):
            with pytest.raises(ProcoreOAuthError, match="Invalid or expired"):
                await exchange_code(code="code", state="invalid-state", db=mock_db)

    async def test_token_exchange_failure_raises(
        self,
        mock_settings,
        mock_cache,
        mock_encryptor,
        mock_db,
    ):
        state = "test-state"
        mock_cache._store[f"{_STATE_PREFIX}{state}"] = {
            "user_id": str(uuid.uuid4()),
            "org_id": str(uuid.uuid4()),
        }

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "invalid_grant"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch("app.services.integrations.procore_oauth._get_cache", return_value=mock_cache),
            patch(
                "app.services.integrations.procore_oauth._get_encryptor",
                return_value=mock_encryptor,
            ),
            patch("app.services.integrations.procore_oauth.httpx") as mock_httpx,
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            with pytest.raises(ProcoreOAuthError, match="Token exchange failed"):
                await exchange_code(code="bad-code", state=state, db=mock_db)


# ---------------------------------------------------------------------------
# Test: Token refresh
# ---------------------------------------------------------------------------


class TestTokenRefresh:
    """Tests for refresh_access_token() and auto-refresh."""

    async def test_refresh_updates_tokens(
        self,
        mock_settings,
        mock_encryptor,
        mock_db,
        sample_connection,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "refreshed-access",
            "refresh_token": "refreshed-refresh",
            "expires_in": 7200,
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch(
                "app.services.integrations.procore_oauth._get_encryptor",
                return_value=mock_encryptor,
            ),
            patch("app.services.integrations.procore_oauth.httpx") as mock_httpx,
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            token = await refresh_access_token(sample_connection, mock_db)

        assert token == "refreshed-access"
        mock_db.flush.assert_called()

    async def test_refresh_failure_marks_expired(
        self,
        mock_settings,
        mock_encryptor,
        mock_db,
        sample_connection,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "invalid_token"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch(
                "app.services.integrations.procore_oauth._get_encryptor",
                return_value=mock_encryptor,
            ),
            patch("app.services.integrations.procore_oauth.httpx") as mock_httpx,
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            with pytest.raises(ProcoreOAuthError, match="Token refresh failed"):
                await refresh_access_token(sample_connection, mock_db)

        assert sample_connection.sync_status == "token_expired"

    async def test_get_valid_token_returns_fresh(
        self,
        mock_settings,
        mock_encryptor,
        mock_db,
        sample_connection,
    ):
        # Token is still valid (2h from now)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_connection
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch(
                "app.services.integrations.procore_oauth._get_encryptor",
                return_value=mock_encryptor,
            ),
        ):
            token, _conn = await get_valid_access_token(sample_connection.organization_id, mock_db)

        assert token == "test-access-token"

    async def test_get_valid_token_refreshes_expired(
        self,
        mock_settings,
        mock_encryptor,
        mock_db,
        sample_connection,
    ):
        # Set token as expired
        sample_connection.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_connection
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-token",
            "refresh_token": "new-refresh",
            "expires_in": 7200,
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch(
                "app.services.integrations.procore_oauth._get_encryptor",
                return_value=mock_encryptor,
            ),
            patch("app.services.integrations.procore_oauth.httpx") as mock_httpx,
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            token, _conn = await get_valid_access_token(sample_connection.organization_id, mock_db)

        assert token == "new-token"

    async def test_no_connection_raises(self, mock_settings, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.services.integrations.procore_oauth.settings", mock_settings):
            with pytest.raises(ProcoreOAuthError, match="No Procore connection"):
                await get_valid_access_token(uuid.uuid4(), mock_db)


# ---------------------------------------------------------------------------
# Test: Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Tests for the sliding-window rate limiter."""

    @pytest.fixture(autouse=True)
    def _clear_rate_state(self):
        _rate_timestamps.clear()
        # _rate_limit_wait short-circuits to Redis when available; force the
        # in-memory path so the assertions on _rate_timestamps mean something.
        with patch(
            "app.services.integrations.procore_api._check_rate_limit_redis",
            AsyncMock(return_value=None),
        ):
            yield
        _rate_timestamps.clear()

    async def test_tracks_request_timestamps(self):
        org = "test-org"
        await _rate_limit_wait(org)
        assert len(_rate_timestamps.get(org, [])) == 1

    async def test_multiple_requests_tracked(self):
        org = "test-org"
        for _ in range(5):
            await _rate_limit_wait(org)
        assert len(_rate_timestamps.get(org, [])) == 5

    async def test_prunes_old_timestamps(self):
        org = "test-org"
        # Insert old timestamps
        old = time.monotonic() - 4000  # older than 1hr window
        _rate_timestamps[org] = [old, old + 1, old + 2]
        await _rate_limit_wait(org)
        # Old timestamps should be pruned, only new one remains
        assert len(_rate_timestamps.get(org, [])) == 1


# ---------------------------------------------------------------------------
# Test: Token encryption round-trip
# ---------------------------------------------------------------------------


class TestTokenEncryption:
    """Tests for encrypting/decrypting OAuth tokens."""

    def test_encrypt_decrypt_roundtrip(self):
        from app.services.security.encryption import FieldEncryptor

        enc = FieldEncryptor(encryption_key="test-key-for-procore-tokens-32chars!")
        original = "some-oauth-access-token-value"
        encrypted = enc.encrypt(original)

        assert encrypted != original
        assert enc.decrypt(encrypted) == original

    def test_different_encryptions_produce_different_ciphertext(self):
        from app.services.security.encryption import FieldEncryptor

        enc = FieldEncryptor(encryption_key="test-key-for-procore-tokens-32chars!")
        token = "same-token-value"
        enc1 = enc.encrypt(token)
        enc2 = enc.encrypt(token)

        # Per-value random salt means different ciphertext
        assert enc1 != enc2
        # Both decrypt to the same value
        assert enc.decrypt(enc1) == token
        assert enc.decrypt(enc2) == token


# ---------------------------------------------------------------------------
# Test: ProcoreAPI methods
# ---------------------------------------------------------------------------


class TestProcoreAPIMethods:
    """Tests for ProcoreAPI class methods."""

    def _make_api_and_call(self, mock_db, mock_settings, mock_encryptor, sample_connection):
        """Helper: returns (api, context_patches) — patches stay active while context is open."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_connection
        mock_db.execute = AsyncMock(return_value=mock_result)

        return ProcoreAPI(org_id=sample_connection.organization_id, db=mock_db)

    def _mock_http(self, response_data):
        """Helper: build mock httpx client returning given JSON data."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    def _patch_api_client(self, api, mock_client):
        """Replace the ProcoreAPI's persistent _client (created at __init__)
        with the supplied mock so subsequent requests don't hit the network."""
        api._client = mock_client
        # Pre-set token cache so _get_token doesn't try to call OAuth.
        api._access_token = "fake-token"
        api._company_id = 1

    async def test_list_companies(self, mock_db, mock_settings, mock_encryptor, sample_connection):
        api = self._make_api_and_call(mock_db, mock_settings, mock_encryptor, sample_connection)
        mock_client = self._mock_http(
            [
                {"id": 1, "name": "Acme Construction", "is_active": True},
                {"id": 2, "name": "BuildCo", "is_active": True},
            ]
        )

        self._patch_api_client(api, mock_client)
        with (
            patch("app.services.integrations.procore_api.httpx") as mock_httpx,
            patch("app.services.integrations.procore_api.settings", mock_settings),
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch(
                "app.services.integrations.procore_oauth._get_encryptor",
                return_value=mock_encryptor,
            ),
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            companies = await api.list_companies()

        assert len(companies) == 2
        assert isinstance(companies[0], ProcoreCompany)
        assert companies[0].name == "Acme Construction"

    async def test_list_projects(self, mock_db, mock_settings, mock_encryptor, sample_connection):
        api = self._make_api_and_call(mock_db, mock_settings, mock_encryptor, sample_connection)
        mock_client = self._mock_http(
            [
                {"id": 100, "name": "Office Tower", "status": "Active"},
            ]
        )

        self._patch_api_client(api, mock_client)
        with (
            patch("app.services.integrations.procore_api.httpx") as mock_httpx,
            patch("app.services.integrations.procore_api.settings", mock_settings),
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch(
                "app.services.integrations.procore_oauth._get_encryptor",
                return_value=mock_encryptor,
            ),
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            projects = await api.list_projects(company_id=1)

        assert len(projects) == 1
        assert isinstance(projects[0], ProcoreProject)
        assert projects[0].name == "Office Tower"

    async def test_list_rfis(self, mock_db, mock_settings, mock_encryptor, sample_connection):
        api = self._make_api_and_call(mock_db, mock_settings, mock_encryptor, sample_connection)
        mock_client = self._mock_http(
            [
                {"id": 50, "subject": "Foundation Detail", "status": "open"},
            ]
        )

        self._patch_api_client(api, mock_client)
        with (
            patch("app.services.integrations.procore_api.httpx") as mock_httpx,
            patch("app.services.integrations.procore_api.settings", mock_settings),
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch(
                "app.services.integrations.procore_oauth._get_encryptor",
                return_value=mock_encryptor,
            ),
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            rfis = await api.list_rfis(project_id=100, company_id=1)

        assert len(rfis) == 1
        assert isinstance(rfis[0], ProcoreRFI)

    async def test_list_submittals(self, mock_db, mock_settings, mock_encryptor, sample_connection):
        api = self._make_api_and_call(mock_db, mock_settings, mock_encryptor, sample_connection)
        mock_client = self._mock_http(
            [
                {"id": 60, "title": "Concrete Mix Design", "status": "approved"},
            ]
        )

        self._patch_api_client(api, mock_client)
        with (
            patch("app.services.integrations.procore_api.httpx") as mock_httpx,
            patch("app.services.integrations.procore_api.settings", mock_settings),
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch(
                "app.services.integrations.procore_oauth._get_encryptor",
                return_value=mock_encryptor,
            ),
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            submittals = await api.list_submittals(project_id=100, company_id=1)

        assert len(submittals) == 1
        assert isinstance(submittals[0], ProcoreSubmittal)


# ---------------------------------------------------------------------------
# Test: ProcoreAPI error handling
# ---------------------------------------------------------------------------


class TestProcoreAPIErrorHandling:
    """Tests for retry and error handling in ProcoreAPI."""

    async def test_401_triggers_token_refresh_and_retry(
        self,
        mock_settings,
        mock_encryptor,
        mock_db,
        sample_connection,
    ):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_connection
        mock_db.execute = AsyncMock(return_value=mock_result)

        # First call returns 401, second returns 200
        resp_401 = MagicMock()
        resp_401.status_code = 401

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = [{"id": 1, "name": "Co", "is_active": True}]

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[resp_401, resp_200])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.integrations.procore_api.httpx") as mock_httpx,
            patch("app.services.integrations.procore_api.settings", mock_settings),
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch(
                "app.services.integrations.procore_oauth._get_encryptor",
                return_value=mock_encryptor,
            ),
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            api = ProcoreAPI(org_id=sample_connection.organization_id, db=mock_db)
            companies = await api.list_companies()

        assert len(companies) == 1
        # Should have made 2 requests (first 401, retry 200)
        assert mock_client.request.call_count == 2

    async def test_429_retries_after_delay(
        self,
        mock_settings,
        mock_encryptor,
        mock_db,
        sample_connection,
    ):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_connection
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "1"}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = [{"id": 1, "name": "Co", "is_active": True}]

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[resp_429, resp_200])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.integrations.procore_api.httpx") as mock_httpx,
            patch("app.services.integrations.procore_api.settings", mock_settings),
            patch("app.services.integrations.procore_oauth.settings", mock_settings),
            patch(
                "app.services.integrations.procore_oauth._get_encryptor",
                return_value=mock_encryptor,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            api = ProcoreAPI(org_id=sample_connection.organization_id, db=mock_db)
            companies = await api.list_companies()

        assert len(companies) == 1
        mock_sleep.assert_called_with(1)


# ---------------------------------------------------------------------------
# Test: Connection status and disconnect
# ---------------------------------------------------------------------------


class TestConnectionLifecycle:
    """Tests for connection status and disconnect."""

    async def test_status_when_connected(
        self,
        mock_settings,
        mock_db,
        sample_connection,
    ):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_connection
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.services.integrations.procore_oauth.settings", mock_settings):
            status = await get_connection_status(sample_connection.organization_id, mock_db)

        assert status["connected"] is True
        assert status["token_valid"] is True
        assert status["procore_company_id"] == "12345"

    async def test_status_when_not_connected(self, mock_settings, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.services.integrations.procore_oauth.settings", mock_settings):
            status = await get_connection_status(uuid.uuid4(), mock_db)

        assert status is None

    async def test_disconnect_removes_connection(
        self,
        mock_settings,
        mock_db,
        sample_connection,
    ):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_connection
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.services.integrations.procore_oauth.settings", mock_settings):
            result = await disconnect_procore(sample_connection.organization_id, mock_db)

        assert result is True
        mock_db.delete.assert_called_once_with(sample_connection)
        mock_db.flush.assert_called()

    async def test_disconnect_when_not_connected(self, mock_settings, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with patch("app.services.integrations.procore_oauth.settings", mock_settings):
            result = await disconnect_procore(uuid.uuid4(), mock_db)

        assert result is False


# ---------------------------------------------------------------------------
# Test: Pydantic response models
# ---------------------------------------------------------------------------


class TestPydanticModels:
    """Tests for Pydantic response models."""

    def test_procore_company(self):
        c = ProcoreCompany(id=1, name="Test Co")
        assert c.id == 1
        assert c.is_active is True

    def test_procore_project(self):
        p = ProcoreProject(id=100, name="Tower", status="Active")
        assert p.id == 100
        assert p.project_number is None

    def test_procore_rfi(self):
        r = ProcoreRFI(id=50, subject="Foundation question")
        assert r.id == 50
        assert r.status is None

    def test_procore_submittal(self):
        s = ProcoreSubmittal(id=60, title="Mix Design")
        assert s.id == 60

    def test_procore_change_order(self):
        co = ProcoreChangeOrder(id=70, title="Extra Piers", grand_total=185000.0)
        assert co.grand_total == 185000.0

    def test_procore_budget_line_item(self):
        b = ProcoreBudgetLineItem(id=80, cost_code="03", original_budget_amount=6500000.0)
        assert b.cost_code == "03"


# ---------------------------------------------------------------------------
# Test: ProcoreOAuthError
# ---------------------------------------------------------------------------


class TestProcoreOAuthError:
    """Tests for the custom exception."""

    def test_is_exception(self):
        assert issubclass(ProcoreOAuthError, Exception)

    def test_message_preserved(self):
        err = ProcoreOAuthError("test error")
        assert "test error" in str(err)


# ---------------------------------------------------------------------------
# Test: Mock procore_client.py is untouched
# ---------------------------------------------------------------------------


class TestMockClientUntouched:
    """Verify the existing mock procore_client.py was not modified."""

    def test_mock_client_still_exists(self):
        from app.services.procurement.procore_client import ProcoreClient

        client = ProcoreClient()
        assert hasattr(client, "get_projects")
        assert hasattr(client, "get_rfis")
        assert hasattr(client, "get_submittals")
        assert hasattr(client, "get_change_orders")
        assert hasattr(client, "get_budget")
        assert hasattr(client, "sync_cost_data")

    async def test_mock_client_returns_mock_data(self):
        from app.services.procurement.procore_client import ProcoreClient

        client = ProcoreClient()
        projects = await client.get_projects("company-1")
        assert len(projects) == 3
        assert projects[0]["name"] == "Downtown Office Tower"
