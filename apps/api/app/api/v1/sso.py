"""SSO (SAML/OIDC/OAuth2) authentication endpoints."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()

# SSO state TTL in seconds (10 minutes)
_SSO_STATE_TTL = 600
_SSO_MAX_STATES = 1000


class SSOProviderConfig(BaseModel):
    provider: str
    client_id: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scopes: list[str] = Field(default_factory=lambda: ["openid", "email", "profile"])


class SSOCallbackResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: dict


# SECURITY [H-04]: SSO state and auth code stores — Redis-backed with in-memory fallback.
# In-memory dicts are only used when Redis is unavailable, which logs a warning.
# In production, Redis is required (see H-02) ensuring state is shared across workers.
_sso_states: dict[str, dict] = {}
_sso_states_lock = asyncio.Lock()

# RT6-AUTH-02: Short-lived authorization code store (code -> tokens, 60s TTL)
_sso_auth_codes: dict[str, dict] = {}
_sso_auth_codes_lock = asyncio.Lock()
# M-3: 60s was too tight — slow networks or user hesitation after SSO
# redirect trigger silent exchange failures and force the user to redo the
# whole SSO flow. 5 min is still safely bounded (code is single-use, bound
# to IP + state).
_SSO_AUTH_CODE_TTL = 300  # seconds

_SSO_STATE_REDIS_PREFIX = "cai:sso_state:"
_SSO_AUTH_CODE_REDIS_PREFIX = "cai:sso_authcode:"


async def _get_redis():
    """Get Redis client from the shared security module."""
    try:
        from app.services.security.redis_state import _get_redis as _redis_fn

        return await _redis_fn()
    except Exception:
        return None


async def _store_sso_state(state: str, data: dict) -> None:
    """Store SSO state in Redis with TTL, falling back to in-memory dict."""
    import json

    r = await _get_redis()
    if r is not None:
        try:
            await r.set(
                f"{_SSO_STATE_REDIS_PREFIX}{state}",
                json.dumps(data),
                ex=_SSO_STATE_TTL,
            )
            return
        except Exception:
            logger.warning(
                "SECURITY [H-04]: Redis unavailable for SSO state store, using in-memory fallback"
            )
    else:
        logger.warning("Redis unavailable for SSO state - using in-memory fallback")
    async with _sso_states_lock:
        _cleanup_expired_states()
        if len(_sso_states) >= _SSO_MAX_STATES:
            raise HTTPException(status_code=429, detail="Too many pending SSO requests")
        _sso_states[state] = {**data, "created_at": time.monotonic()}


async def _pop_sso_state(state: str) -> dict | None:
    """Retrieve and remove SSO state from Redis, falling back to in-memory dict.

    SEC-06: Uses atomic GETDEL (Redis 6.2+) to prevent TOCTOU race conditions.
    Falls back to pipeline GET+DELETE for older Redis versions.
    """
    import json

    r = await _get_redis()
    if r is not None:
        try:
            key = f"{_SSO_STATE_REDIS_PREFIX}{state}"
            # Atomic get-and-delete to prevent TOCTOU race
            try:
                value = await r.getdel(key)  # Redis 6.2+ GETDEL
            except Exception:
                # Fallback for older Redis: use pipeline
                pipe = r.pipeline()
                pipe.get(key)
                pipe.delete(key)
                results = await pipe.execute()
                value = results[0]
            if value:
                return json.loads(value)
            # Not in Redis — also check memory fallback in case of mixed mode
        except Exception:
            logger.warning(
                "SECURITY [H-04]: Redis unavailable for SSO state pop, using in-memory fallback"
            )
    else:
        logger.warning("Redis unavailable for SSO state - using in-memory fallback")
    # In-memory fallback — pop inside the lock to prevent race conditions
    async with _sso_states_lock:
        data = _sso_states.pop(state, None)
    if data and time.monotonic() - data.get("created_at", 0) > _SSO_STATE_TTL:
        return None
    return data


async def _store_sso_auth_code(code: str, data: dict) -> None:
    """Store SSO auth code in Redis with TTL, falling back to in-memory dict."""
    import json

    r = await _get_redis()
    if r is not None:
        try:
            await r.set(
                f"{_SSO_AUTH_CODE_REDIS_PREFIX}{code}",
                json.dumps(data),
                ex=_SSO_AUTH_CODE_TTL,
            )
            return
        except Exception:
            logger.warning(
                "SECURITY [H-04]: Redis unavailable for SSO auth code store, using in-memory fallback"
            )
    else:
        logger.warning("Redis unavailable for SSO state - using in-memory fallback")
    async with _sso_auth_codes_lock:
        now = time.monotonic()
        expired = [
            k for k, v in _sso_auth_codes.items() if now - v["created_at"] > _SSO_AUTH_CODE_TTL
        ]
        for k in expired:
            del _sso_auth_codes[k]
        _sso_auth_codes[code] = {**data, "created_at": now}


async def _pop_sso_auth_code(code: str) -> dict | None:
    """Retrieve and remove SSO auth code from Redis, falling back to in-memory dict.

    SEC-06: Uses atomic GETDEL (Redis 6.2+) to prevent TOCTOU race conditions.
    Falls back to pipeline GET+DELETE for older Redis versions.
    """
    import json

    r = await _get_redis()
    if r is not None:
        try:
            key = f"{_SSO_AUTH_CODE_REDIS_PREFIX}{code}"
            # Atomic get-and-delete to prevent TOCTOU race
            try:
                value = await r.getdel(key)  # Redis 6.2+ GETDEL
            except Exception:
                # Fallback for older Redis: use pipeline
                pipe = r.pipeline()
                pipe.get(key)
                pipe.delete(key)
                results = await pipe.execute()
                value = results[0]
            if value:
                return json.loads(value)
        except Exception:
            logger.warning(
                "SECURITY [H-04]: Redis unavailable for SSO auth code pop, using in-memory fallback"
            )
    else:
        logger.warning("Redis unavailable for SSO state - using in-memory fallback")
    async with _sso_auth_codes_lock:
        now = time.monotonic()
        expired = [
            k for k, v in _sso_auth_codes.items() if now - v["created_at"] > _SSO_AUTH_CODE_TTL
        ]
        for k in expired:
            del _sso_auth_codes[k]
        return _sso_auth_codes.pop(code, None)


class SSOExchangeRequest(BaseModel):
    code: str


# Supported OIDC providers
PROVIDERS: dict[str, dict] = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scopes": ["openid", "email", "profile"],
    },
    "microsoft": {
        "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
        "scopes": ["openid", "email", "profile"],
    },
}


@router.get("/providers")
async def list_sso_providers(
    current_user: User = Depends(get_current_user),
):
    """List available SSO providers."""
    available = []
    if getattr(settings, "GOOGLE_CLIENT_ID", ""):
        available.append(
            {
                "id": "google",
                "name": "Google",
                "enabled": True,
            }
        )
    if getattr(settings, "MICROSOFT_CLIENT_ID", ""):
        available.append(
            {
                "id": "microsoft",
                "name": "Microsoft",
                "enabled": True,
            }
        )
    return {"providers": available}


def _validate_redirect_uri(uri: str) -> str:
    """Validate redirect_uri against FRONTEND_URL to prevent open redirects."""
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    allowed_origin = urlparse(frontend_url)
    parsed = urlparse(uri)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Invalid redirect URI scheme")
    if parsed.hostname != allowed_origin.hostname:
        raise HTTPException(status_code=400, detail="Redirect URI must match FRONTEND_URL domain")
    # RT6-AUTH-10: Also validate port and require path starts with /
    if parsed.port != allowed_origin.port:
        raise HTTPException(status_code=400, detail="Redirect URI port must match FRONTEND_URL")
    if not parsed.path or not parsed.path.startswith("/"):
        raise HTTPException(status_code=400, detail="Redirect URI path must start with /")
    return uri


def _cleanup_expired_states() -> None:
    """Remove expired SSO states to prevent memory leak."""
    now = time.monotonic()
    expired = [k for k, v in _sso_states.items() if now - v.get("created_at", 0) > _SSO_STATE_TTL]
    for k in expired:
        del _sso_states[k]


@router.get("/{provider}/authorize", response_model=None)
async def sso_authorize(
    request: Request,
    provider: str,
    redirect_uri: str = Query(default=""),
):
    """Initiate SSO login flow. Returns redirect URL.

    SEC-04: Rate limited via RateLimiter middleware (_AUTH_STRICT_PATHS covers
    /api/v1/auth/sso/ at 10 req/min per IP). Additional per-IP check below
    prevents SSO state flooding even if the middleware limit is raised.
    """
    # SEC-04 / M-4: Explicit per-IP rate limit on SSO state generation.
    # IP resolution honors TRUSTED_PROXY_IPS so an attacker behind an
    # untrusted proxy can't rotate XFF values to bypass this cap.
    if request and request.client:
        from app.utils.client_ip import resolve_client_ip

        client_ip = resolve_client_ip(request)
        _cleanup_expired_states()
        ip_states = sum(1 for v in _sso_states.values() if v.get("client_ip") == client_ip)
        if ip_states >= 20:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many pending SSO requests from this IP",
            )

    if provider not in PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown SSO provider: {provider}",
        )

    client_id = getattr(settings, f"{provider.upper()}_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SSO provider {provider} not configured",
        )

    # Validate redirect_uri against allowed domain (prevent open redirect)
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    if redirect_uri:
        callback_url = _validate_redirect_uri(redirect_uri)
    else:
        callback_url = f"{frontend_url}/api/v1/auth/sso/{provider}/callback"

    # SECURITY [H-04]: Store SSO state in Redis (shared across workers)
    state = secrets.token_urlsafe(32)
    client_ip = request.client.host if request and request.client else "unknown"
    await _store_sso_state(
        state,
        {
            "provider": provider,
            "redirect_uri": callback_url,
            "client_ip": client_ip,
        },
    )

    config = PROVIDERS[provider]
    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": " ".join(config["scopes"]),
        "redirect_uri": callback_url,
        "state": state,
    }

    authorize_url = f"{config['authorize_url']}?{urlencode(params)}"
    return {"authorize_url": authorize_url, "state": state}


@router.get("/{provider}/callback")
async def sso_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Handle SSO callback after user authorizes."""
    # SECURITY [H-04]: Retrieve SSO state from Redis (shared across workers)
    stored_state = await _pop_sso_state(state)
    if not stored_state or stored_state["provider"] != provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired SSO state",
        )

    if provider not in PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown SSO provider: {provider}",
        )

    config = PROVIDERS[provider]
    client_id = getattr(settings, f"{provider.upper()}_CLIENT_ID", "")
    client_secret = getattr(settings, f"{provider.upper()}_CLIENT_SECRET", "")

    # Exchange code for tokens
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                config["token_url"],
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": stored_state["redirect_uri"],
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code != 200:
                logger.error("SSO token exchange failed: %s", token_resp.text)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to exchange SSO code for tokens",
                )
            tokens = token_resp.json()

            # Get user info
            userinfo_resp = await client.get(
                config["userinfo_url"],
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            if userinfo_resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to fetch user info from SSO provider",
                )
            userinfo = userinfo_resp.json()
    except ImportError as err:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="httpx not installed for SSO",
        ) from err

    # Find or create user
    email = userinfo.get("email", "").lower().strip()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSO provider did not return an email address",
        )

    # Verify provider confirmed the email
    provider_email_verified = userinfo.get("email_verified", False)

    from sqlalchemy import select

    from app.models.user import User
    from app.utils.security import create_access_token, create_refresh_token, hash_password

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        # SSO auto-creation requires a pre-provisioned org with matching SSO domain.
        email_domain = email.split("@")[1].lower() if "@" in email else ""
        if not email_domain:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid email address from SSO provider.",
            )

        from app.models.organization import Organization

        # Match org by sso_domain or email domain in org settings — reject if no match
        org_result = await db.execute(
            select(Organization).where(Organization.sso_domain == email_domain)
        )
        matched_org = org_result.scalar_one_or_none()
        if not matched_org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No organization is configured for SSO with this email domain. "
                "Contact your administrator.",
            )

        # SECURITY [M-07]: SSO auto-creation gated behind SSO_AUTO_CREATE_USERS setting.
        # When disabled (default), unknown users get a "pending approval" response
        # instead of auto-provisioned accounts. This prevents anyone with a matching
        # email domain from self-provisioning access.
        sso_auto_create = getattr(settings, "SSO_AUTO_CREATE_USERS", False)
        if not sso_auto_create:
            logger.warning(
                "SECURITY [M-07]: SSO login attempted by unknown user %s via %s "
                "(org: %s). Auto-creation is disabled. Admin approval required.",
                email,
                provider,
                matched_org.id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account requires administrator approval. "
                "Your SSO login attempt has been logged for admin review.",
            )

        # Use a random unguessable password (SSO users authenticate via provider, not password)
        random_password = secrets.token_urlsafe(48)
        user = User(
            email=email,
            full_name=userinfo.get("name", email.split("@")[0]),
            hashed_password=hash_password(random_password),
            email_verified=bool(provider_email_verified),
            org_id=matched_org.id,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info("Created SSO user: %s via %s (org: %s)", email, provider, matched_org.id)
    elif not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    # RT6-AUTH-07: Reject SSO login if user email is not verified
    if not user.email_verified:
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        fragment_params = urlencode({"sso": "email_unverified"})
        return RedirectResponse(
            url=f"{frontend_url}/login#{fragment_params}",
            status_code=302,
        )

    # AUTH-03: If user has MFA enabled, store MFA token behind an opaque code
    # (same pattern as RT6-AUTH-02) and redirect. The frontend exchanges the
    # code for the actual MFA token via the /exchange endpoint.
    # SECURITY [L-04]: MFA token is no longer exposed in the URL fragment.
    if user.mfa_enabled:
        from app.api.v1.auth import _create_mfa_challenge_token

        mfa_token = _create_mfa_challenge_token(str(user.id), str(user.org_id))
        mfa_code = secrets.token_urlsafe(32)
        await _store_sso_auth_code(
            mfa_code,
            {
                "mfa_token": mfa_token,
                "sso": "mfa_required",
            },
        )
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        fragment_params = urlencode({"mfa_token": mfa_code, "sso": "mfa_required"})
        return RedirectResponse(
            url=f"{frontend_url}/login#{fragment_params}",
            status_code=302,
        )

    # Include full token claims (org_id, token_version) matching normal login flow
    token_data = {
        "sub": str(user.id),
        "org_id": str(user.org_id),
        "token_version": user.token_version or 0,
    }
    access_token = create_access_token(data=token_data)
    refresh_token = create_refresh_token(data=token_data)

    # RT6-AUTH-02: Store tokens behind a short-lived opaque authorization code
    # instead of putting them directly in the URL fragment.
    # SECURITY [H-04]: Store auth code in Redis (shared across workers)
    auth_code = secrets.token_urlsafe(32)
    await _store_sso_auth_code(
        auth_code,
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
        },
    )

    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    fragment_params = urlencode(
        {
            "code": auth_code,
            "sso": "success",
        }
    )
    return RedirectResponse(
        url=f"{frontend_url}/login#{fragment_params}",
        status_code=302,
    )


@router.post("/exchange")
async def sso_exchange(request: SSOExchangeRequest):
    """Exchange a one-time SSO authorization code for access and refresh tokens.

    RT6-AUTH-02: The SSO callback now returns an opaque code instead of tokens
    in the redirect fragment. The frontend calls this endpoint to exchange the
    code for actual tokens.
    """
    # SECURITY [H-04]: Retrieve auth code from Redis (shared across workers)
    token_data = await _pop_sso_auth_code(request.code)

    if token_data is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired authorization code",
        )

    # Handle MFA flow: if the auth code maps to an MFA token, return it
    # (no cookies needed yet — user still needs to complete MFA verification)
    if "mfa_token" in token_data:
        return {"mfa_required": True, "mfa_token": token_data["mfa_token"]}

    from app.api.v1.auth import _set_auth_cookies

    response = JSONResponse({"message": "Login successful"})
    _set_auth_cookies(response, token_data["access_token"], token_data["refresh_token"])
    return response
