from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Paths that don't require tenant context
EXEMPT_PATHS = {
    "/health",
    "/api/v1/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/auth",
    "/api/v1/webhooks",
    "/api/v1/integrations/procore/callback",
    "/api/v1/integrations/procore/webhooks",
    "/api/v1/integrations/autodesk/webhooks",
    # Demo backup screencasts — operator-only failsafe page, no auth.
    "/api/v1/demo",
}

# SECURITY (H-10): Public paths that are allowed without tenant context.
# All other paths MUST have a valid tenant_id after JWT decode.
_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/api/v1/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/auth",
    "/metrics",
    "/api/v1/webhooks",
    "/api/v1/integrations/procore/callback",
    "/api/v1/integrations/procore/webhooks",
    "/api/v1/integrations/autodesk/webhooks",
    "/api/v1/demo",
)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Extract tenant (org_id) from JWT and set PostgreSQL session variable.

    This enables Row Level Security policies to filter data
    automatically.
    """

    async def dispatch(self, request: Request, call_next):
        # Skip CORS preflight requests — handled by CORSMiddleware
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        # Skip exempt paths
        if any(path == p or path.startswith(p + "/") for p in EXEMPT_PATHS):
            return await call_next(request)

        # Extract org_id from request state or JWT claims
        org_id = getattr(request.state, "org_id", None)
        if not org_id:
            # Try to extract from Authorization header JWT claims
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                org_id = self._extract_org_id(auth_header[7:])
        if not org_id:
            # Try to extract from cookie-based auth (httpOnly access_token)
            token = request.cookies.get("access_token")
            if token:
                org_id = self._extract_org_id(token)

        if org_id:
            request.state.tenant_id = org_id
            logger.debug("Tenant context set: %s", org_id)
        else:
            # SECURITY (H-10): If JWT decode failed or no token was provided,
            # reject the request unless it targets a public path.
            is_public = any(path == p or path.startswith(p + "/") for p in _PUBLIC_PATH_PREFIXES)
            if not is_public:
                logger.warning("Tenant context missing for non-public path: %s", path)
                return JSONResponse(
                    {"detail": "Tenant context required"},
                    status_code=403,
                )

        response = await call_next(request)
        return response

    def _extract_org_id(self, token: str) -> str | None:
        """Extract org_id from JWT token with signature verification."""
        try:
            from app.utils.security import decode_access_token

            payload = decode_access_token(token)
            if payload is None:
                return None
            return payload.get("org_id")
        except Exception:
            return None
