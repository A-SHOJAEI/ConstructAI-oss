"""Email service abstraction with SMTP and console implementations."""

from __future__ import annotations

import contextlib
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings

logger = logging.getLogger(__name__)


def _redact_email(email: str) -> str:
    """Redact an email address for safe logging."""
    local, _, domain = email.partition("@")
    return f"{local[0]}***@{domain}" if local else "***"


# Jinja2 template environment — loaded once
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates" / "email"
_jinja_env: Environment | None = None


def _get_jinja_env() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
        )
    return _jinja_env


def render_template(template_name: str, **context: object) -> str:
    """Render a Jinja2 email template with the given context."""
    env = _get_jinja_env()
    tmpl = env.get_template(template_name)
    return tmpl.render(**context)


class EmailService:
    """Abstract base for email sending."""

    def send(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        text: str | None = None,
    ) -> bool:
        """Send an email. Returns True on success."""
        raise NotImplementedError


class SMTPEmailService(EmailService):
    """Send emails via SMTP (works with SES, SendGrid, Postmark, Gmail)."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_address: str,
        from_name: str,
        use_tls: bool = True,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_address = from_address
        self.from_name = from_name
        self.use_tls = use_tls

    def send(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        text: str | None = None,
    ) -> bool:
        recipients = [to] if isinstance(to, str) else to
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{self.from_name} <{self.from_address}>"
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject

        if text:
            msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        server: smtplib.SMTP | None = None
        try:
            server = smtplib.SMTP(self.host, self.port, timeout=10)
            if self.use_tls:
                server.ehlo()
                server.starttls()
                server.ehlo()

            if self.username and self.password:
                server.login(self.username, self.password)

            server.sendmail(self.from_address, recipients, msg.as_string())
            logger.info(
                "Email sent to %s: %s",
                ", ".join(_redact_email(r) for r in recipients),
                subject,
            )
            return True
        except Exception:
            logger.exception(
                "Failed to send email to %s",
                ", ".join(_redact_email(r) for r in recipients),
            )
            return False
        finally:
            if server is not None:
                with contextlib.suppress(Exception):
                    server.quit()


class ConsoleEmailService(EmailService):
    """Dev fallback — logs email content to console."""

    def send(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        text: str | None = None,
    ) -> bool:
        recipients = [to] if isinstance(to, str) else to
        logger.info(
            "[EMAIL] To: %s | Subject: %s | Body length: %d chars",
            ", ".join(recipients),
            subject,
            len(html),
        )
        return True


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

_email_service: EmailService | None = None


def get_email_service() -> EmailService:
    """Return the configured email service (singleton)."""
    global _email_service
    if _email_service is not None:
        return _email_service

    if settings.SMTP_HOST and settings.SMTP_HOST != "":
        _email_service = SMTPEmailService(
            host=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            from_address=settings.EMAIL_FROM_ADDRESS,
            from_name=settings.EMAIL_FROM_NAME,
            use_tls=settings.SMTP_USE_TLS,
        )
        logger.info("Email service: SMTP (%s:%d)", settings.SMTP_HOST, settings.SMTP_PORT)
    else:
        _email_service = ConsoleEmailService()
        logger.info("Email service: Console (no SMTP configured)")

    return _email_service


# --------------------------------------------------------------------------- #
# Convenience helpers
# --------------------------------------------------------------------------- #


def send_verification_email(email: str, token: str) -> bool:
    """Send email verification link."""
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    verification_url = f"{frontend_url}/verify-email?token={token}"

    html = render_template(
        "verification.html",
        verification_url=verification_url,
        app_name=settings.APP_NAME,
    )
    return get_email_service().send(
        to=email,
        subject=f"Verify your email — {settings.APP_NAME}",
        html=html,
        text=f"Verify your email by visiting: {verification_url}",
    )


def send_password_reset_email(email: str, token: str) -> bool:
    """Send password reset link."""
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    reset_url = f"{frontend_url}/reset-password?token={token}"

    html = render_template(
        "password_reset.html",
        reset_url=reset_url,
        app_name=settings.APP_NAME,
        expiry_hours=1,
    )
    return get_email_service().send(
        to=email,
        subject=f"Reset your password — {settings.APP_NAME}",
        html=html,
        text=f"Reset your password by visiting: {reset_url}\nThis link expires in 1 hour.",
    )


def send_safety_alert_email(to: str | list[str], alert: dict) -> bool:
    """Send safety alert notification email."""
    html = render_template(
        "safety_alert.html",
        alert=alert,
        app_name=settings.APP_NAME,
        dashboard_url=f"{settings.FRONTEND_URL.rstrip('/')}/safety",
    )
    severity = alert.get("priority", "alert").replace("_", " ").upper()
    return get_email_service().send(
        to=to,
        subject=f"[{severity}] Safety Alert — {settings.APP_NAME}",
        html=html,
        text=f"Safety alert: {alert.get('description', 'No description')}",
    )
