"""Notification service for intelligence brief distribution.

Supports email (with PDF attachment) and webhook notifications,
with per-user preferences stored in the notification_preferences table.
"""

from __future__ import annotations

import html
import ipaddress
import logging
import os
import smtplib
import socket
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Allowed webhook URL schemes
_ALLOWED_WEBHOOK_SCHEMES = {"https"}
# In development, also allow http for localhost
_DEV_MODE = os.environ.get("ENVIRONMENT", "production").lower() in ("development", "dev", "local")


def _validate_webhook_url(url: str) -> str:
    """Validate webhook URL to prevent SSRF attacks.

    Only allows https:// scheme (and http:// for localhost in dev mode).
    Resolves hostname and blocks private/link-local/reserved/loopback IPs.
    Blocks cloud metadata endpoints.

    Raises ValueError if the URL is not allowed.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    allowed = set(_ALLOWED_WEBHOOK_SCHEMES)
    if _DEV_MODE:
        allowed.add("http")

    if scheme not in allowed:
        raise ValueError(f"Unsupported webhook protocol: {scheme}. Only HTTPS is allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Webhook URL must include a hostname")

    # Block well-known cloud metadata endpoints
    _blocked_hosts = {"169.254.169.254", "metadata.google.internal", "metadata"}
    if hostname in _blocked_hosts:
        raise ValueError("Webhook URL points to a blocked metadata endpoint")

    # In dev mode, allow localhost without further checks
    if _DEV_MODE and hostname in ("localhost", "127.0.0.1", "::1"):
        return url

    # Resolve hostname and block internal/private IPs
    try:
        resolved_ips = socket.getaddrinfo(hostname, None)
        for _family, _type, _proto, _canonname, sockaddr in resolved_ips:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError(f"Webhook URL resolves to internal/private IP: {sockaddr[0]}")
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve webhook hostname: {hostname}") from exc

    return url


async def send_brief_notifications(
    project_id: str,
    brief_id: str,
    pdf_bytes: bytes | None,
    json_summary: dict,
    db: Any = None,
) -> dict:
    """Send notifications for a generated intelligence brief.

    Queries NotificationPreference for the project and sends via
    enabled channels (email, webhook).

    Returns dict with sent/failed counts.
    """
    results: dict[str, Any] = {"email_sent": 0, "webhook_sent": 0, "errors": []}

    # Query notification preferences
    preferences = []
    if db is not None:
        try:
            from sqlalchemy import select

            from app.models.evm import NotificationPreference
            from app.models.user import User

            stmt = (
                select(NotificationPreference, User.email)
                .join(User, User.id == NotificationPreference.user_id)
                .where(
                    NotificationPreference.project_id == project_id,
                    NotificationPreference.notification_type == "intelligence_brief",
                )
            )
            result = await db.execute(stmt)
            preferences = result.all()
        except Exception as exc:
            logger.warning("Failed to query notification preferences: %s", exc)
            results["errors"].append(f"preference_query: {exc}")

    for pref_row in preferences:
        pref = pref_row[0] if hasattr(pref_row, "__getitem__") else pref_row
        email = pref_row[1] if hasattr(pref_row, "__getitem__") and len(pref_row) > 1 else None

        pref_obj = pref if hasattr(pref, "email_enabled") else None
        if not pref_obj:
            continue

        # Email
        if pref_obj.email_enabled and email:
            try:
                # Escape all user-sourced values to prevent HTML injection
                project_name = html.escape(str(json_summary.get("project_name", "Project")))
                status = html.escape(str(json_summary.get("project_status", "")))
                score = html.escape(str(json_summary.get("overall_health_score", "")))
                executive_summary = html.escape(str(json_summary.get("executive_summary", "")))

                subject = f"[ConstructAI] Intelligence Brief — {project_name} ({status})"
                body = (
                    f"<h2>Project Intelligence Brief</h2>"
                    f"<p><b>Project:</b> {project_name}</p>"
                    f"<p><b>Health Score:</b> {score}/100 ({status})</p>"
                    f"<p><b>Summary:</b> {executive_summary}</p>"
                    f"<p>See attached PDF for the full report.</p>"
                    f"<hr><p><small>Generated by ConstructAI</small></p>"
                )

                await send_email_with_attachment(
                    to_email=email,
                    subject=subject,
                    body_html=body,
                    attachment_bytes=pdf_bytes,
                    attachment_filename=f"intelligence_brief_{brief_id[:8]}.pdf",
                )
                results["email_sent"] += 1
            except Exception as exc:
                logger.warning("Failed to send email to %s: %s", email, exc)
                results["errors"].append(f"email:{email}:{exc}")

        # Webhook
        if pref_obj.webhook_enabled and pref_obj.webhook_url:
            try:
                await post_webhook(
                    webhook_url=pref_obj.webhook_url,
                    payload={
                        "event": "intelligence_brief.generated",
                        "brief_id": brief_id,
                        "project_id": project_id,
                        "overall_health_score": json_summary.get("overall_health_score"),
                        "project_status": json_summary.get("project_status"),
                        "executive_summary": json_summary.get("executive_summary", ""),
                    },
                )
                results["webhook_sent"] += 1
            except Exception as exc:
                logger.warning(
                    "Failed to post webhook to %s: %s",
                    urlparse(pref_obj.webhook_url).hostname,
                    exc,
                )
                results["errors"].append(f"webhook:{urlparse(pref_obj.webhook_url).hostname}:{exc}")

    logger.info(
        "Brief notifications for %s: %d emails, %d webhooks, %d errors",
        project_id,
        results["email_sent"],
        results["webhook_sent"],
        len(results["errors"]),
    )
    return results


async def send_email_with_attachment(
    to_email: str,
    subject: str,
    body_html: str,
    attachment_bytes: bytes | None = None,
    attachment_filename: str = "report.pdf",
) -> None:
    """Send an email with optional PDF attachment via SMTP.

    Falls back to logging if SMTP is not configured.
    """
    try:
        from app.config import Settings

        settings = Settings()
        smtp_host = getattr(settings, "SMTP_HOST", None)
        smtp_port = getattr(settings, "SMTP_PORT", 587)
        smtp_user = getattr(settings, "SMTP_USER", None)
        smtp_password = getattr(settings, "SMTP_PASSWORD", None)
        from_email = getattr(settings, "SMTP_FROM_EMAIL", "noreply@constructai.com")
    except Exception:
        smtp_host = None
        smtp_user = None
        smtp_password = None
        smtp_port = 587
        from_email = "noreply@constructai.com"

    if not smtp_host:
        logger.info(
            "SMTP not configured — would send email to %s: %s",
            to_email,
            subject,
        )
        return

    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html"))

    if attachment_bytes:
        attachment = MIMEApplication(attachment_bytes, _subtype="pdf")
        attachment.add_header("Content-Disposition", "attachment", filename=attachment_filename)
        msg.attach(attachment)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        logger.info("Email sent to %s: %s", to_email, subject)
    except Exception as exc:
        logger.error("SMTP send failed to %s: %s", to_email, exc)
        raise


async def post_webhook(
    webhook_url: str,
    payload: dict,
) -> None:
    """POST JSON payload to a configured webhook URL.

    Validates the URL against SSRF before sending.
    """
    # Validate URL to prevent SSRF
    validated_url = _validate_webhook_url(webhook_url)

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                validated_url,
                json=payload,
                headers={"Content-Type": "application/json", "User-Agent": "ConstructAI/1.0"},
            )
            response.raise_for_status()
            logger.info(
                "Webhook posted to %s: status %d",
                urlparse(validated_url).hostname,
                response.status_code,
            )
    except ImportError:
        logger.warning(
            "httpx not available — webhook to %s skipped",
            urlparse(validated_url).hostname,
        )
    except Exception as exc:
        logger.error("Webhook POST to %s failed: %s", urlparse(validated_url).hostname, exc)
        raise
