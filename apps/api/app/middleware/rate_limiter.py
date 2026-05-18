from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import ClassVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class RateLimiterBackend(ABC):
    """Abstract rate limiter backend."""

    @abstractmethod
    async def check_and_increment(self, key: str, window_seconds: int, limit: int) -> int:
        """Return current count after incrementing. If over limit, return count >= limit."""


class MemoryRateLimiterBackend(RateLimiterBackend):
    """In-memory sliding window rate limiter."""

    def __init__(self) -> None:
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    _MAX_KEYS = 10000

    async def check_and_increment(self, key: str, window_seconds: int, limit: int) -> int:
        async with self._lock:
            now = time.monotonic()
            window_start = now - window_seconds
            self._requests[key] = [t for t in self._requests[key] if t > window_start]
            count = len(self._requests[key])
            self._requests[key].append(now)

            # Evict stale keys to prevent unbounded memory growth
            if len(self._requests) > self._MAX_KEYS:
                # Remove keys whose most recent timestamp is oldest first
                sorted_keys = sorted(
                    self._requests.keys(),
                    key=lambda k: self._requests[k][-1] if self._requests[k] else 0,
                )
                # Evict the oldest 10% of keys
                evict_count = len(sorted_keys) // 10 or 1
                for k in sorted_keys[:evict_count]:
                    del self._requests[k]

            return count


class RedisRateLimiterBackend(RateLimiterBackend):
    """Redis-backed sliding window rate limiter using sorted sets."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def check_and_increment(self, key: str, window_seconds: int, limit: int) -> int:
        r = await self._get_redis()
        now = time.time()
        window_start = now - window_seconds
        rl_key = f"ratelimit:{key}"

        pipe = r.pipeline()
        pipe.zremrangebyscore(rl_key, "-inf", window_start)
        pipe.zcard(rl_key)
        pipe.zadd(rl_key, {str(now): now})
        pipe.expire(rl_key, window_seconds + 1)
        results = await pipe.execute()
        count = results[1]  # ZCARD result
        return count


def _create_backend(backend_type: str, redis_url: str) -> RateLimiterBackend:
    if backend_type == "redis":
        return RedisRateLimiterBackend(redis_url)
    return MemoryRateLimiterBackend()


class RateLimiter(BaseHTTPMiddleware):
    """Per-tenant, per-endpoint rate limiting.

    Uses sliding window algorithm with pluggable backend (memory or Redis).
    """

    EXEMPT_PATHS: ClassVar[set[str]] = {
        "/health",
        "/api/v1/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/metrics",
    }

    # SECURITY [M-17]: Webhook paths get a higher rate limit instead of full
    # exemption, to prevent DoS via unauthenticated webhook flood.
    _WEBHOOK_PATHS: ClassVar[tuple[str, ...]] = ("/api/v1/webhooks/procore",)
    _WEBHOOK_LIMIT: ClassVar[int] = 1000  # per minute — high enough for legitimate traffic

    def __init__(
        self,
        app,
        default_limit: int = 100,
        burst_limit: int = 200,
        window_seconds: int = 60,
    ):
        super().__init__(app)
        self.default_limit = default_limit
        self.burst_limit = burst_limit
        self.window_seconds = window_seconds

        from app.config import settings

        self._backend = _create_backend(settings.RATE_LIMIT_BACKEND, settings.REDIS_URL)

    # Auth endpoints get stricter rate limits to mitigate brute-force attacks
    _AUTH_STRICT_PATHS: ClassVar[tuple[str, ...]] = (
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
        "/api/v1/auth/mfa/verify",
        "/api/v1/auth/sso/",
    )
    _AUTH_LIMIT: ClassVar[int] = 10  # max requests per window for auth endpoints

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in self.EXEMPT_PATHS or path.rstrip("/") in self.EXEMPT_PATHS:
            return await call_next(request)

        # SECURITY (H-05): Only trust X-Forwarded-For when the direct
        # connection comes from a trusted reverse proxy.  Otherwise, a client
        # can rotate spoofed IPs to bypass rate limits.
        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            from app.config import settings

            trusted_proxies = {
                ip.strip() for ip in settings.TRUSTED_PROXY_IPS.split(",") if ip.strip()
            }
            if trusted_proxies and client_ip in trusted_proxies:
                candidate = forwarded.split(",")[0].strip()
                try:
                    ipaddress.ip_address(candidate)
                    client_ip = candidate
                except ValueError:
                    pass  # Invalid IP in header — keep direct client_ip
        tenant_id = getattr(request.state, "tenant_id", None) or client_ip

        # SECURITY [M-17]: Apply webhook-specific rate limit instead of exemption
        is_webhook_path = any(path.startswith(p) for p in self._WEBHOOK_PATHS)
        # Apply stricter limits to authentication endpoints
        is_auth_path = any(path.startswith(p) for p in self._AUTH_STRICT_PATHS)
        if is_webhook_path:
            effective_limit = self._WEBHOOK_LIMIT
            rate_key = f"webhook:{client_ip}"
        elif is_auth_path:
            effective_limit = self._AUTH_LIMIT
            rate_key = f"auth:{client_ip}"
        else:
            effective_limit = self.burst_limit
            rate_key = tenant_id

        current_count = await self._backend.check_and_increment(
            rate_key, self.window_seconds, effective_limit
        )

        if current_count >= effective_limit:
            logger.warning("Rate limit exceeded for tenant %s", tenant_id)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={
                    "Retry-After": str(self.window_seconds),
                    "X-RateLimit-Limit": str(effective_limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        remaining = max(0, effective_limit - current_count - 1)
        response.headers["X-RateLimit-Limit"] = str(effective_limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
