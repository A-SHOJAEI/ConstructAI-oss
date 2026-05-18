"""Tests for rate limiter middleware constants.

Pin the documented EXEMPT_PATHS, _AUTH_STRICT_PATHS,
_WEBHOOK_PATHS, and the per-bucket rate limits. Backend logic is
in test_rate_limiter_backend.py.
"""

from __future__ import annotations

from app.middleware.rate_limiter import RateLimiter

# =========================================================================
# Class-level constants — pin documented exemptions and limits
# =========================================================================


def test_exempt_paths_canonical():
    """[security] Pin the 6 documented public paths exempt from rate
    limiting (health/docs/openapi/metrics). Refactor must NOT add
    a new exemption silently — could open DoS surface."""
    expected = {
        "/health",
        "/api/v1/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/metrics",
    }
    assert expected == RateLimiter.EXEMPT_PATHS


def test_auth_strict_paths_canonical():
    """[security] Auth endpoints get stricter limits to mitigate
    brute-force. Pin the 6 documented auth paths."""
    expected = {
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
        "/api/v1/auth/mfa/verify",
        "/api/v1/auth/sso/",
    }
    assert set(RateLimiter._AUTH_STRICT_PATHS) == expected


def test_auth_strict_limit_10_per_window():
    """[security] 10 requests/window for auth — generous enough for
    legitimate retries (typo password) but tight enough to block
    brute-force. Pin: refactor must NOT raise this without explicit
    review."""
    assert RateLimiter._AUTH_LIMIT == 10


def test_webhook_paths_canonical():
    """[security/M-17] Webhook paths are NOT fully exempt — they get
    a higher rate limit instead. Pin: refactor must NOT add a new
    webhook path to EXEMPT_PATHS (would bypass DoS protection)."""
    assert RateLimiter._WEBHOOK_PATHS == ("/api/v1/webhooks/procore",)


def test_webhook_limit_high_for_legitimate_traffic():
    """[contract] Webhook paths get 1000 req/min — high enough for
    real Procore traffic (bursts during sync) but capped to prevent
    flooding."""
    assert RateLimiter._WEBHOOK_LIMIT == 1000


def test_webhook_paths_not_in_exempt_paths():
    """[security/M-17] Webhook paths must NOT be in EXEMPT_PATHS.
    Pin so the high-limit path is enforced, not skipped."""
    for webhook in RateLimiter._WEBHOOK_PATHS:
        assert webhook not in RateLimiter.EXEMPT_PATHS


def test_auth_paths_not_in_exempt_paths():
    """[security] Auth paths must NOT be exempt — they get the
    AUTH_LIMIT (10) instead. Pin so brute-force protection cannot
    be silently disabled by adding the path to EXEMPT_PATHS."""
    for auth_path in RateLimiter._AUTH_STRICT_PATHS:
        assert auth_path not in RateLimiter.EXEMPT_PATHS


def test_metrics_path_in_exempt():
    """[invariant] /metrics in EXEMPT_PATHS so Prometheus scraping
    isn't rate-limited (would break monitoring)."""
    assert "/metrics" in RateLimiter.EXEMPT_PATHS


def test_constants_are_classvar():
    """[contract] EXEMPT_PATHS, _AUTH_STRICT_PATHS, _WEBHOOK_PATHS
    are class-level (not instance) so they can be checked without
    instantiation. Pin so tools like 'allow this path' decorators
    can introspect."""
    # Access via class (not instance) — would AttributeError if
    # they were instance vars set in __init__:
    _ = RateLimiter.EXEMPT_PATHS
    _ = RateLimiter._AUTH_STRICT_PATHS
    _ = RateLimiter._WEBHOOK_PATHS


def test_auth_limit_lower_than_webhook_limit():
    """[invariant] Auth limit (10) << webhook limit (1000) —
    refactor must not invert these. Auth is per-IP (small),
    webhook is per-source (high-volume integration)."""
    assert RateLimiter._AUTH_LIMIT < RateLimiter._WEBHOOK_LIMIT
    assert RateLimiter._AUTH_LIMIT < 100  # well below default tenant limit
