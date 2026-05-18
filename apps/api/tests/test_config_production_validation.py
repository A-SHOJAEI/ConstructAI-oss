"""Tests for Settings.validate_production_config — the gate that
prevents deployment with insecure defaults.

This is C-06/C-07/C-08 security hardening: production must refuse to
boot when JWT secrets, encryption keys, or S3 credentials still carry
their dev defaults. Pin every check so a refactor can't quietly drop
one.
"""

from __future__ import annotations

import pytest

from app.config import Settings


def _settings(**overrides) -> Settings:
    """Build a minimally-valid prod-style Settings, overriding the
    field(s) under test."""
    base = {
        "ENVIRONMENT": "production",
        "TESTING": False,
        "JWT_SECRET_KEY": "x" * 64,
        "DATABASE_URL": "postgresql+asyncpg://user:strongpw@db.example.com/cai",
        "PGBOUNCER_URL": "postgresql://user:strongpw@db.example.com:6432/cai",
        "ENCRYPTION_KEY": "y" * 64,
        "S3_ACCESS_KEY": "AKIAEXAMPLE",
        "S3_SECRET_KEY": "secret-real-key-not-minioadmin",
        "REDIS_URL": "rediss://:strongpw@redis.example.com:6379/0",
        "PROCORE_REDIRECT_URI": "https://app.example.com/oauth/callback",
        "FRONTEND_URL": "https://app.example.com",
        "COOKIE_SECURE": True,
        "COOKIE_DOMAIN": ".example.com",
        "COOKIE_SAMESITE": "lax",
        "CORS_ORIGINS": "https://app.example.com",
        "RATE_LIMIT_BACKEND": "redis",
        "MODEL_SIGNATURE_KEY": "z" * 64,
    }
    base.update(overrides)
    return Settings(**base)


def test_valid_prod_config_does_not_raise():
    """Sanity check — the baseline _settings() must pass."""
    s = _settings()
    s.validate_production_config()


# ---- TESTING bypass ------------------------------------------------------


def test_testing_true_in_dev_skips_validation():
    """In dev mode, TESTING=True bypasses the strict checks so unit
    tests don't need fully-rotated production secrets."""
    s = _settings(
        ENVIRONMENT="development",
        TESTING=True,
        JWT_SECRET_KEY="too-short",  # would fail prod validation
    )
    # Must NOT raise — dev/test bypass.
    s.validate_production_config()


def test_testing_true_in_prod_does_not_skip(monkeypatch):
    """[S4]: TESTING=True is honoured ONLY in dev/test environments —
    production with TESTING=True should still validate. Otherwise an
    attacker who flipped TESTING in env could boot a prod node with
    insecure defaults."""
    s = _settings(
        ENVIRONMENT="production",
        TESTING=True,
        JWT_SECRET_KEY="short",
    )
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        s.validate_production_config()


# ---- JWT_SECRET_KEY ------------------------------------------------------


def test_jwt_secret_too_short_rejected():
    s = _settings(JWT_SECRET_KEY="too-short")
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        s.validate_production_config()


def test_jwt_secret_with_insecure_marker_rejected():
    """Even a 64-char string is rejected if it contains "INSECURE",
    "DEV-ONLY", "CHANGEME", or "CHANGE_ME" markers — those are the
    canonical placeholders developers leave behind."""
    for marker in ("INSECURE", "DEV-ONLY", "CHANGEME", "CHANGE_ME"):
        secret = f"{marker}-padded-out-to-32-plus-characters!!"
        s = _settings(JWT_SECRET_KEY=secret)
        with pytest.raises(ValueError, match="insecure marker"):
            s.validate_production_config()


def test_jwt_marker_check_is_case_insensitive():
    s = _settings(JWT_SECRET_KEY="changeme-padded-out-to-32-plus-characters!!")
    with pytest.raises(ValueError, match="insecure marker"):
        s.validate_production_config()


# ---- DATABASE_URL / PGBOUNCER_URL ---------------------------------------


def test_database_url_with_default_credentials_rejected():
    s = _settings(DATABASE_URL="postgresql://constructai:constructai@db/cai")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        s.validate_production_config()


def test_database_url_with_changeme_rejected():
    s = _settings(DATABASE_URL="postgresql://user:CHANGEME@db/cai")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        s.validate_production_config()


def test_pgbouncer_url_with_default_credentials_rejected():
    s = _settings(PGBOUNCER_URL="postgresql://constructai:constructai@bouncer/cai")
    with pytest.raises(ValueError, match="PGBOUNCER_URL"):
        s.validate_production_config()


# ---- ENCRYPTION_KEY ------------------------------------------------------


def test_encryption_key_missing_rejected():
    s = _settings(ENCRYPTION_KEY="")
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        s.validate_production_config()


def test_encryption_key_too_short_rejected():
    s = _settings(ENCRYPTION_KEY="short")
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        s.validate_production_config()


# ---- S3 credentials ------------------------------------------------------


def test_s3_default_minioadmin_access_key_rejected():
    s = _settings(S3_ACCESS_KEY="minioadmin")
    with pytest.raises(ValueError, match="S3 credentials"):
        s.validate_production_config()


def test_s3_default_minioadmin_secret_key_rejected():
    s = _settings(S3_SECRET_KEY="minioadmin")
    with pytest.raises(ValueError, match="S3 credentials"):
        s.validate_production_config()


# ---- REDIS_URL -----------------------------------------------------------


def test_redis_default_url_rejected():
    s = _settings(REDIS_URL="redis://localhost:6379/0")
    with pytest.raises(ValueError, match="REDIS_URL"):
        s.validate_production_config()


def test_redis_empty_rejected():
    s = _settings(REDIS_URL="")
    with pytest.raises(ValueError, match="REDIS_URL"):
        s.validate_production_config()


# ---- HTTPS-only redirects -----------------------------------------------


def test_procore_redirect_must_be_https():
    s = _settings(PROCORE_REDIRECT_URI="http://app.example.com/oauth/callback")
    with pytest.raises(ValueError, match="PROCORE_REDIRECT_URI"):
        s.validate_production_config()


def test_frontend_url_must_be_https():
    s = _settings(FRONTEND_URL="http://app.example.com")
    with pytest.raises(ValueError, match="FRONTEND_URL"):
        s.validate_production_config()


# ---- Cookie hardening ----------------------------------------------------


def test_cookie_secure_required_in_production():
    s = _settings(COOKIE_SECURE=False)
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        s.validate_production_config()


def test_cookie_domain_required_in_production():
    s = _settings(COOKIE_DOMAIN="")
    with pytest.raises(ValueError, match="COOKIE_DOMAIN"):
        s.validate_production_config()


def test_cookie_samesite_none_rejected_in_production():
    """``none`` SameSite is dangerous (cross-site cookies) — production
    must use lax or strict."""
    s = _settings(COOKIE_SAMESITE="none")
    with pytest.raises(ValueError, match="COOKIE_SAMESITE"):
        s.validate_production_config()


# ---- CORS / rate-limit backend ------------------------------------------


def test_cors_origins_wildcard_rejected():
    s = _settings(CORS_ORIGINS="*")
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        s.validate_production_config()


def test_rate_limit_backend_must_be_redis_in_prod():
    s = _settings(RATE_LIMIT_BACKEND="memory")
    with pytest.raises(ValueError, match="RATE_LIMIT_BACKEND"):
        s.validate_production_config()


# ---- MODEL_SIGNATURE_KEY (H-10) -----------------------------------------


def test_model_signature_key_missing_rejected():
    s = _settings(MODEL_SIGNATURE_KEY="")
    with pytest.raises(ValueError, match="MODEL_SIGNATURE_KEY"):
        s.validate_production_config()


def test_model_signature_key_too_short_rejected():
    s = _settings(MODEL_SIGNATURE_KEY="short")
    with pytest.raises(ValueError, match="MODEL_SIGNATURE_KEY"):
        s.validate_production_config()


# ---- Aggregated error reporting -----------------------------------------


def test_validation_collects_all_errors_in_one_raise():
    """Multiple problems should land in a single ValueError so ops see
    every issue at once instead of fixing them one Heisenbug at a
    time."""
    s = _settings(
        JWT_SECRET_KEY="short",
        ENCRYPTION_KEY="",
        S3_ACCESS_KEY="minioadmin",
        REDIS_URL="redis://localhost:6379/0",
    )
    with pytest.raises(ValueError) as exc_info:
        s.validate_production_config()
    msg = str(exc_info.value)
    # All four problems mentioned:
    assert "JWT_SECRET_KEY" in msg
    assert "ENCRYPTION_KEY" in msg
    assert "S3 credentials" in msg
    assert "REDIS_URL" in msg
