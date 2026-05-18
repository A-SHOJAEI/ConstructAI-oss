from __future__ import annotations

import logging
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

# Static headers that don't change per request
STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "0",
    # SECURITY [L-05]: Added preload directive for HSTS preload list eligibility.
    "Strict-Transport-Security": ("max-age=31536000; includeSubDomains; preload"),
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # SECURITY [L-13]: Allow microphone from same origin for voice recording.
    "Permissions-Policy": ("camera=(), microphone=(self), geolocation=()"),
}

# Keep DEFAULT_HEADERS for backward compatibility (tests/imports that reference it)
DEFAULT_HEADERS = {
    **STATIC_HEADERS,
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "font-src 'self'"
    ),
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses with per-request CSP nonce."""

    def __init__(self, app, extra_headers: dict | None = None):
        super().__init__(app)
        self.static_headers = {**STATIC_HEADERS}
        if extra_headers:
            self.static_headers.update(extra_headers)

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Apply static headers
        for key, value in self.static_headers.items():
            response.headers[key] = value

        # Generate per-request nonce for CSP style-src
        nonce = secrets.token_urlsafe(16)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            f"style-src 'self' 'nonce-{nonce}'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "frame-ancestors 'none'"
        )
        # Nonce passed via header for SSR frameworks that need it for inline styles.
        # This is not a security concern since style nonces cannot execute scripts.
        response.headers["X-Style-Nonce"] = nonce

        return response
