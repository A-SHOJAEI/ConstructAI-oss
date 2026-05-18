"""Tests for the structured logger sensitive-key redaction.

The ``_sanitize_log_event`` processor is the gate that prevents
secrets from leaking into log aggregation. It must redact every value
whose key matches the sensitive-key regex — case-insensitive, partial
match (``api_key`` inside ``X_api_key`` should still trigger).
"""

from __future__ import annotations

import structlog

from app.services.observability.structured_logger import (
    _REDACTED,
    _sanitize_log_event,
    bind_correlation_id,
    bind_tenant_context,
    clear_context,
    get_logger,
    setup_logging,
)

# =========================================================================
# _sanitize_log_event — secret redaction
# =========================================================================


def test_redacts_password_field():
    event = {"event": "login", "password": "supersecret"}
    out = _sanitize_log_event(None, "info", event)
    assert out["password"] == _REDACTED
    assert out["event"] == "login"  # non-sensitive preserved


def test_redacts_passwd_alternative():
    event = {"event": "x", "passwd": "raw"}
    assert _sanitize_log_event(None, "info", event)["passwd"] == _REDACTED


def test_redacts_token_field():
    event = {"event": "auth", "token": "eyJabc..."}
    assert _sanitize_log_event(None, "info", event)["token"] == _REDACTED


def test_redacts_secret_field():
    event = {"event": "x", "secret": "abc"}
    assert _sanitize_log_event(None, "info", event)["secret"] == _REDACTED


def test_redacts_authorization_header():
    event = {"event": "request", "authorization": "Bearer eyJ..."}
    assert _sanitize_log_event(None, "info", event)["authorization"] == _REDACTED


def test_redacts_cookie():
    event = {"event": "x", "cookie": "session=abc"}
    assert _sanitize_log_event(None, "info", event)["cookie"] == _REDACTED


def test_redacts_api_key():
    event = {"event": "x", "api_key": "sk_live_123"}
    assert _sanitize_log_event(None, "info", event)["api_key"] == _REDACTED


def test_redacts_access_key():
    event = {"event": "x", "access_key": "AKIA..."}
    assert _sanitize_log_event(None, "info", event)["access_key"] == _REDACTED


def test_redacts_private_key():
    event = {"event": "x", "private_key": "-----BEGIN..."}
    assert _sanitize_log_event(None, "info", event)["private_key"] == _REDACTED


def test_redacts_substring_match():
    """The regex matches anywhere in the key — ``user_password`` must
    still redact."""
    event = {"event": "x", "user_password": "secret"}
    assert _sanitize_log_event(None, "info", event)["user_password"] == _REDACTED


def test_redacts_case_insensitive():
    event = {"event": "x", "PASSWORD": "secret", "Token": "abc"}
    out = _sanitize_log_event(None, "info", event)
    assert out["PASSWORD"] == _REDACTED
    assert out["Token"] == _REDACTED


def test_does_not_redact_unrelated_fields():
    event = {
        "event": "request",
        "user_id": "u-123",
        "request_id": "r-456",
        "method": "POST",
        "url": "/api/x",
    }
    out = _sanitize_log_event(None, "info", event)
    assert out["user_id"] == "u-123"
    assert out["request_id"] == "r-456"
    assert out["method"] == "POST"
    assert out["url"] == "/api/x"


def test_redacts_multiple_sensitive_in_one_event():
    event = {
        "event": "auth",
        "username": "alice",
        "password": "secret1",
        "api_key": "sk_abc",
        "token": "eyJ...",
    }
    out = _sanitize_log_event(None, "info", event)
    assert out["username"] == "alice"  # not sensitive
    assert out["password"] == _REDACTED
    assert out["api_key"] == _REDACTED
    assert out["token"] == _REDACTED


def test_returns_dict_in_place_for_compatibility():
    """structlog processors should return the same dict so the chain
    stays cheap. The helper does this — pin it."""
    event = {"x": 1, "password": "p"}
    out = _sanitize_log_event(None, "info", event)
    assert out is event  # same object, mutated


def test_preserves_non_string_values():
    """Sensitive-key match looks at the KEY only — non-string values
    on non-sensitive keys must pass through unchanged."""
    event = {"count": 42, "items": [1, 2, 3], "metadata": {"a": "b"}}
    out = _sanitize_log_event(None, "info", event)
    assert out["count"] == 42
    assert out["items"] == [1, 2, 3]
    assert out["metadata"] == {"a": "b"}


# =========================================================================
# setup_logging / get_logger / bindings
# =========================================================================


def test_setup_logging_invalid_level_falls_back_to_info():
    """Garbage level should not crash — falls back to INFO."""
    setup_logging(log_level="NOT_A_REAL_LEVEL")
    log = get_logger()
    assert log is not None


def test_setup_logging_lowercase_level_works():
    setup_logging(log_level="debug")
    log = get_logger()
    assert log is not None


def test_get_logger_returns_logger():
    log = get_logger("test")
    assert log is not None
    # Has the standard structlog logging methods:
    assert callable(log.info)
    assert callable(log.warning)
    assert callable(log.error)


def test_bind_correlation_id_adds_to_context():
    clear_context()
    bind_correlation_id("req-123")
    ctx = structlog.contextvars.get_contextvars()
    assert ctx.get("correlation_id") == "req-123"
    clear_context()


def test_bind_tenant_context_adds_org_and_user():
    clear_context()
    bind_tenant_context(org_id="org-1", user_id="user-2")
    ctx = structlog.contextvars.get_contextvars()
    assert ctx.get("org_id") == "org-1"
    assert ctx.get("user_id") == "user-2"
    clear_context()


def test_bind_tenant_context_default_user_id_empty_string():
    clear_context()
    bind_tenant_context(org_id="org-only")
    ctx = structlog.contextvars.get_contextvars()
    assert ctx.get("org_id") == "org-only"
    assert ctx.get("user_id") == ""
    clear_context()


def test_clear_context_removes_bound_vars():
    bind_correlation_id("req-x")
    bind_tenant_context(org_id="org-x", user_id="user-x")
    clear_context()
    ctx = structlog.contextvars.get_contextvars()
    assert "correlation_id" not in ctx
    assert "org_id" not in ctx
    assert "user_id" not in ctx
