"""Tests for ``send_brief_notifications`` orchestration logic.

The SMTP/SSRF helpers are tested elsewhere (``test_notification_service_ssrf.py``).
This file pins the orchestrator that fans out to email + webhook based
on user preferences:
- HTML escaping of project name / status / score / summary (XSS hardening)
- Per-channel error isolation (one user's webhook failure must not
  block another user's email)
- DB query failure fallback (don't crash the whole brief because we
  can't fetch preferences)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.agents.notification_service import send_brief_notifications

# =========================================================================
# Helpers
# =========================================================================


def _pref(
    *,
    email_enabled: bool = True,
    webhook_enabled: bool = False,
    webhook_url: str | None = None,
) -> SimpleNamespace:
    """Build a fake NotificationPreference object."""
    return SimpleNamespace(
        email_enabled=email_enabled,
        webhook_enabled=webhook_enabled,
        webhook_url=webhook_url,
    )


class _FakeResult:
    """Mimics SQLAlchemy ``result.all()`` returning list of (pref, email) tuples."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    """Minimal async DB stub. Returns a configurable result set."""

    def __init__(self, rows=None, raise_on_execute: Exception | None = None):
        self._rows = rows or []
        self._raise = raise_on_execute

    async def execute(self, _stmt):
        if self._raise:
            raise self._raise
        return _FakeResult(self._rows)


_BASE_SUMMARY = {
    "project_name": "Tower 42",
    "project_status": "GREEN",
    "overall_health_score": 85,
    "executive_summary": "On track.",
}


# =========================================================================
# No DB / empty preferences
# =========================================================================


@pytest.mark.asyncio
async def test_no_db_skips_preference_query():
    """[edge case] db=None -> no email/webhook attempts, no errors."""
    out = await send_brief_notifications(
        project_id="p-1", brief_id="b-1", pdf_bytes=b"%PDF-fake", json_summary=_BASE_SUMMARY
    )
    assert out == {"email_sent": 0, "webhook_sent": 0, "errors": []}


@pytest.mark.asyncio
async def test_empty_preferences_no_actions():
    db = _FakeDb(rows=[])
    out = await send_brief_notifications(
        project_id="p-1",
        brief_id="b-1",
        pdf_bytes=b"%PDF-fake",
        json_summary=_BASE_SUMMARY,
        db=db,
    )
    assert out["email_sent"] == 0
    assert out["webhook_sent"] == 0
    assert out["errors"] == []


# =========================================================================
# Email path
# =========================================================================


@pytest.mark.asyncio
async def test_email_enabled_sends_to_user():
    pref = _pref(email_enabled=True)
    db = _FakeDb(rows=[(pref, "alice@example.com")])
    fake_send = AsyncMock(return_value=None)
    with patch(
        "app.services.agents.notification_service.send_email_with_attachment",
        fake_send,
    ):
        out = await send_brief_notifications(
            project_id="p-1",
            brief_id="b-deadbeef-x",
            pdf_bytes=b"%PDF-fake",
            json_summary=_BASE_SUMMARY,
            db=db,
        )
    assert out["email_sent"] == 1
    assert out["errors"] == []
    fake_send.assert_called_once()
    # Subject contains project name + status:
    call_kwargs = fake_send.call_args.kwargs
    assert "Tower 42" in call_kwargs["subject"]
    assert "GREEN" in call_kwargs["subject"]
    assert call_kwargs["to_email"] == "alice@example.com"
    # Attachment filename uses the first 8 chars of brief_id:
    assert call_kwargs["attachment_filename"] == "intelligence_brief_b-deadbe.pdf"


@pytest.mark.asyncio
async def test_email_disabled_skipped():
    pref = _pref(email_enabled=False)
    db = _FakeDb(rows=[(pref, "alice@example.com")])
    fake_send = AsyncMock(return_value=None)
    with patch(
        "app.services.agents.notification_service.send_email_with_attachment",
        fake_send,
    ):
        out = await send_brief_notifications(
            project_id="p-1",
            brief_id="b-1",
            pdf_bytes=b"",
            json_summary=_BASE_SUMMARY,
            db=db,
        )
    assert out["email_sent"] == 0
    fake_send.assert_not_called()


@pytest.mark.asyncio
async def test_email_no_email_address_skipped():
    """Pref says email_enabled but user row has no email -> skipped
    silently (no crash, no error log)."""
    pref = _pref(email_enabled=True)
    db = _FakeDb(rows=[(pref, None)])
    fake_send = AsyncMock(return_value=None)
    with patch(
        "app.services.agents.notification_service.send_email_with_attachment",
        fake_send,
    ):
        out = await send_brief_notifications(
            project_id="p-1",
            brief_id="b-1",
            pdf_bytes=b"",
            json_summary=_BASE_SUMMARY,
            db=db,
        )
    assert out["email_sent"] == 0
    fake_send.assert_not_called()


@pytest.mark.asyncio
async def test_email_send_failure_recorded():
    """[error isolation] Email helper raises -> error captured, count
    stays at 0, no exception bubbles out (so other channels can still
    fire for other users)."""
    pref = _pref(email_enabled=True)
    db = _FakeDb(rows=[(pref, "alice@example.com")])
    fake_send = AsyncMock(side_effect=RuntimeError("smtp 500"))
    with patch(
        "app.services.agents.notification_service.send_email_with_attachment",
        fake_send,
    ):
        out = await send_brief_notifications(
            project_id="p-1",
            brief_id="b-1",
            pdf_bytes=b"",
            json_summary=_BASE_SUMMARY,
            db=db,
        )
    assert out["email_sent"] == 0
    assert len(out["errors"]) == 1
    assert "alice@example.com" in out["errors"][0]
    assert "smtp 500" in out["errors"][0]


# =========================================================================
# HTML escaping (XSS hardening)
# =========================================================================


@pytest.mark.asyncio
async def test_email_escapes_html_in_summary_fields():
    """[security/XSS] Project name and summary are user-controlled —
    must be HTML-escaped before being interpolated into the email
    body."""
    pref = _pref(email_enabled=True)
    db = _FakeDb(rows=[(pref, "alice@example.com")])
    summary = {
        "project_name": "<script>alert(1)</script>",
        "project_status": "GREEN",
        "overall_health_score": 85,
        "executive_summary": "Status: <b>good</b> & all '\"safe\"'",
    }
    fake_send = AsyncMock(return_value=None)
    with patch(
        "app.services.agents.notification_service.send_email_with_attachment",
        fake_send,
    ):
        await send_brief_notifications(
            project_id="p-1",
            brief_id="b-1",
            pdf_bytes=b"",
            json_summary=summary,
            db=db,
        )
    body = fake_send.call_args.kwargs["body_html"]
    # Raw <script> must NOT appear:
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    # Quotes/ampersands escaped:
    assert "&amp;" in body
    # Subject also escaped via html.escape on project_name:
    subject = fake_send.call_args.kwargs["subject"]
    assert "<script>" not in subject


# =========================================================================
# Webhook path
# =========================================================================


@pytest.mark.asyncio
async def test_webhook_enabled_with_url_posts_payload():
    pref = _pref(
        email_enabled=False,
        webhook_enabled=True,
        webhook_url="https://hook.example.com/webhook",
    )
    db = _FakeDb(rows=[(pref, "alice@example.com")])
    fake_post = AsyncMock(return_value=None)
    with patch("app.services.agents.notification_service.post_webhook", fake_post):
        out = await send_brief_notifications(
            project_id="p-1",
            brief_id="b-1",
            pdf_bytes=b"",
            json_summary=_BASE_SUMMARY,
            db=db,
        )
    assert out["webhook_sent"] == 1
    fake_post.assert_called_once()
    payload = fake_post.call_args.kwargs["payload"]
    # Pin payload contract — clients depend on these field names:
    assert payload["event"] == "intelligence_brief.generated"
    assert payload["brief_id"] == "b-1"
    assert payload["project_id"] == "p-1"
    assert payload["overall_health_score"] == 85
    assert payload["project_status"] == "GREEN"
    assert payload["executive_summary"] == "On track."


@pytest.mark.asyncio
async def test_webhook_enabled_no_url_skipped():
    """Webhook flag on but URL missing -> skipped silently."""
    pref = _pref(email_enabled=False, webhook_enabled=True, webhook_url=None)
    db = _FakeDb(rows=[(pref, "alice@example.com")])
    fake_post = AsyncMock(return_value=None)
    with patch("app.services.agents.notification_service.post_webhook", fake_post):
        out = await send_brief_notifications(
            project_id="p-1",
            brief_id="b-1",
            pdf_bytes=b"",
            json_summary=_BASE_SUMMARY,
            db=db,
        )
    assert out["webhook_sent"] == 0
    fake_post.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_disabled_skipped():
    pref = _pref(
        email_enabled=False,
        webhook_enabled=False,
        webhook_url="https://hook.example.com/x",
    )
    db = _FakeDb(rows=[(pref, "alice@example.com")])
    fake_post = AsyncMock(return_value=None)
    with patch("app.services.agents.notification_service.post_webhook", fake_post):
        out = await send_brief_notifications(
            project_id="p-1",
            brief_id="b-1",
            pdf_bytes=b"",
            json_summary=_BASE_SUMMARY,
            db=db,
        )
    assert out["webhook_sent"] == 0
    fake_post.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_failure_records_error_and_redacts_url():
    """[error isolation + privacy] Webhook fails -> error captured.
    Error message includes only the hostname (not full URL with query
    params that may carry secrets)."""
    pref = _pref(
        email_enabled=False,
        webhook_enabled=True,
        webhook_url="https://hook.example.com/secret-token-abc123",
    )
    db = _FakeDb(rows=[(pref, "alice@example.com")])
    fake_post = AsyncMock(side_effect=RuntimeError("connection refused"))
    with patch("app.services.agents.notification_service.post_webhook", fake_post):
        out = await send_brief_notifications(
            project_id="p-1",
            brief_id="b-1",
            pdf_bytes=b"",
            json_summary=_BASE_SUMMARY,
            db=db,
        )
    assert out["webhook_sent"] == 0
    assert len(out["errors"]) == 1
    err = out["errors"][0]
    assert "hook.example.com" in err
    # Secret path component must NOT leak into the error log:
    assert "secret-token-abc123" not in err


# =========================================================================
# Multi-user fan-out + isolation
# =========================================================================


@pytest.mark.asyncio
async def test_multi_user_email_and_webhook_independent():
    """[error isolation] User A's email failure must not prevent
    User B's webhook from firing."""
    pref_a = _pref(email_enabled=True)
    pref_b = _pref(
        email_enabled=False,
        webhook_enabled=True,
        webhook_url="https://hook.example.com/b",
    )
    db = _FakeDb(rows=[(pref_a, "a@x.com"), (pref_b, "b@x.com")])
    fake_email = AsyncMock(side_effect=RuntimeError("smtp 500"))
    fake_webhook = AsyncMock(return_value=None)
    with (
        patch(
            "app.services.agents.notification_service.send_email_with_attachment",
            fake_email,
        ),
        patch("app.services.agents.notification_service.post_webhook", fake_webhook),
    ):
        out = await send_brief_notifications(
            project_id="p-1",
            brief_id="b-1",
            pdf_bytes=b"",
            json_summary=_BASE_SUMMARY,
            db=db,
        )
    # User A's email failed:
    assert out["email_sent"] == 0
    assert any("a@x.com" in e for e in out["errors"])
    # But User B's webhook still fired:
    assert out["webhook_sent"] == 1
    fake_webhook.assert_called_once()


@pytest.mark.asyncio
async def test_user_with_both_channels_enabled_sends_both():
    pref = _pref(
        email_enabled=True,
        webhook_enabled=True,
        webhook_url="https://hook.example.com/x",
    )
    db = _FakeDb(rows=[(pref, "user@x.com")])
    fake_email = AsyncMock(return_value=None)
    fake_webhook = AsyncMock(return_value=None)
    with (
        patch(
            "app.services.agents.notification_service.send_email_with_attachment",
            fake_email,
        ),
        patch("app.services.agents.notification_service.post_webhook", fake_webhook),
    ):
        out = await send_brief_notifications(
            project_id="p-1",
            brief_id="b-1",
            pdf_bytes=b"",
            json_summary=_BASE_SUMMARY,
            db=db,
        )
    assert out["email_sent"] == 1
    assert out["webhook_sent"] == 1


# =========================================================================
# DB query failure
# =========================================================================


@pytest.mark.asyncio
async def test_db_query_failure_does_not_crash():
    """[error isolation] Preference query throws -> capture error,
    return 0/0 sent (the brief was already generated, we just can't
    notify)."""
    db = _FakeDb(raise_on_execute=RuntimeError("connection refused"))
    out = await send_brief_notifications(
        project_id="p-1",
        brief_id="b-1",
        pdf_bytes=b"",
        json_summary=_BASE_SUMMARY,
        db=db,
    )
    assert out["email_sent"] == 0
    assert out["webhook_sent"] == 0
    assert len(out["errors"]) == 1
    assert "preference_query" in out["errors"][0]
