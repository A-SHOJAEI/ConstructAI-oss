"""Tests for send_email_with_attachment SMTP helper.

Pin the no-SMTP short-circuit, the MIME composition (HTML body +
optional PDF attachment), the STARTTLS sequence, and the
authenticated-vs-anonymous SMTP paths. SSRF tests for webhooks
are in test_notification_service_ssrf.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.agents.notification_service import send_email_with_attachment


def _make_smtp_mock():
    """Build an SMTP context-manager mock (smtplib.SMTP class)."""
    fake_server = MagicMock()
    fake_smtp_class = MagicMock()
    # smtplib.SMTP() returns an object that supports __enter__/__exit__:
    fake_smtp_instance = MagicMock()
    fake_smtp_instance.__enter__ = MagicMock(return_value=fake_server)
    fake_smtp_instance.__exit__ = MagicMock(return_value=False)
    fake_smtp_class.return_value = fake_smtp_instance
    return fake_smtp_class, fake_server


# =========================================================================
# No SMTP configured -> log and return
# =========================================================================


@pytest.mark.asyncio
async def test_no_smtp_host_short_circuits(caplog):
    """[fallback] SMTP_HOST not set -> log warning and return (no
    crash, no SMTP call)."""
    fake_settings = MagicMock(spec=[])  # no SMTP_HOST attribute

    with (
        caplog.at_level("INFO"),
        patch("app.config.Settings", return_value=fake_settings),
    ):
        await send_email_with_attachment(
            to_email="alice@x.com",
            subject="Test",
            body_html="<p>x</p>",
        )

    # Should log "SMTP not configured":
    assert any("SMTP not configured" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_settings_loading_failure_short_circuits():
    """[robustness] Settings() constructor raises -> fall back to
    no-SMTP path (don't crash on bad config). Pin so missing-config
    failures don't take down the entire brief notification flow."""
    with patch(
        "app.config.Settings",
        side_effect=RuntimeError("bad env"),
    ):
        # Should NOT raise:
        await send_email_with_attachment(
            to_email="alice@x.com",
            subject="x",
            body_html="x",
        )


# =========================================================================
# Configured SMTP — happy path
# =========================================================================


@pytest.mark.asyncio
async def test_smtp_send_html_body_only():
    """No attachment -> single multipart with HTML body."""
    fake_settings = MagicMock()
    fake_settings.SMTP_HOST = "smtp.example.com"
    fake_settings.SMTP_PORT = 587
    fake_settings.SMTP_USER = None
    fake_settings.SMTP_PASSWORD = None
    fake_settings.SMTP_FROM_EMAIL = "noreply@constructai.com"

    fake_smtp_class, fake_server = _make_smtp_mock()

    with (
        patch("app.config.Settings", return_value=fake_settings),
        patch(
            "app.services.agents.notification_service.smtplib.SMTP",
            fake_smtp_class,
        ),
    ):
        await send_email_with_attachment(
            to_email="alice@x.com",
            subject="Hello",
            body_html="<h1>Hi</h1>",
        )

    fake_smtp_class.assert_called_once_with("smtp.example.com", 587)
    fake_server.starttls.assert_called_once()
    fake_server.send_message.assert_called_once()
    # No login() because user/password are None:
    fake_server.login.assert_not_called()


@pytest.mark.asyncio
async def test_smtp_send_with_pdf_attachment():
    """[contract] PDF attachment includes Content-Disposition header
    with filename. Pin so a refactor doesn't break the brief PDF
    workflow."""
    fake_settings = MagicMock()
    fake_settings.SMTP_HOST = "smtp.example.com"
    fake_settings.SMTP_PORT = 587
    fake_settings.SMTP_USER = None
    fake_settings.SMTP_PASSWORD = None
    fake_settings.SMTP_FROM_EMAIL = "noreply@constructai.com"

    fake_smtp_class, fake_server = _make_smtp_mock()

    with (
        patch("app.config.Settings", return_value=fake_settings),
        patch(
            "app.services.agents.notification_service.smtplib.SMTP",
            fake_smtp_class,
        ),
    ):
        await send_email_with_attachment(
            to_email="alice@x.com",
            subject="Brief",
            body_html="<p>see attached</p>",
            attachment_bytes=b"%PDF-fake content",
            attachment_filename="brief_2026.pdf",
        )

    fake_server.send_message.assert_called_once()
    # Inspect the message that was sent:
    msg = fake_server.send_message.call_args.args[0]
    # Should have 2 parts: HTML body + PDF attachment:
    parts = list(msg.walk())
    # walk() includes the multipart wrapper + each part
    pdf_parts = [p for p in parts if p.get_content_type() == "application/pdf"]
    assert len(pdf_parts) == 1
    # Content-Disposition includes the filename:
    cd = pdf_parts[0]["Content-Disposition"]
    assert "brief_2026.pdf" in cd


@pytest.mark.asyncio
async def test_smtp_authenticated_path_calls_login():
    """[contract] When SMTP_USER and SMTP_PASSWORD set, server.login
    is called. Pin: refactor must not skip authentication when
    creds are available."""
    fake_settings = MagicMock()
    fake_settings.SMTP_HOST = "smtp.example.com"
    fake_settings.SMTP_PORT = 587
    fake_settings.SMTP_USER = "user@example.com"
    fake_settings.SMTP_PASSWORD = "secret123"
    fake_settings.SMTP_FROM_EMAIL = "noreply@constructai.com"

    fake_smtp_class, fake_server = _make_smtp_mock()

    with (
        patch("app.config.Settings", return_value=fake_settings),
        patch(
            "app.services.agents.notification_service.smtplib.SMTP",
            fake_smtp_class,
        ),
    ):
        await send_email_with_attachment(
            to_email="alice@x.com",
            subject="Hi",
            body_html="<p>x</p>",
        )

    fake_server.login.assert_called_once_with("user@example.com", "secret123")


@pytest.mark.asyncio
async def test_smtp_partial_creds_no_login():
    """[edge case] SMTP_USER set but SMTP_PASSWORD None -> no login.
    Pin: refactor must NOT call login with a None password (would
    raise TypeError or fail auth oddly)."""
    fake_settings = MagicMock()
    fake_settings.SMTP_HOST = "smtp.example.com"
    fake_settings.SMTP_PORT = 587
    fake_settings.SMTP_USER = "user@example.com"
    fake_settings.SMTP_PASSWORD = None
    fake_settings.SMTP_FROM_EMAIL = "noreply@constructai.com"

    fake_smtp_class, fake_server = _make_smtp_mock()

    with (
        patch("app.config.Settings", return_value=fake_settings),
        patch(
            "app.services.agents.notification_service.smtplib.SMTP",
            fake_smtp_class,
        ),
    ):
        await send_email_with_attachment(
            to_email="alice@x.com",
            subject="Hi",
            body_html="<p>x</p>",
        )

    fake_server.login.assert_not_called()


# =========================================================================
# Failure path
# =========================================================================


@pytest.mark.asyncio
async def test_smtp_failure_logged_and_raised():
    """[contract] SMTP send failure -> log error AND re-raise (so
    upstream orchestrator can catch and record the failure per user).
    Pin: refactor must NOT swallow the exception."""
    fake_settings = MagicMock()
    fake_settings.SMTP_HOST = "smtp.example.com"
    fake_settings.SMTP_PORT = 587
    fake_settings.SMTP_USER = None
    fake_settings.SMTP_PASSWORD = None
    fake_settings.SMTP_FROM_EMAIL = "noreply@constructai.com"

    fake_smtp_class, fake_server = _make_smtp_mock()
    fake_server.send_message.side_effect = ConnectionError("smtp 500")

    with (
        patch("app.config.Settings", return_value=fake_settings),
        patch(
            "app.services.agents.notification_service.smtplib.SMTP",
            fake_smtp_class,
        ),
    ):
        with pytest.raises(ConnectionError, match="smtp 500"):
            await send_email_with_attachment(
                to_email="alice@x.com",
                subject="x",
                body_html="x",
            )


# =========================================================================
# Default values
# =========================================================================


@pytest.mark.asyncio
async def test_smtp_default_port_587():
    """[contract] Default SMTP_PORT=587 (submission port with STARTTLS).
    Pin: refactor must NOT default to 25 (relay) or 465 (TLS-only)."""
    # Use a settings mock that triggers AttributeError for SMTP_PORT
    # so the default kicks in. We achieve this with spec=['SMTP_HOST']:
    fake_settings = MagicMock(spec=["SMTP_HOST"])
    fake_settings.SMTP_HOST = "smtp.example.com"

    fake_smtp_class, _fake_server = _make_smtp_mock()

    with (
        patch("app.config.Settings", return_value=fake_settings),
        patch(
            "app.services.agents.notification_service.smtplib.SMTP",
            fake_smtp_class,
        ),
    ):
        await send_email_with_attachment(
            to_email="alice@x.com",
            subject="x",
            body_html="x",
        )

    # SMTP class called with port=587 default:
    args = fake_smtp_class.call_args.args
    assert args[1] == 587


@pytest.mark.asyncio
async def test_smtp_default_from_email_noreply():
    """[contract] Default From: noreply@constructai.com. Pin so a
    refactor doesn't accidentally set From to a real human."""
    fake_settings = MagicMock(spec=["SMTP_HOST"])
    fake_settings.SMTP_HOST = "smtp.example.com"

    fake_smtp_class, fake_server = _make_smtp_mock()

    with (
        patch("app.config.Settings", return_value=fake_settings),
        patch(
            "app.services.agents.notification_service.smtplib.SMTP",
            fake_smtp_class,
        ),
    ):
        await send_email_with_attachment(
            to_email="alice@x.com",
            subject="x",
            body_html="x",
        )

    msg = fake_server.send_message.call_args.args[0]
    assert msg["From"] == "noreply@constructai.com"
