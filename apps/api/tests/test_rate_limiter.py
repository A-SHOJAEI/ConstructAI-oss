from __future__ import annotations

from app.middleware.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_rate_limiter_init(self):
        limiter = RateLimiter(app=None, default_limit=100, burst_limit=200)
        assert limiter.default_limit == 100
        assert limiter.burst_limit == 200

    def test_rate_limiter_window(self):
        limiter = RateLimiter(app=None, window_seconds=60)
        assert limiter.window_seconds == 60

    def test_exempt_paths(self):
        assert "/health" in RateLimiter.EXEMPT_PATHS
        assert "/docs" in RateLimiter.EXEMPT_PATHS

    def test_requests_dict_initialized(self):
        limiter = RateLimiter(app=None)
        # Backend choice depends on settings.RATE_LIMIT_BACKEND; only the
        # in-memory backend exposes the `_requests` dict.
        assert hasattr(limiter, "_backend")
        from app.middleware.rate_limiter import MemoryRateLimiterBackend

        if isinstance(limiter._backend, MemoryRateLimiterBackend):
            assert isinstance(limiter._backend._requests, dict)

    def test_custom_limits(self):
        limiter = RateLimiter(
            app=None,
            default_limit=50,
            burst_limit=75,
            window_seconds=30,
        )
        assert limiter.default_limit == 50
        assert limiter.burst_limit == 75
        assert limiter.window_seconds == 30
