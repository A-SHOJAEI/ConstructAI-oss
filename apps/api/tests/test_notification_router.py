"""Tests for the safety notification router.

The router fans out alerts across channels based on priority. Critical
properties this file pins:

- WebSocket is ALWAYS attempted (primary real-time channel).
- P1_critical → SMS + push + audible alarm + email fallback if any fail.
- P2_high → push + email + email fallback.
- P3_medium → review queue.
- P5_info → digest only.
- One channel failure does not block others (per-channel try/except).
- Per-user notification preferences are honoured when the DB is
  provided; an opted-out user is recorded in ``skipped_users``.
- ``should_notify_user`` is "fail open" — unknown user / DB error /
  missing settings all default to delivering the alert.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.safety.notification_router import (
    route_notification,
    should_notify_user,
)

# =========================================================================
# should_notify_user (fail-open behaviour)
# =========================================================================


async def test_should_notify_user_no_db_returns_true():
    """No DB session = no way to check prefs = fail open (deliver)."""
    assert await should_notify_user(None, uuid.uuid4(), "safety_alerts") is True


async def test_should_notify_user_unknown_user_returns_true():
    """User not found in DB = fail open."""
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    assert await should_notify_user(db, uuid.uuid4(), "safety_alerts") is True


async def test_should_notify_user_explicitly_disabled_returns_false():
    user = MagicMock()
    user.settings = {"notifications": {"safety_alerts": False}}
    db = AsyncMock()
    db.get = AsyncMock(return_value=user)
    assert await should_notify_user(db, uuid.uuid4(), "safety_alerts") is False


async def test_should_notify_user_missing_setting_defaults_to_enabled():
    user = MagicMock()
    user.settings = {"notifications": {}}  # key absent
    db = AsyncMock()
    db.get = AsyncMock(return_value=user)
    assert await should_notify_user(db, uuid.uuid4(), "safety_alerts") is True


async def test_should_notify_user_no_notifications_dict_defaults_to_enabled():
    user = MagicMock()
    user.settings = {}  # no notifications dict at all
    db = AsyncMock()
    db.get = AsyncMock(return_value=user)
    assert await should_notify_user(db, uuid.uuid4(), "safety_alerts") is True


async def test_should_notify_user_db_exception_returns_true():
    """DB error on the query path is fail-open as well — better to over-
    deliver than to silently suppress a P1 alert."""
    db = AsyncMock()
    db.get = AsyncMock(side_effect=RuntimeError("connection lost"))
    assert await should_notify_user(db, uuid.uuid4(), "safety_alerts") is True


async def test_should_notify_user_string_uuid_accepted():
    user = MagicMock()
    user.settings = {"notifications": {"safety_alerts": True}}
    db = AsyncMock()
    db.get = AsyncMock(return_value=user)
    out = await should_notify_user(db, str(uuid.uuid4()), "safety_alerts")
    assert out is True


# =========================================================================
# route_notification — channel-by-priority mapping
# =========================================================================


@pytest.fixture
def patched_handlers():
    """Patch every channel handler with an AsyncMock so we can observe
    which channels were attempted."""
    targets = {
        "ws": "_broadcast_websocket",
        "sms": "_send_sms",
        "push": "_send_push_notification",
        "email": "_send_email",
        "alarm": "_trigger_audible_alarm",
        "queue": "_queue_for_review",
        "digest": "_add_to_digest",
    }
    patches = {
        name: patch(
            f"app.services.safety.notification_router.{symbol}",
            new=AsyncMock(),
        )
        for name, symbol in targets.items()
    }
    started = {n: p.start() for n, p in patches.items()}
    yield started
    for p in patches.values():
        p.stop()


async def test_p1_critical_dispatches_to_ws_sms_push_alarm(patched_handlers):
    out = await route_notification({"id": "a1", "priority": "P1_critical"})
    # WebSocket always first, then SMS + push + alarm.
    assert set(out["channels"]) == {"websocket", "sms", "push", "alarm"}
    assert out["status"] == "success"


async def test_p2_high_dispatches_to_ws_push_email(patched_handlers):
    out = await route_notification({"id": "a1", "priority": "P2_high"})
    assert set(out["channels"]) == {"websocket", "push", "email"}


async def test_p3_medium_dispatches_to_ws_and_review_queue(patched_handlers):
    out = await route_notification({"id": "a1", "priority": "P3_medium"})
    assert set(out["channels"]) == {"websocket", "review_queue"}


async def test_p5_info_dispatches_to_ws_and_digest(patched_handlers):
    out = await route_notification({"id": "a1", "priority": "P5_info"})
    assert set(out["channels"]) == {"websocket", "digest"}


async def test_unknown_priority_falls_back_to_digest(patched_handlers):
    out = await route_notification({"id": "a1"})  # default priority = P5_info
    assert "digest" in out["channels"]


# ---- failure isolation --------------------------------------------------


async def test_one_channel_failure_does_not_block_others():
    """If SMS fails, push/alarm must still attempt — never let one
    flaky provider silence the alert."""
    with (
        patch(
            "app.services.safety.notification_router._broadcast_websocket",
            new=AsyncMock(),
        ),
        patch(
            "app.services.safety.notification_router._send_sms",
            new=AsyncMock(side_effect=RuntimeError("twilio down")),
        ),
        patch(
            "app.services.safety.notification_router._send_push_notification",
            new=AsyncMock(),
        ),
        patch(
            "app.services.safety.notification_router._trigger_audible_alarm",
            new=AsyncMock(),
        ),
        patch("app.services.safety.notification_router._send_email", new=AsyncMock()),
    ):
        out = await route_notification({"id": "a1", "priority": "P1_critical"})
    assert "websocket" in out["channels"]
    assert "push" in out["channels"]
    assert "alarm" in out["channels"]
    assert "sms" in out["failed_channels"]
    # Some channels worked AND some failed → "partial" status.
    assert out["status"] == "partial"


async def test_emergency_email_fallback_fires_for_p1_when_other_channels_fail():
    """P1/P2 with all primary channels failing must still attempt the
    emergency email fallback — that's the lifeline."""
    with (
        patch(
            "app.services.safety.notification_router._broadcast_websocket",
            new=AsyncMock(side_effect=RuntimeError("ws down")),
        ),
        patch(
            "app.services.safety.notification_router._send_sms",
            new=AsyncMock(side_effect=RuntimeError("sms down")),
        ),
        patch(
            "app.services.safety.notification_router._send_push_notification",
            new=AsyncMock(side_effect=RuntimeError("push down")),
        ),
        patch(
            "app.services.safety.notification_router._trigger_audible_alarm",
            new=AsyncMock(side_effect=RuntimeError("iot down")),
        ),
        patch(
            "app.services.safety.notification_router._send_email",
            new=AsyncMock(),
        ) as email,
    ):
        out = await route_notification({"id": "a1", "priority": "P1_critical"})
    email.assert_awaited_once()
    assert "email_fallback" in out["channels"]


async def test_all_channels_fail_returns_status_failed():
    """If WebSocket AND every other channel fails, the router reports
    overall status="failed"."""
    with (
        patch(
            "app.services.safety.notification_router._broadcast_websocket",
            new=AsyncMock(side_effect=RuntimeError("down")),
        ),
        patch(
            "app.services.safety.notification_router._add_to_digest",
            new=AsyncMock(side_effect=RuntimeError("down")),
        ),
    ):
        out = await route_notification({"id": "a1", "priority": "P5_info"})
    assert out["status"] == "failed"
    assert out["channels"] == []


# ---- per-user preference filter -----------------------------------------


async def test_skips_users_with_disabled_safety_alerts(patched_handlers):
    """When db is provided and a user has safety_alerts disabled, they
    show up in skipped_users (not in the broadcast list)."""
    user = MagicMock()
    user.settings = {"notifications": {"safety_alerts": False}}
    db = AsyncMock()
    db.get = AsyncMock(return_value=user)

    out = await route_notification(
        {
            "id": "a1",
            "priority": "P2_high",
            "alert_type": "ppe_violation",
            "notify_user_ids": [str(uuid.uuid4())],
        },
        db=db,
    )
    assert len(out["skipped_users"]) == 1


async def test_short_circuits_when_all_users_opt_out(patched_handlers):
    """If every user opted out and there's no email list, status =
    "skipped" — no channels were dispatched."""
    user = MagicMock()
    user.settings = {"notifications": {"safety_alerts": False}}
    db = AsyncMock()
    db.get = AsyncMock(return_value=user)

    out = await route_notification(
        {
            "id": "a1",
            "priority": "P1_critical",
            "alert_type": "ppe_violation",
            "notify_user_ids": [str(uuid.uuid4())],
        },
        db=db,
    )
    assert out["status"] == "skipped"
    assert out["channels"] == []
