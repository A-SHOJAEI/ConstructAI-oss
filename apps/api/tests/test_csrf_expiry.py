"""Tests for CSRF token cookie lifetime."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def csrf_app():
    from fastapi import FastAPI

    from app.middleware.csrf import CSRFMiddleware

    test_app = FastAPI()
    test_app.add_middleware(CSRFMiddleware)

    @test_app.get("/test")
    async def _get():
        return {"ok": True}

    @test_app.post("/test")
    async def _post():
        return {"ok": True}

    return test_app


@pytest.fixture
async def csrf_client(csrf_app):
    transport = ASGITransport(app=csrf_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestCSRFCookieLifetime:
    async def test_csrf_cookie_max_age_is_7_days(self, csrf_client):
        """CSRF cookie should have a 7-day max_age, not 30 minutes."""
        response = await csrf_client.get("/test")
        set_cookie = response.headers.get("set-cookie", "")
        if "csrf_token" in set_cookie:
            # 7 days = 604800 seconds
            assert "604800" in set_cookie, (
                f"Expected CSRF cookie max-age=604800 (7 days), got: {set_cookie}"
            )

    async def test_csrf_cookie_set_on_first_request(self, csrf_client):
        """First GET request should set the CSRF cookie."""
        response = await csrf_client.get("/test")
        cookies = response.cookies
        # Cookie may be in set-cookie header or response cookies
        set_cookie = response.headers.get("set-cookie", "")
        assert "csrf_token" in set_cookie or "csrf_token" in cookies
