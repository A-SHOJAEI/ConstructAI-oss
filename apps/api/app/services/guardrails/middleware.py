"""FastAPI middleware for guardrails enforcement."""

from __future__ import annotations

import logging

from fastapi import Request, Response
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)

logger = logging.getLogger(__name__)

# Paths that bypass guardrails
EXEMPT_PATHS = {
    "/api/v1/health",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    "/docs",
    "/openapi.json",
}


class GuardrailsMiddleware(BaseHTTPMiddleware):
    """Guardrails enforcement middleware.

    Primary guardrails are applied at the service level for granularity.
    This middleware serves as a safety net - it logs warnings for any
    agent-related responses that did not pass through the guardrails pipeline.
    """

    # Agent-related path segments that should have guardrails applied
    _AGENT_SEGMENTS = (
        "orchestrator",
        "intelligence",
        "rfis/auto-resolve",
        "rfis/draft-response",
        "ask",
    )

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Process request through guardrails if applicable."""
        path = request.url.path

        # Skip exempt paths
        if path in EXEMPT_PATHS:
            return await call_next(request)

        response = await call_next(request)

        # Log warning for agent endpoints that may have bypassed guardrails
        if (
            path.startswith("/api/v1/")
            and any(segment in path for segment in self._AGENT_SEGMENTS)
            and not getattr(request.state, "guardrails_applied", False)
        ):
            logger.warning(
                "Agent endpoint response may not have passed guardrails",
                extra={"path": path, "method": request.method},
            )

        return response
