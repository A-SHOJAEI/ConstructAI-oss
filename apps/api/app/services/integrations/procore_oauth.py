"""Procore OAuth 2.0 Authorization Code flow.

Handles the full lifecycle:
  1. Generate authorization URL with CSRF state token (stored in Redis)
  2. Exchange authorization code for access + refresh tokens
  3. Auto-refresh expired access tokens before API calls
  4. Store / retrieve encrypted tokens from database (multi-tenant)
  5. Disconnect (revoke and delete tokens)
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.procore_connection import ProcoreConnection
from app.services.cache import CacheService
from app.services.security.encryption import FieldEncryptor

logger = logging.getLogger(__name__)

# OAuth state tokens live in Redis for 10 minutes
_STATE_TTL = 600
_STATE_PREFIX = "procore:oauth:state:"

# Access tokens last 2 hours in Procore
_TOKEN_LIFETIME = timedelta(hours=2)

# Refresh buffer — refresh 5 minutes before expiry
_REFRESH_BUFFER = timedelta(minutes=5)


class ProcoreOAuthError(Exception):
    """Raised when an OAuth operation fails."""


# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_cache: CacheService | None = None
_encryptor: FieldEncryptor | None = None


def _get_cache() -> CacheService:
    global _cache
    if _cache is None:
        _cache = CacheService()
    return _cache


def _get_encryptor() -> FieldEncryptor:
    global _encryptor
    if _encryptor is None:
        _encryptor = FieldEncryptor()
    return _encryptor


# ---------------------------------------------------------------------------
# 1. Authorization URL generation
# ---------------------------------------------------------------------------


async def generate_auth_url(user_id: uuid.UUID, org_id: uuid.UUID) -> str:
    """Build the Procore OAuth authorization URL.

    A random state token is stored in Redis (10-min TTL) to prevent CSRF.
    The state encodes both the random nonce and the user/org context.

    Returns the full URL to redirect the user's browser to.
    """
    if not settings.PROCORE_CLIENT_ID:
        raise ProcoreOAuthError("PROCORE_CLIENT_ID not configured")

    state = secrets.token_urlsafe(32)
    cache = _get_cache()

    # Store state → {user_id, org_id} mapping in Redis
    state_data = {"user_id": str(user_id), "org_id": str(org_id)}
    stored = await cache.set(f"{_STATE_PREFIX}{state}", state_data, ttl=_STATE_TTL)
    # SECURITY (H-21): CSRF state storage is mandatory. If Redis is down,
    # we must NOT proceed without CSRF protection — abort the OAuth flow.
    if not stored:
        raise ProcoreOAuthError(
            "Failed to store OAuth CSRF state token (Redis unavailable). "
            "Cannot proceed with OAuth flow without CSRF protection."
        )

    login_url = settings.PROCORE_LOGIN_URL.rstrip("/")
    params = (
        f"response_type=code"
        f"&client_id={quote(settings.PROCORE_CLIENT_ID, safe='')}"
        f"&redirect_uri={quote(settings.PROCORE_REDIRECT_URI, safe='')}"
        f"&state={quote(state, safe='')}"
    )
    return f"{login_url}/oauth/authorize?{params}"


# ---------------------------------------------------------------------------
# 2. Code exchange (callback)
# ---------------------------------------------------------------------------


async def exchange_code(
    code: str,
    state: str,
    db: AsyncSession,
) -> ProcoreConnection:
    """Exchange an authorization code for tokens and persist them.

    Validates the state token against Redis, then calls Procore's token
    endpoint.  The resulting tokens are encrypted and stored in the
    procore_connections table.

    Returns the created/updated ProcoreConnection record.
    """
    if httpx is None:
        raise ProcoreOAuthError("httpx is required for Procore OAuth")

    # Validate state
    cache = _get_cache()
    state_data = await cache.get(f"{_STATE_PREFIX}{state}")
    if state_data is None:
        raise ProcoreOAuthError(
            "Invalid or expired OAuth state token. Please restart the connection flow."
        )

    user_id = uuid.UUID(state_data["user_id"])
    org_id = uuid.UUID(state_data["org_id"])

    # Delete used state token
    await cache.delete(f"{_STATE_PREFIX}{state}")

    # Exchange code for tokens
    login_url = settings.PROCORE_LOGIN_URL.rstrip("/")
    token_url = f"{login_url}/oauth/token"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.PROCORE_CLIENT_ID,
                "client_secret": settings.PROCORE_CLIENT_SECRET,
                "redirect_uri": settings.PROCORE_REDIRECT_URI,
            },
        )

    if resp.status_code != 200:
        logger.error("Procore token exchange failed (%d)", resp.status_code)
        raise ProcoreOAuthError(f"Token exchange failed: {resp.status_code}")

    token_data = resp.json()
    access_token = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    expires_in = token_data.get("expires_in", 7200)
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    # Encrypt tokens before storing
    enc = _get_encryptor()
    access_encrypted = enc.encrypt(access_token)
    refresh_encrypted = enc.encrypt(refresh_token)

    # Upsert: one connection per org
    result = await db.execute(
        select(ProcoreConnection).where(ProcoreConnection.organization_id == org_id)
    )
    conn = result.scalar_one_or_none()

    if conn:
        conn.access_token_encrypted = access_encrypted
        conn.refresh_token_encrypted = refresh_encrypted
        conn.token_expires_at = expires_at
        conn.connected_by_user_id = user_id
        conn.connected_at = datetime.now(UTC)
        conn.sync_status = "connected"
    else:
        conn = ProcoreConnection(
            organization_id=org_id,
            access_token_encrypted=access_encrypted,
            refresh_token_encrypted=refresh_encrypted,
            token_expires_at=expires_at,
            connected_by_user_id=user_id,
            sync_status="connected",
        )
        db.add(conn)

    await db.flush()
    await db.refresh(conn)

    logger.info(
        "Procore OAuth tokens stored for org %s (expires %s)",
        org_id,
        expires_at.isoformat(),
    )
    return conn


# ---------------------------------------------------------------------------
# 3. Token refresh
# ---------------------------------------------------------------------------


async def refresh_access_token(
    conn: ProcoreConnection,
    db: AsyncSession,
) -> str:
    """Refresh the access token using the stored refresh token.

    Decrypts the refresh token, calls Procore's token endpoint, encrypts
    and stores the new tokens, and returns the new plaintext access token.
    """
    if httpx is None:
        raise ProcoreOAuthError("httpx is required for Procore OAuth")

    enc = _get_encryptor()
    refresh_token = enc.decrypt(conn.refresh_token_encrypted)

    login_url = settings.PROCORE_LOGIN_URL.rstrip("/")
    token_url = f"{login_url}/oauth/token"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.PROCORE_CLIENT_ID,
                "client_secret": settings.PROCORE_CLIENT_SECRET,
            },
        )

    if resp.status_code != 200:
        logger.error("Procore token refresh failed (%d)", resp.status_code)
        conn.sync_status = "token_expired"
        await db.flush()
        raise ProcoreOAuthError(
            f"Token refresh failed ({resp.status_code}). "
            "User must re-authorize the Procore connection."
        )

    token_data = resp.json()
    new_access = token_data["access_token"]
    new_refresh = token_data["refresh_token"]
    expires_in = token_data.get("expires_in", 7200)
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    conn.access_token_encrypted = enc.encrypt(new_access)
    conn.refresh_token_encrypted = enc.encrypt(new_refresh)
    conn.token_expires_at = expires_at
    conn.sync_status = "connected"
    await db.flush()

    logger.info(
        "Procore access token refreshed for org %s (expires %s)",
        conn.organization_id,
        expires_at.isoformat(),
    )
    return new_access


# ---------------------------------------------------------------------------
# 4. Get valid access token (auto-refresh if expired)
# ---------------------------------------------------------------------------


async def get_valid_access_token(
    org_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[str, ProcoreConnection]:
    """Get a valid (non-expired) access token for the organization.

    Automatically refreshes if the token is expired or within the
    refresh buffer window.

    Returns (plaintext_access_token, connection_record).
    """
    result = await db.execute(
        select(ProcoreConnection).where(ProcoreConnection.organization_id == org_id)
    )
    conn = result.scalar_one_or_none()
    if conn is None:
        raise ProcoreOAuthError(
            "No Procore connection found for this organization. "
            "Connect via /api/v1/integrations/procore/connect first."
        )

    enc = _get_encryptor()
    now = datetime.now(UTC)

    if conn.token_expires_at and now < (conn.token_expires_at - _REFRESH_BUFFER):
        # Token is still valid
        return enc.decrypt(conn.access_token_encrypted), conn

    # Token expired or about to expire — refresh
    logger.info("Procore token expired/expiring for org %s, refreshing...", org_id)
    new_token = await refresh_access_token(conn, db)
    return new_token, conn


# ---------------------------------------------------------------------------
# 5. Disconnect
# ---------------------------------------------------------------------------


async def disconnect_procore(
    org_id: uuid.UUID,
    db: AsyncSession,
) -> bool:
    """Remove the Procore connection for an organization.

    Deletes the encrypted tokens from the database. Does NOT call
    Procore's revocation endpoint (Procore OAuth does not support
    token revocation — tokens expire naturally).
    """
    result = await db.execute(
        select(ProcoreConnection).where(ProcoreConnection.organization_id == org_id)
    )
    conn = result.scalar_one_or_none()
    if conn is None:
        return False

    await db.delete(conn)
    await db.flush()

    logger.info("Procore connection removed for org %s", org_id)
    return True


# ---------------------------------------------------------------------------
# 6. Connection status
# ---------------------------------------------------------------------------


async def get_connection_status(
    org_id: uuid.UUID,
    db: AsyncSession,
) -> dict | None:
    """Get the current Procore connection status for an organization.

    Returns None if not connected.
    """
    result = await db.execute(
        select(ProcoreConnection).where(ProcoreConnection.organization_id == org_id)
    )
    conn = result.scalar_one_or_none()
    if conn is None:
        return None

    now = datetime.now(UTC)
    token_valid = conn.token_expires_at and now < conn.token_expires_at

    return {
        "connected": True,
        "procore_company_id": conn.procore_company_id,
        "connected_at": conn.connected_at.isoformat() if conn.connected_at else None,
        "connected_by_user_id": str(conn.connected_by_user_id)
        if conn.connected_by_user_id
        else None,
        "last_sync_at": conn.last_sync_at.isoformat() if conn.last_sync_at else None,
        "sync_status": conn.sync_status,
        "token_valid": token_valid,
        "token_expires_at": conn.token_expires_at.isoformat() if conn.token_expires_at else None,
    }
