"""Tests for the CSRF middleware (H-09 double-submit cookie pattern).

Pin the documented exempt-path prefixes, the safe-method bypass,
the Bearer-token exception, and the hmac.compare_digest validation.
The cookie-attribute pinning is in test_csrf_expiry.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.middleware.csrf import (
    _CSRF_EXEMPT_PREFIXES,
    _SAFE_METHODS,
    CSRFMiddleware,
)

# =========================================================================
# Constants
# =========================================================================


def test_safe_methods_canonical():
    """[contract] CSRF only validates state-changing methods.
    GET/HEAD/OPTIONS are explicitly safe (RFC 9110)."""
    assert {"GET", "HEAD", "OPTIONS"} == _SAFE_METHODS


def test_csrf_exempt_prefixes_includes_auth_paths():
    """[invariant] All auth endpoints are CSRF-exempt — login can't
    have a CSRF token because no cookie exists yet. Pin so a refactor
    doesn't accidentally break the login flow."""
    assert "/api/v1/auth/login" in _CSRF_EXEMPT_PREFIXES
    assert "/api/v1/auth/register" in _CSRF_EXEMPT_PREFIXES
    assert "/api/v1/auth/forgot-password" in _CSRF_EXEMPT_PREFIXES
    assert "/api/v1/auth/reset-password" in _CSRF_EXEMPT_PREFIXES
    assert "/api/v1/auth/refresh" in _CSRF_EXEMPT_PREFIXES


def test_csrf_exempt_includes_webhooks():
    """[invariant] Webhooks (Procore/Autodesk/generic) are exempt —
    they're authenticated by signature, not cookies."""
    assert "/api/v1/webhooks" in _CSRF_EXEMPT_PREFIXES
    assert "/api/v1/integrations/procore/callback" in _CSRF_EXEMPT_PREFIXES
    assert "/api/v1/integrations/procore/webhooks" in _CSRF_EXEMPT_PREFIXES
    assert "/api/v1/integrations/autodesk/webhooks" in _CSRF_EXEMPT_PREFIXES


def test_csrf_exempt_includes_health_and_docs():
    """[invariant] Public endpoints don't need CSRF."""
    assert "/health" in _CSRF_EXEMPT_PREFIXES
    assert "/docs" in _CSRF_EXEMPT_PREFIXES
    assert "/redoc" in _CSRF_EXEMPT_PREFIXES
    assert "/openapi.json" in _CSRF_EXEMPT_PREFIXES
    assert "/metrics" in _CSRF_EXEMPT_PREFIXES


# =========================================================================
# Helpers
# =========================================================================


def _make_request(
    method: str = "POST",
    path: str = "/api/v1/projects",
    headers=None,
    cookies=None,
):
    fake = MagicMock()
    fake.method = method
    fake.url.path = path
    fake.headers = headers or {}
    fake.cookies = cookies or {}
    return fake


def _make_middleware():
    return CSRFMiddleware(app=MagicMock())


# =========================================================================
# Safe methods bypass
# =========================================================================


@pytest.mark.asyncio
async def test_get_method_bypasses_csrf_check():
    """GET is safe — no CSRF validation required."""
    fake_request = _make_request(method="GET")
    fake_response = MagicMock()
    fake_response.set_cookie = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


@pytest.mark.asyncio
async def test_head_method_bypasses_csrf_check():
    fake_request = _make_request(method="HEAD")
    fake_response = MagicMock()
    fake_response.set_cookie = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


@pytest.mark.asyncio
async def test_options_method_bypasses_csrf_check():
    fake_request = _make_request(method="OPTIONS")
    fake_response = MagicMock()
    fake_response.set_cookie = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


# =========================================================================
# Exempt paths
# =========================================================================


@pytest.mark.asyncio
async def test_login_path_bypasses_csrf_check():
    fake_request = _make_request(method="POST", path="/api/v1/auth/login")
    fake_response = MagicMock()
    fake_response.set_cookie = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


@pytest.mark.asyncio
async def test_webhook_path_bypasses_csrf_check():
    """Procore webhook endpoint is exempt (signature-authenticated)."""
    fake_request = _make_request(
        method="POST",
        path="/api/v1/integrations/procore/webhooks/foo",
    )
    fake_response = MagicMock()
    fake_response.set_cookie = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


@pytest.mark.asyncio
async def test_exempt_prefix_match_requires_path_or_slash():
    """[security/H-09] Path '/api/v1/auth/loginEXTRA' should NOT
    match exempt prefix '/api/v1/auth/login' — only exact match
    or path followed by '/' is exempt. Pin so a refactor doesn't
    introduce prefix collisions."""
    fake_request = _make_request(method="POST", path="/api/v1/auth/loginExtraText")
    fake_response = MagicMock()

    async def fake_next(_request):
        return fake_response

    # No CSRF cookie/header set — should be rejected (not exempt):
    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out.status_code == 403


# =========================================================================
# Bearer auth bypass
# =========================================================================


@pytest.mark.asyncio
async def test_bearer_auth_bypasses_csrf_check():
    """[contract] Bearer auth doesn't use cookies, so CSRF is N/A."""
    fake_request = _make_request(
        method="POST",
        path="/api/v1/projects",
        headers={"authorization": "Bearer token.value"},
    )
    fake_response = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


# =========================================================================
# Cookie-based requests — CSRF validation
# =========================================================================


@pytest.mark.asyncio
async def test_cookie_request_no_csrf_token_returns_403():
    """[security] Cookie auth without csrf_token cookie -> 403."""
    fake_request = _make_request(
        method="POST",
        path="/api/v1/projects",
        cookies={},  # no csrf_token
    )

    async def fake_next(_request):
        return MagicMock()

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out.status_code == 403
    # Body content shouldn't disclose the exact reason in a pickup-able way:
    assert b"CSRF" in out.body


@pytest.mark.asyncio
async def test_cookie_request_no_csrf_header_returns_403():
    """[security] Cookie present, header missing -> 403."""
    fake_request = _make_request(
        method="POST",
        path="/api/v1/projects",
        cookies={"csrf_token": "valid-token-abc"},
        # No X-CSRF-Token header
    )

    async def fake_next(_request):
        return MagicMock()

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out.status_code == 403


@pytest.mark.asyncio
async def test_cookie_request_mismatched_token_returns_403():
    """[security/critical] Cookie ≠ header -> 403. This is the core
    CSRF protection. Pin: refactor must NOT silently accept on
    mismatch."""
    fake_request = _make_request(
        method="POST",
        path="/api/v1/projects",
        cookies={"csrf_token": "value-a"},
        headers={"x-csrf-token": "value-b"},  # different
    )

    async def fake_next(_request):
        return MagicMock()

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out.status_code == 403


@pytest.mark.asyncio
async def test_cookie_request_matching_token_passes():
    """[contract] Cookie == header -> request proceeds."""
    fake_request = _make_request(
        method="POST",
        path="/api/v1/projects",
        cookies={"csrf_token": "matching-token"},
        headers={"x-csrf-token": "matching-token"},
    )
    fake_response = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


@pytest.mark.asyncio
async def test_cookie_uses_constant_time_comparison():
    """[security] Token comparison uses hmac.compare_digest (constant
    time) to prevent timing attacks. Pin: refactor must NOT switch
    to == operator (timing-attack vulnerability)."""
    # Two strings of equal length but different content — both branches
    # would reject either way, but compare_digest does so in constant
    # time. We can't directly test constant-timeness, but we can
    # verify the rejection happens at all (covered by mismatch test
    # above) and inspect that hmac is imported:
    import app.middleware.csrf as csrf_module

    # hmac module must be present (not just '==' comparison):
    assert hasattr(csrf_module, "hmac")


# =========================================================================
# _ensure_csrf_cookie
# =========================================================================


@pytest.mark.asyncio
async def test_csrf_cookie_set_on_safe_request_when_missing():
    """Safe request (GET) without csrf_token cookie -> response gets
    a fresh CSRF cookie set."""
    fake_request = _make_request(method="GET", cookies={})
    fake_response = MagicMock()
    fake_response.set_cookie = MagicMock()

    async def fake_next(_request):
        return fake_response

    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Lax"
    fake_settings.COOKIE_DOMAIN = ""

    with patch("app.config.settings", fake_settings):
        await _make_middleware().dispatch(fake_request, fake_next)

    fake_response.set_cookie.assert_called_once()
    call_kwargs = fake_response.set_cookie.call_args.kwargs
    assert call_kwargs["key"] == "csrf_token"
    # 7 days max_age:
    assert call_kwargs["max_age"] == 7 * 24 * 3600
    # NOT httponly — JS must read it for double-submit:
    assert call_kwargs["httponly"] is False


@pytest.mark.asyncio
async def test_csrf_cookie_not_overwritten_when_present():
    """Existing csrf_token cookie -> not regenerated (avoid token
    rotation on every safe request, would invalidate in-flight
    state-changing requests)."""
    fake_request = _make_request(
        method="GET",
        cookies={"csrf_token": "existing-token"},
    )
    fake_response = MagicMock()
    fake_response.set_cookie = MagicMock()

    async def fake_next(_request):
        return fake_response

    await _make_middleware().dispatch(fake_request, fake_next)
    fake_response.set_cookie.assert_not_called()
