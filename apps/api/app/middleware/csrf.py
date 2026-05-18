"""CSRF protection middleware for cookie-based authentication.

Uses the double-submit cookie pattern: a non-httpOnly ``csrf_token`` cookie
is set, and state-changing requests that use cookie auth must include the
matching value in an ``X-CSRF-Token`` header.

Requests that carry a ``Bearer`` Authorization header are exempt because
they don't rely on ambient cookie credentials.
"""

from __future__ import annotations

import hmac
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Paths that are exempt from CSRF (e.g. login, where no cookie exists yet)
_CSRF_EXEMPT_PREFIXES = (
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/verify-email",
    "/api/v1/auth/resend-verification",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/auth/mfa/verify",
    "/api/v1/auth/sso/exchange",
    # /refresh authenticates via the refresh token in body or httpOnly cookie;
    # the refresh token itself is the integrity check, so CSRF is redundant.
    "/api/v1/auth/refresh",
    "/api/v1/webhooks",
    "/api/v1/integrations/procore/callback",
    "/api/v1/integrations/procore/webhooks",
    "/api/v1/integrations/autodesk/webhooks",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Safe methods and exempt paths skip CSRF check
        if request.method in _SAFE_METHODS:
            response = await call_next(request)
            self._ensure_csrf_cookie(request, response)
            return response

        # SECURITY (H-09): Match exact path or path followed by '/' to
        # prevent prefix collisions (e.g. /health vs /healthcheck).
        if any(
            request.url.path == p or request.url.path.startswith(p + "/")
            for p in _CSRF_EXEMPT_PREFIXES
        ):
            response = await call_next(request)
            self._ensure_csrf_cookie(request, response)
            return response

        # Requests with Bearer auth header don't use cookies, skip CSRF
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            return await call_next(request)

        # Cookie-based request: validate CSRF token
        csrf_cookie = request.cookies.get("csrf_token")
        csrf_header = request.headers.get("x-csrf-token")

        if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
            return JSONResponse(
                {"detail": "CSRF token missing or invalid"},
                status_code=403,
            )

        response = await call_next(request)
        return response

    def _ensure_csrf_cookie(self, request: Request, response: Response) -> None:
        """Set a CSRF cookie if one doesn't exist yet."""
        if "csrf_token" not in request.cookies:
            from app.config import settings

            response.set_cookie(
                key="csrf_token",
                value=secrets.token_urlsafe(32),
                httponly=False,  # JS must read this value
                secure=settings.COOKIE_SECURE,
                samesite=settings.COOKIE_SAMESITE,
                domain=settings.COOKIE_DOMAIN or None,
                path="/",
                max_age=7 * 24 * 3600,  # 7 days: CSRF cookie outlives access token
            )
