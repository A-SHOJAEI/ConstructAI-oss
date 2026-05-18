"""Tests for the email service abstraction."""

import logging
from unittest.mock import MagicMock, patch

from app.services.email.service import (
    ConsoleEmailService,
    SMTPEmailService,
    get_email_service,
    render_template,
    send_password_reset_email,
    send_safety_alert_email,
    send_verification_email,
)


class TestConsoleEmailService:
    """Tests for the console (dev) email backend."""

    def test_send_logs_message(self, caplog):
        svc = ConsoleEmailService()
        with caplog.at_level(logging.INFO):
            svc.send(
                to="test@example.com",
                subject="Hello",
                html="<p>Body</p>",
            )
        assert "test@example.com" in caplog.text
        assert "Hello" in caplog.text

    def test_send_with_list_of_recipients(self, caplog):
        svc = ConsoleEmailService()
        with caplog.at_level(logging.INFO):
            svc.send(
                to=["a@example.com", "b@example.com"],
                subject="Multi",
                html="<p>Body</p>",
            )
        assert "a@example.com" in caplog.text


class TestSMTPEmailService:
    """Tests for the SMTP backend (mocked)."""

    @patch("smtplib.SMTP")
    def test_send_calls_smtp(self, mock_smtp_class):
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        svc = SMTPEmailService(
            host="smtp.test.com",
            port=587,
            username="user",
            password="pass",
            use_tls=True,
            from_address="noreply@test.com",
            from_name="Test",
        )
        svc.send(to="recipient@test.com", subject="Test", html="<p>Hi</p>")

        mock_smtp_class.assert_called_once_with("smtp.test.com", 587, timeout=10)


class TestRenderTemplate:
    """Tests for the Jinja2 template renderer."""

    def test_render_verification_template(self):
        html = render_template("verification.html", verification_url="https://example.com/verify")
        assert "https://example.com/verify" in html
        assert "Verify" in html

    def test_render_password_reset_template(self):
        html = render_template("password_reset.html", reset_url="https://example.com/reset")
        assert "https://example.com/reset" in html

    def test_render_safety_alert_template(self):
        # Template expects an `alert` dict, not flat kwargs.
        html = render_template(
            "safety_alert.html",
            alert={
                "priority": "P1_critical",
                "alert_type": "PPE Violation",
                "description": "Worker without hard hat",
                "location": "Zone A",
                "detected_at": "2025-01-15 14:30",
            },
            dashboard_url="https://example.com/dashboard",
        )
        assert "Worker without hard hat" in html
        assert "Safety Alert" in html


class TestConvenienceHelpers:
    """Tests for send_verification_email, send_password_reset_email, etc."""

    @patch("app.services.email.service.get_email_service")
    def test_send_verification_email(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        send_verification_email("test@example.com", "token123")

        mock_svc.send.assert_called_once()
        call_args = mock_svc.send.call_args
        assert call_args.kwargs["to"] == "test@example.com"
        assert "Verify" in call_args.kwargs["subject"]

    @patch("app.services.email.service.get_email_service")
    def test_send_password_reset_email(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        send_password_reset_email("test@example.com", "resettoken")

        mock_svc.send.assert_called_once()
        assert "test@example.com" in str(mock_svc.send.call_args)

    @patch("app.services.email.service.get_email_service")
    def test_send_safety_alert_email(self, mock_get_svc):
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        send_safety_alert_email(
            ["admin@example.com"],
            {
                "alert_type": "Fall Detection",
                "severity": "critical",
                "description": "Worker fell",
                "zone_name": "Zone B",
                "created_at": "2025-01-15",
                "project_id": "proj-123",
            },
        )

        mock_svc.send.assert_called_once()


class TestGetEmailService:
    """Tests for the factory function."""

    @patch("app.services.email.service.settings")
    def test_returns_console_when_no_smtp_host(self, mock_settings):
        mock_settings.SMTP_HOST = ""
        # Reset singleton
        import app.services.email.service as mod

        mod._email_service = None
        svc = get_email_service()
        assert isinstance(svc, ConsoleEmailService)
        mod._email_service = None  # cleanup

    @patch("app.services.email.service.settings")
    def test_returns_smtp_when_host_configured(self, mock_settings):
        mock_settings.SMTP_HOST = "smtp.example.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_USER = "user"
        mock_settings.SMTP_PASSWORD = "pass"
        mock_settings.SMTP_USE_TLS = True
        mock_settings.EMAIL_FROM_ADDRESS = "noreply@test.com"
        mock_settings.EMAIL_FROM_NAME = "Test"

        import app.services.email.service as mod

        mod._email_service = None
        svc = get_email_service()
        assert isinstance(svc, SMTPEmailService)
        mod._email_service = None  # cleanup
