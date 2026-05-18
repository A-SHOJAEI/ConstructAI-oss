from __future__ import annotations

import collections
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.services.observability.metrics import record_http_request

logger = logging.getLogger(__name__)


class ResponseProfiler(BaseHTTPMiddleware):
    """Profile response times and detect N+1 query patterns."""

    SLOW_THRESHOLD_MS = 500
    N_PLUS_ONE_THRESHOLD = 10  # queries per request

    def __init__(self, app, slow_threshold_ms: int = 500):
        super().__init__(app)
        self.slow_threshold_ms = slow_threshold_ms
        self._request_stats: collections.deque[dict] = collections.deque(maxlen=1000)

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        request.state.query_count = 0

        response = await call_next(request)

        duration_ms = (time.monotonic() - start) * 1000
        query_count = getattr(request.state, "query_count", 0)

        response.headers["X-Response-Time"] = f"{duration_ms:.0f}ms"

        if duration_ms > self.slow_threshold_ms:
            logger.warning(
                "Slow response: %s %s took %.0fms (%d queries)",
                request.method,
                request.url.path,
                duration_ms,
                query_count,
            )

        if query_count > self.N_PLUS_ONE_THRESHOLD:
            logger.warning(
                "Possible N+1: %s %s made %d queries",
                request.method,
                request.url.path,
                query_count,
            )

        self._request_stats.append(
            {
                "method": request.method,
                "path": request.url.path,
                "duration_ms": round(duration_ms, 2),
                "query_count": query_count,
            }
        )

        # Record Prometheus HTTP request metric
        record_http_request(
            method=request.method,
            endpoint=request.url.path,
            status=response.status_code,
        )

        return response

    def get_stats(self) -> dict:
        """Get profiling statistics."""
        if not self._request_stats:
            return {"total_requests": 0}
        durations = [s["duration_ms"] for s in self._request_stats]
        durations.sort()
        return {
            "total_requests": len(self._request_stats),
            "avg_ms": round(sum(durations) / len(durations), 2),
            "p95_ms": round(durations[int(len(durations) * 0.95)], 2) if durations else 0,
            "p99_ms": round(durations[int(len(durations) * 0.99)], 2) if durations else 0,
            "slow_count": sum(1 for d in durations if d > self.slow_threshold_ms),
        }
