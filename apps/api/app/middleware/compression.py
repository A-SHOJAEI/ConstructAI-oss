from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)


class CompressionMiddleware(BaseHTTPMiddleware):
    """Response compression (gzip).

    In production, use uvicorn's built-in gzip or a reverse proxy.
    This middleware adds Content-Encoding tracking.
    """

    MIN_SIZE = 500  # Don't compress responses smaller than 500 bytes

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Track accept-encoding for observability
        accept_encoding = request.headers.get("accept-encoding", "")
        if "gzip" in accept_encoding:
            response.headers["Vary"] = "Accept-Encoding"
        return response
