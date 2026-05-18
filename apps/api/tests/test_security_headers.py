from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.middleware.security_headers import (
    DEFAULT_HEADERS,
    SecurityHeadersMiddleware,
)


class TestSecurityHeaders:
    def test_default_headers_defined(self):
        assert "X-Content-Type-Options" in DEFAULT_HEADERS
        assert "X-Frame-Options" in DEFAULT_HEADERS
        assert "Strict-Transport-Security" in DEFAULT_HEADERS
        assert "Content-Security-Policy" in DEFAULT_HEADERS

    def test_x_frame_options_deny(self):
        assert DEFAULT_HEADERS["X-Frame-Options"] == "DENY"

    def test_hsts_includes_subdomains(self):
        hsts = DEFAULT_HEADERS["Strict-Transport-Security"]
        assert "includeSubDomains" in hsts

    def test_csp_default_self(self):
        csp = DEFAULT_HEADERS["Content-Security-Policy"]
        assert "default-src 'self'" in csp

    def test_middleware_init(self):
        mw = SecurityHeadersMiddleware(app=None)
        assert len(mw.static_headers) >= 5


# --- CSP Nonce Tests ---


@pytest.fixture
async def nonce_test_app():
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.add_middleware(SecurityHeadersMiddleware)

    @test_app.get("/test")
    async def _test_endpoint():
        return {"ok": True}

    return test_app


@pytest.fixture
async def nonce_client(nonce_test_app):
    transport = ASGITransport(app=nonce_test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestCSPNonce:
    async def test_csp_contains_nonce_not_unsafe_inline(self, nonce_client):
        response = await nonce_client.get("/test")
        csp = response.headers.get("Content-Security-Policy", "")
        assert "'unsafe-inline'" not in csp
        assert "nonce-" in csp

    async def test_each_request_gets_unique_nonce(self, nonce_client):
        resp1 = await nonce_client.get("/test")
        resp2 = await nonce_client.get("/test")
        nonce1 = resp1.headers.get("X-Style-Nonce", "")
        nonce2 = resp2.headers.get("X-Style-Nonce", "")
        assert nonce1 and nonce2
        assert nonce1 != nonce2

    async def test_x_style_nonce_header_present(self, nonce_client):
        response = await nonce_client.get("/test")
        nonce = response.headers.get("X-Style-Nonce")
        assert nonce is not None
        assert len(nonce) > 10

    async def test_csp_nonce_matches_header(self, nonce_client):
        response = await nonce_client.get("/test")
        csp = response.headers.get("Content-Security-Policy", "")
        nonce = response.headers.get("X-Style-Nonce", "")
        assert f"'nonce-{nonce}'" in csp

    async def test_static_headers_still_present(self, nonce_client):
        response = await nonce_client.get("/test")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert "max-age=31536000" in response.headers.get("Strict-Transport-Security", "")

    async def test_csp_has_all_directives(self, nonce_client):
        response = await nonce_client.get("/test")
        csp = response.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "style-src 'self'" in csp
        assert "img-src 'self' data: blob:" in csp
        assert "font-src 'self'" in csp
