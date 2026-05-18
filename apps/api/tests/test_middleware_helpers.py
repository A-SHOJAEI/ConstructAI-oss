"""Tests for small middleware helpers (compression, product_gate, audit).

Pin documented thresholds, the product-route map, and the
dev/test bypass for product gating.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.middleware.audit import AuditContextMiddleware
from app.middleware.compression import CompressionMiddleware
from app.middleware.product_gate import PRODUCT_ROUTE_MAP, require_product

# =========================================================================
# CompressionMiddleware constants
# =========================================================================


def test_compression_min_size_500_bytes():
    """[contract] 500-byte minimum size for compression. Pin so a
    refactor doesn't try to compress tiny responses (HTTP overhead
    > savings) or large responses get skipped."""
    assert CompressionMiddleware.MIN_SIZE == 500


# =========================================================================
# Compression middleware — Vary header
# =========================================================================


@pytest.mark.asyncio
async def test_compression_adds_vary_when_gzip_accepted():
    """When client sends Accept-Encoding: gzip, response gets
    Vary: Accept-Encoding for proxy cache correctness."""
    fake_response = MagicMock()
    fake_response.headers = {}

    async def fake_next(_request):
        return fake_response

    fake_request = MagicMock()
    fake_request.headers = {"accept-encoding": "gzip, deflate"}

    middleware = CompressionMiddleware(app=MagicMock())
    out = await middleware.dispatch(fake_request, fake_next)
    assert out.headers["Vary"] == "Accept-Encoding"


@pytest.mark.asyncio
async def test_compression_no_vary_when_gzip_not_accepted():
    """No gzip in accept-encoding -> no Vary header (pin: refactor
    must NOT add Vary unconditionally — wastes cache slots)."""
    fake_response = MagicMock()
    fake_response.headers = {}

    async def fake_next(_request):
        return fake_response

    fake_request = MagicMock()
    fake_request.headers = {"accept-encoding": "br"}  # no gzip

    middleware = CompressionMiddleware(app=MagicMock())
    out = await middleware.dispatch(fake_request, fake_next)
    assert "Vary" not in out.headers


# =========================================================================
# AuditContextMiddleware — IP extraction
# =========================================================================


def _make_audit_middleware():
    return AuditContextMiddleware(app=MagicMock())


@pytest.mark.asyncio
async def test_audit_uses_direct_ip_when_no_xff():
    """No X-Forwarded-For -> use direct client IP."""
    fake_request = MagicMock()
    fake_request.client.host = "192.0.2.1"
    fake_request.headers = {}
    fake_request.state = MagicMock()

    fake_response = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_audit_middleware().dispatch(fake_request, fake_next)
    assert fake_request.state.client_ip == "192.0.2.1"
    assert out is fake_response


@pytest.mark.asyncio
async def test_audit_no_client_sets_ip_none():
    """No client (e.g., test fixtures) -> client_ip=None (no crash)."""
    fake_request = MagicMock()
    fake_request.client = None
    fake_request.headers = {}
    fake_request.state = MagicMock()

    async def fake_next(_request):
        return MagicMock()

    await _make_audit_middleware().dispatch(fake_request, fake_next)
    assert fake_request.state.client_ip is None


@pytest.mark.asyncio
async def test_audit_uses_xff_when_proxy_trusted():
    """[security/H-06] X-Forwarded-For is honored when direct IP is
    in TRUSTED_PROXY_IPS. Pin so a refactor doesn't blanket-trust
    XFF (spoofing risk)."""
    fake_request = MagicMock()
    fake_request.client.host = "10.0.0.1"  # proxy
    fake_request.headers = {"x-forwarded-for": "203.0.113.42, 10.0.0.1"}
    fake_request.state = MagicMock()

    fake_settings = MagicMock()
    fake_settings.TRUSTED_PROXY_IPS = "10.0.0.1,10.0.0.2"

    async def fake_next(_request):
        return MagicMock()

    with patch("app.config.settings", fake_settings):
        await _make_audit_middleware().dispatch(fake_request, fake_next)

    # First IP from XFF when proxy is trusted:
    assert fake_request.state.client_ip == "203.0.113.42"


@pytest.mark.asyncio
async def test_audit_ignores_xff_when_proxy_not_trusted():
    """[security/H-06] Proxy not in TRUSTED_PROXY_IPS -> XFF ignored,
    use direct IP. Pin: refactor must NOT trust XFF blindly."""
    fake_request = MagicMock()
    fake_request.client.host = "192.0.2.1"  # not in trusted list
    fake_request.headers = {"x-forwarded-for": "203.0.113.42"}
    fake_request.state = MagicMock()

    fake_settings = MagicMock()
    fake_settings.TRUSTED_PROXY_IPS = "10.0.0.1"

    async def fake_next(_request):
        return MagicMock()

    with patch("app.config.settings", fake_settings):
        await _make_audit_middleware().dispatch(fake_request, fake_next)

    assert fake_request.state.client_ip == "192.0.2.1"


@pytest.mark.asyncio
async def test_audit_invalid_xff_falls_back_to_direct_ip():
    """[security] Bogus XFF value (not a valid IP) -> ignore, use
    direct IP."""
    fake_request = MagicMock()
    fake_request.client.host = "10.0.0.1"
    fake_request.headers = {"x-forwarded-for": "not-an-ip"}
    fake_request.state = MagicMock()

    fake_settings = MagicMock()
    fake_settings.TRUSTED_PROXY_IPS = "10.0.0.1"

    async def fake_next(_request):
        return MagicMock()

    with patch("app.config.settings", fake_settings):
        await _make_audit_middleware().dispatch(fake_request, fake_next)

    assert fake_request.state.client_ip == "10.0.0.1"


@pytest.mark.asyncio
async def test_audit_extracts_user_agent():
    fake_request = MagicMock()
    fake_request.client.host = "1.2.3.4"
    fake_request.headers = {"user-agent": "TestClient/1.0"}
    fake_request.state = MagicMock()

    async def fake_next(_request):
        return MagicMock()

    await _make_audit_middleware().dispatch(fake_request, fake_next)
    assert fake_request.state.user_agent == "TestClient/1.0"


@pytest.mark.asyncio
async def test_audit_user_agent_none_when_missing():
    """Missing User-Agent header -> user_agent=None."""
    fake_request = MagicMock()
    fake_request.client.host = "1.2.3.4"
    fake_request.headers = {}
    fake_request.state = MagicMock()

    async def fake_next(_request):
        return MagicMock()

    await _make_audit_middleware().dispatch(fake_request, fake_next)
    assert fake_request.state.user_agent is None


# =========================================================================
# product_gate — PRODUCT_ROUTE_MAP
# =========================================================================


def test_product_route_map_canonical():
    """[contract] Pin the 4 documented product names that map from
    route prefixes. Pin so a refactor doesn't accidentally drop a
    product (would silently allow access to gated features)."""
    expected = {
        "closeout": "closeout_iq",
        "heat": "heatshield",
        "wages": "wageguard",
        "carbon": "carbonlens",
    }
    assert expected == PRODUCT_ROUTE_MAP


# =========================================================================
# require_product — dependency factory
# =========================================================================


@pytest.mark.asyncio
async def test_require_product_dev_environment_is_open():
    """[fallback] Dev environment -> always allow (no Stripe call)."""
    gate = require_product("closeout_iq")
    fake_request = MagicMock()
    fake_db = MagicMock()

    fake_settings = MagicMock()
    fake_settings.ENVIRONMENT = "development"
    fake_settings.TESTING = False

    fake_check = AsyncMock(return_value=False)
    with (
        patch("app.middleware.product_gate.settings", fake_settings),
        patch("app.middleware.product_gate.is_product_enabled", fake_check),
    ):
        # Should NOT raise even though product is disabled:
        await gate(fake_request, fake_db)

    fake_check.assert_not_called()


@pytest.mark.asyncio
async def test_require_product_testing_mode_is_open():
    """[fallback] TESTING=True -> always allow."""
    gate = require_product("closeout_iq")
    fake_request = MagicMock()
    fake_db = MagicMock()

    fake_settings = MagicMock()
    fake_settings.ENVIRONMENT = "production"
    fake_settings.TESTING = True

    fake_check = AsyncMock(return_value=False)
    with (
        patch("app.middleware.product_gate.settings", fake_settings),
        patch("app.middleware.product_gate.is_product_enabled", fake_check),
    ):
        await gate(fake_request, fake_db)

    fake_check.assert_not_called()


@pytest.mark.asyncio
async def test_require_product_no_tenant_raises_403():
    """[security] Missing tenant_id in request state -> 403 (don't
    fall through to public access)."""
    gate = require_product("closeout_iq")
    fake_request = MagicMock()
    fake_request.state = MagicMock(spec=[])  # no tenant_id attribute
    fake_db = MagicMock()

    fake_settings = MagicMock()
    fake_settings.ENVIRONMENT = "production"
    fake_settings.TESTING = False

    with patch("app.middleware.product_gate.settings", fake_settings):
        with pytest.raises(HTTPException) as exc_info:
            await gate(fake_request, fake_db)

    assert exc_info.value.status_code == 403
    assert "Organisation context required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_require_product_disabled_raises_403_with_upgrade_hint():
    """[business] Disabled product -> 403 with upgrade message
    (pin so a refactor doesn't drop the upgrade-prompt UX hint)."""
    gate = require_product("heatshield")
    fake_request = MagicMock()
    fake_request.state.tenant_id = "org-1"
    fake_db = MagicMock()

    fake_settings = MagicMock()
    fake_settings.ENVIRONMENT = "production"
    fake_settings.TESTING = False

    with (
        patch("app.middleware.product_gate.settings", fake_settings),
        patch(
            "app.middleware.product_gate.is_product_enabled",
            AsyncMock(return_value=False),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await gate(fake_request, fake_db)

    assert exc_info.value.status_code == 403
    assert "heatshield" in exc_info.value.detail
    assert "upgrade" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_require_product_enabled_passes():
    """[contract] Enabled product -> no exception (returns None)."""
    gate = require_product("carbonlens")
    fake_request = MagicMock()
    fake_request.state.tenant_id = "org-1"
    fake_db = MagicMock()

    fake_settings = MagicMock()
    fake_settings.ENVIRONMENT = "production"
    fake_settings.TESTING = False

    with (
        patch("app.middleware.product_gate.settings", fake_settings),
        patch(
            "app.middleware.product_gate.is_product_enabled",
            AsyncMock(return_value=True),
        ),
    ):
        out = await gate(fake_request, fake_db)

    assert out is None
