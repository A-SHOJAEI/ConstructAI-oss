"""Tests for the tenant context middleware (H-10 enforcement).

Pin the documented public-path prefixes, the JWT extraction
fallback chain (request.state.org_id -> Authorization header ->
access_token cookie), and the 403 rejection for missing tenant.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.middleware.tenant_context import (
    _PUBLIC_PATH_PREFIXES,
    EXEMPT_PATHS,
    TenantContextMiddleware,
)

# =========================================================================
# Public path constants — pin documented exemptions
# =========================================================================


def test_exempt_paths_canonical_set():
    """[security] Pin all exempt paths. Refactor must NOT silently
    add an exemption (would bypass tenant isolation)."""
    expected = {
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
    }
    assert expected == EXEMPT_PATHS


def test_public_path_prefixes_includes_metrics():
    """[invariant] /metrics is in public prefixes (Prometheus
    scraping doesn't have a JWT). Pin so a refactor doesn't break
    monitoring."""
    assert "/metrics" in _PUBLIC_PATH_PREFIXES


def test_public_path_prefixes_no_metrics_in_exempt_paths():
    """[contract] /metrics is in PUBLIC_PATH_PREFIXES (no 403) but
    NOT in EXEMPT_PATHS (still extracts tenant if available).
    Pin: refactor must not move metrics to EXEMPT_PATHS (would
    skip JWT decode entirely, losing observability)."""
    assert "/metrics" not in EXEMPT_PATHS
    assert "/metrics" in _PUBLIC_PATH_PREFIXES


# =========================================================================
# Helpers
# =========================================================================


def _make_middleware():
    return TenantContextMiddleware(app=MagicMock())


def _make_request(path: str, method: str = "GET", headers=None, cookies=None):
    """Build a fake Starlette Request."""
    fake = MagicMock()
    fake.method = method
    fake.url.path = path
    fake.headers = headers or {}
    fake.cookies = cookies or {}
    fake.state = MagicMock(spec=["org_id"])
    fake.state.org_id = None
    return fake


# =========================================================================
# OPTIONS preflight short-circuit
# =========================================================================


@pytest.mark.asyncio
async def test_options_request_skips_tenant_check():
    """[contract] CORS preflight (OPTIONS) skips tenant validation —
    handled by CORSMiddleware. Pin: refactor must NOT 403 OPTIONS
    (would break browser CORS)."""
    fake_request = _make_request("/api/v1/projects", method="OPTIONS")
    fake_response = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


# =========================================================================
# Exempt paths — bypass tenant check
# =========================================================================


@pytest.mark.asyncio
async def test_health_path_bypasses_tenant_check():
    fake_request = _make_request("/health")
    fake_response = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


@pytest.mark.asyncio
async def test_auth_path_bypasses_tenant_check():
    """/api/v1/auth/login etc. don't need tenant — they CREATE the JWT."""
    fake_request = _make_request("/api/v1/auth/login")
    fake_response = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


@pytest.mark.asyncio
async def test_docs_path_bypasses_tenant_check():
    """OpenAPI docs are public."""
    fake_request = _make_request("/docs")
    fake_response = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


@pytest.mark.asyncio
async def test_procore_webhook_callback_bypasses_tenant_check():
    """[invariant] Procore webhook callbacks don't carry a JWT —
    they're authenticated by webhook signature. Pin so a refactor
    doesn't accidentally 403 webhooks."""
    fake_request = _make_request("/api/v1/integrations/procore/callback")
    fake_response = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


# =========================================================================
# JWT extraction — Authorization header
# =========================================================================


@pytest.mark.asyncio
async def test_extracts_org_id_from_bearer_header():
    """Authorization: Bearer <jwt> -> org_id from JWT claims."""
    fake_request = _make_request(
        "/api/v1/projects",
        headers={"authorization": "Bearer dummy.jwt.token"},
    )
    fake_response = MagicMock()

    async def fake_next(_request):
        return fake_response

    with patch(
        "app.utils.security.decode_access_token",
        return_value={"org_id": "org-xyz", "sub": "user-1"},
    ):
        out = await _make_middleware().dispatch(fake_request, fake_next)

    assert fake_request.state.tenant_id == "org-xyz"
    assert out is fake_response


@pytest.mark.asyncio
async def test_extracts_org_id_from_cookie():
    """[fallback] No Authorization header -> try access_token cookie."""
    fake_request = _make_request(
        "/api/v1/projects",
        cookies={"access_token": "cookie.jwt.token"},
    )
    fake_response = MagicMock()

    async def fake_next(_request):
        return fake_response

    with patch(
        "app.utils.security.decode_access_token",
        return_value={"org_id": "org-cookie"},
    ):
        out = await _make_middleware().dispatch(fake_request, fake_next)

    assert fake_request.state.tenant_id == "org-cookie"
    assert out is fake_response


@pytest.mark.asyncio
async def test_authorization_header_takes_precedence_over_cookie():
    """[contract] Bearer header wins when both are present. Pin so
    a refactor doesn't accidentally use the cookie value."""
    fake_request = _make_request(
        "/api/v1/projects",
        headers={"authorization": "Bearer header.jwt"},
        cookies={"access_token": "cookie.jwt"},
    )

    async def fake_next(_request):
        return MagicMock()

    decode_calls = []

    def fake_decode(token):
        decode_calls.append(token)
        return {"org_id": f"org-{token}"}

    with patch("app.utils.security.decode_access_token", fake_decode):
        await _make_middleware().dispatch(fake_request, fake_next)

    # Header decoded first; cookie decoder NOT called because header succeeded:
    assert decode_calls == ["header.jwt"]
    assert fake_request.state.tenant_id == "org-header.jwt"


# =========================================================================
# H-10 — 403 on missing tenant for non-public paths
# =========================================================================


@pytest.mark.asyncio
async def test_no_tenant_on_protected_path_returns_403():
    """[security/H-10] No JWT, no tenant, non-public path -> 403."""
    fake_request = _make_request("/api/v1/projects")

    async def fake_next(_request):
        return MagicMock()

    out = await _make_middleware().dispatch(fake_request, fake_next)
    # Response is JSONResponse with status_code 403:
    assert out.status_code == 403


@pytest.mark.asyncio
async def test_no_tenant_on_metrics_path_passes_through():
    """[invariant] /metrics is public — no tenant required, no 403.
    Pin: Prometheus scraping must continue working without a JWT."""
    fake_request = _make_request("/metrics")
    fake_response = MagicMock()

    async def fake_next(_request):
        return fake_response

    out = await _make_middleware().dispatch(fake_request, fake_next)
    assert out is fake_response


@pytest.mark.asyncio
async def test_invalid_jwt_on_protected_path_returns_403():
    """[security] Invalid JWT (decode returns None) -> 403."""
    fake_request = _make_request(
        "/api/v1/projects",
        headers={"authorization": "Bearer invalid.token"},
    )

    async def fake_next(_request):
        return MagicMock()

    with patch(
        "app.utils.security.decode_access_token",
        return_value=None,
    ):
        out = await _make_middleware().dispatch(fake_request, fake_next)

    assert out.status_code == 403


# =========================================================================
# request.state.org_id direct path (already set by upstream auth)
# =========================================================================


@pytest.mark.asyncio
async def test_existing_state_org_id_used_directly():
    """[contract] If upstream middleware already set state.org_id,
    use it without re-decoding the JWT. Pin: avoids redundant
    decode work."""
    fake_request = _make_request(
        "/api/v1/projects",
        headers={"authorization": "Bearer some.jwt"},
    )
    # Pre-set by upstream:
    fake_request.state.org_id = "org-from-state"

    decode_calls = []

    def fake_decode(token):
        decode_calls.append(token)
        return None

    async def fake_next(_request):
        return MagicMock()

    with patch("app.utils.security.decode_access_token", fake_decode):
        await _make_middleware().dispatch(fake_request, fake_next)

    # state.org_id was set, so JWT decode shouldn't run:
    assert decode_calls == []
    assert fake_request.state.tenant_id == "org-from-state"


# =========================================================================
# _extract_org_id — exception path
# =========================================================================


@pytest.mark.asyncio
async def test_jwt_decode_exception_returns_none_no_crash():
    """[robustness] decode_access_token raises (e.g., bad lib state) ->
    extractor returns None, falls through to 403 (don't crash)."""
    fake_request = _make_request(
        "/api/v1/projects",
        headers={"authorization": "Bearer x"},
    )

    async def fake_next(_request):
        return MagicMock()

    with patch(
        "app.utils.security.decode_access_token",
        side_effect=RuntimeError("jwt lib explosion"),
    ):
        out = await _make_middleware().dispatch(fake_request, fake_next)

    # Falls through to 403 (no tenant found), no crash:
    assert out.status_code == 403
