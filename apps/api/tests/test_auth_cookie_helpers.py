"""Tests for auth cookie helpers (_set_auth_cookies / _clear_auth_cookies).

Pin httpOnly + Secure + SameSite attributes (security-critical for
session cookies), the documented max-age values from settings, and
the path scoping (refresh_token only sent to /api/v1/auth).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.api.v1.auth import _clear_auth_cookies, _set_auth_cookies

# =========================================================================
# _set_auth_cookies
# =========================================================================


def test_set_auth_cookies_sets_both_tokens():
    """[contract] Both access_token and refresh_token cookies are set
    in a single call. Pin: refactor must not silently drop one."""
    response = MagicMock()
    response.set_cookie = MagicMock()

    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Strict"
    fake_settings.COOKIE_DOMAIN = ""
    fake_settings.COOKIE_PATH = "/"
    fake_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
    fake_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7

    with patch("app.api.v1.auth.settings", fake_settings):
        _set_auth_cookies(response, "access.jwt", "refresh.jwt")

    # Two set_cookie calls — one per token:
    assert response.set_cookie.call_count == 2
    keys = {call.kwargs["key"] for call in response.set_cookie.call_args_list}
    assert keys == {"access_token", "refresh_token"}


def test_set_auth_cookies_access_token_attributes():
    """[security] access_token must be httpOnly + Secure + SameSite.
    Pin so a refactor doesn't accidentally drop a security flag
    (XSS / CSRF risk)."""
    response = MagicMock()
    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Lax"
    fake_settings.COOKIE_DOMAIN = ""
    fake_settings.COOKIE_PATH = "/"
    fake_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
    fake_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7

    with patch("app.api.v1.auth.settings", fake_settings):
        _set_auth_cookies(response, "access.jwt", "refresh.jwt")

    access_call = next(
        c for c in response.set_cookie.call_args_list if c.kwargs["key"] == "access_token"
    )
    assert access_call.kwargs["httponly"] is True
    assert access_call.kwargs["secure"] is True
    assert access_call.kwargs["samesite"] == "Lax"


def test_set_auth_cookies_access_token_max_age_minutes_to_seconds():
    """[contract] access_token max_age = ACCESS_TOKEN_EXPIRE_MINUTES * 60.
    Pin: 30 min -> 1800s. Refactor must NOT confuse minutes/seconds."""
    response = MagicMock()
    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Lax"
    fake_settings.COOKIE_DOMAIN = ""
    fake_settings.COOKIE_PATH = "/"
    fake_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
    fake_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7

    with patch("app.api.v1.auth.settings", fake_settings):
        _set_auth_cookies(response, "x", "y")

    access_call = next(
        c for c in response.set_cookie.call_args_list if c.kwargs["key"] == "access_token"
    )
    assert access_call.kwargs["max_age"] == 30 * 60


def test_set_auth_cookies_refresh_token_max_age_days_to_seconds():
    """[contract] refresh_token max_age = REFRESH_TOKEN_EXPIRE_DAYS * 86400.
    Pin: 7 days -> 604800s."""
    response = MagicMock()
    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Lax"
    fake_settings.COOKIE_DOMAIN = ""
    fake_settings.COOKIE_PATH = "/"
    fake_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
    fake_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7

    with patch("app.api.v1.auth.settings", fake_settings):
        _set_auth_cookies(response, "x", "y")

    refresh_call = next(
        c for c in response.set_cookie.call_args_list if c.kwargs["key"] == "refresh_token"
    )
    assert refresh_call.kwargs["max_age"] == 7 * 86400


def test_set_auth_cookies_refresh_token_path_scoped_to_auth():
    """[security/H-XX] refresh_token path is hard-coded to '/api/v1/auth'
    so it only travels with auth/refresh requests — limits exposure
    surface. Pin: refactor must NOT broaden to '/' (would send
    refresh token on every request)."""
    response = MagicMock()
    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Lax"
    fake_settings.COOKIE_DOMAIN = ""
    fake_settings.COOKIE_PATH = "/"
    fake_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
    fake_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7

    with patch("app.api.v1.auth.settings", fake_settings):
        _set_auth_cookies(response, "x", "y")

    refresh_call = next(
        c for c in response.set_cookie.call_args_list if c.kwargs["key"] == "refresh_token"
    )
    assert refresh_call.kwargs["path"] == "/api/v1/auth"


def test_set_auth_cookies_access_token_path_from_settings():
    """[contract] access_token path uses configurable COOKIE_PATH
    (defaults to '/' so it's sent on all API requests)."""
    response = MagicMock()
    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Lax"
    fake_settings.COOKIE_DOMAIN = ""
    fake_settings.COOKIE_PATH = "/api/v1"
    fake_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
    fake_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7

    with patch("app.api.v1.auth.settings", fake_settings):
        _set_auth_cookies(response, "x", "y")

    access_call = next(
        c for c in response.set_cookie.call_args_list if c.kwargs["key"] == "access_token"
    )
    assert access_call.kwargs["path"] == "/api/v1"


def test_set_auth_cookies_empty_domain_passed_as_none():
    """[contract] Empty COOKIE_DOMAIN is converted to None for
    Starlette (which would otherwise emit Domain="" - invalid)."""
    response = MagicMock()
    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Lax"
    fake_settings.COOKIE_DOMAIN = ""  # empty
    fake_settings.COOKIE_PATH = "/"
    fake_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
    fake_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7

    with patch("app.api.v1.auth.settings", fake_settings):
        _set_auth_cookies(response, "x", "y")

    access_call = next(
        c for c in response.set_cookie.call_args_list if c.kwargs["key"] == "access_token"
    )
    assert access_call.kwargs["domain"] is None


def test_set_auth_cookies_explicit_domain_passed_through():
    response = MagicMock()
    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Lax"
    fake_settings.COOKIE_DOMAIN = "constructai.com"
    fake_settings.COOKIE_PATH = "/"
    fake_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
    fake_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7

    with patch("app.api.v1.auth.settings", fake_settings):
        _set_auth_cookies(response, "x", "y")

    access_call = next(
        c for c in response.set_cookie.call_args_list if c.kwargs["key"] == "access_token"
    )
    assert access_call.kwargs["domain"] == "constructai.com"


def test_set_auth_cookies_token_values_pass_through():
    response = MagicMock()
    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Lax"
    fake_settings.COOKIE_DOMAIN = ""
    fake_settings.COOKIE_PATH = "/"
    fake_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
    fake_settings.REFRESH_TOKEN_EXPIRE_DAYS = 7

    with patch("app.api.v1.auth.settings", fake_settings):
        _set_auth_cookies(response, "secret-access-jwt", "secret-refresh-jwt")

    by_key = {c.kwargs["key"]: c.kwargs["value"] for c in response.set_cookie.call_args_list}
    assert by_key["access_token"] == "secret-access-jwt"
    assert by_key["refresh_token"] == "secret-refresh-jwt"


# =========================================================================
# _clear_auth_cookies
# =========================================================================


def test_clear_auth_cookies_deletes_both():
    response = MagicMock()
    response.delete_cookie = MagicMock()

    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Lax"
    fake_settings.COOKIE_DOMAIN = ""
    fake_settings.COOKIE_PATH = "/"

    with patch("app.api.v1.auth.settings", fake_settings):
        _clear_auth_cookies(response)

    assert response.delete_cookie.call_count == 2
    keys = {c.kwargs["key"] for c in response.delete_cookie.call_args_list}
    assert keys == {"access_token", "refresh_token"}


def test_clear_auth_cookies_preserves_path_scoping():
    """[security] When clearing, must use the SAME path as set —
    otherwise the cookie won't actually clear in the browser."""
    response = MagicMock()
    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Lax"
    fake_settings.COOKIE_DOMAIN = ""
    fake_settings.COOKIE_PATH = "/api/v1"

    with patch("app.api.v1.auth.settings", fake_settings):
        _clear_auth_cookies(response)

    # access_token deleted at COOKIE_PATH:
    access_call = next(
        c for c in response.delete_cookie.call_args_list if c.kwargs["key"] == "access_token"
    )
    assert access_call.kwargs["path"] == "/api/v1"
    # refresh_token deleted at /api/v1/auth (matches set):
    refresh_call = next(
        c for c in response.delete_cookie.call_args_list if c.kwargs["key"] == "refresh_token"
    )
    assert refresh_call.kwargs["path"] == "/api/v1/auth"


def test_clear_auth_cookies_keeps_security_flags():
    """[security] Delete must include httpOnly/Secure/SameSite —
    Starlette uses these to match the cookie being deleted."""
    response = MagicMock()
    fake_settings = MagicMock()
    fake_settings.COOKIE_SECURE = True
    fake_settings.COOKIE_SAMESITE = "Strict"
    fake_settings.COOKIE_DOMAIN = ""
    fake_settings.COOKIE_PATH = "/"

    with patch("app.api.v1.auth.settings", fake_settings):
        _clear_auth_cookies(response)

    for call in response.delete_cookie.call_args_list:
        assert call.kwargs["httponly"] is True
        assert call.kwargs["secure"] is True
        assert call.kwargs["samesite"] == "Strict"
