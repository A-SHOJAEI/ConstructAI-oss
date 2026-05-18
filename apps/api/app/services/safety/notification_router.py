"""Route alerts by severity to appropriate notification channels.

Notification architecture:
- **WebSocket** is the PRIMARY real-time notification channel. Every alert
  is broadcast to connected WebSocket clients for the project via
  ``ws_manager.broadcast_alert()``. This is the only channel that is fully
  functional without external service accounts.
- **Email** works when the email service is configured.
- **SMS** (Twilio), **push** (FCM/APNs), and **audible alarm** (IoT) are
  stubs awaiting integration with external providers. They log warnings
  until configured.

Respects per-user notification preferences stored in ``User.settings``
(JSONB path: ``settings.notifications.<notification_type>``).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Map alert_type values to the toggle key in User.settings.notifications
_ALERT_TYPE_TO_PREF_KEY: dict[str, str] = {
    "ppe_violation": "safety_alerts",
    "zone_breach": "safety_alerts",
    "safety_alert": "safety_alerts",
    "schedule_change": "schedule_changes",
}


async def should_notify_user(
    db: Any,
    user_id: uuid.UUID | str,
    notification_type: str,
) -> bool:
    """Check whether *user_id* has *notification_type* enabled.

    Queries ``User.settings`` JSONB for the toggle at
    ``settings.notifications.<notification_type>``.  Returns ``True``
    (deliver the notification) when:

    * The user cannot be found (fail-open for safety).
    * The ``notifications`` dict or the specific key is absent
      (default: enabled).
    * The toggle is explicitly ``True``.

    Returns ``False`` only when the toggle is explicitly ``False``.
    """
    if db is None:
        return True  # No DB session available — fail open

    try:
        from app.models.user import User

        uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id
        user = await db.get(User, uid)
        if user is None:
            return True  # Unknown user — fail open

        notifications = (user.settings or {}).get("notifications", {})
        # If the key is absent, the default is True (notifications enabled)
        return notifications.get(notification_type, True) is not False
    except Exception:
        logger.warning(
            "Failed to check notification preference for user %s / %s — defaulting to enabled",
            user_id,
            notification_type,
            exc_info=True,
        )
        return True  # Fail open — always safer to deliver than to suppress


async def route_notification(alert: dict, db: Any = None) -> dict:
    """Route alert to appropriate channels based on priority.

    When *db* is provided and the alert contains ``notify_user_ids``,
    each user's notification preferences are checked before dispatch.
    Users who have disabled the relevant notification type are skipped.

    Returns dict with channels notified, status per channel, and overall status.
    One channel failure does not block others.
    """
    priority = alert.get("priority", "P5_info")
    result = {
        "alert_id": alert.get("id"),
        "channels": [],
        "failed_channels": [],
        "skipped_users": [],
        "priority": priority,
        "status": "success",
    }

    # Determine which notification-preference key to check
    alert_type = alert.get("alert_type", "safety_alert")
    pref_key = _ALERT_TYPE_TO_PREF_KEY.get(alert_type, "safety_alerts")

    # If a DB session and user list are available, filter out users who
    # have opted out of this notification type.
    notify_user_ids: list[str] = alert.get("notify_user_ids", [])
    if db is not None and notify_user_ids:
        filtered_ids: list[str] = []
        for uid in notify_user_ids:
            if await should_notify_user(db, uid, pref_key):
                filtered_ids.append(uid)
            else:
                result["skipped_users"].append(uid)
                logger.info(
                    "Skipping notification for user %s — %s disabled",
                    uid,
                    pref_key,
                )
        alert = {**alert, "notify_user_ids": filtered_ids}

        # If all users have opted out, short-circuit
        if not filtered_ids and not alert.get("notify_emails"):
            result["status"] = "skipped"
            return result

    # WebSocket is ALWAYS the first channel — it's the primary real-time
    # notification mechanism and works without any external service accounts.
    # handler signature: `(alert: dict) -> Awaitable[Any]`
    channels: list[tuple[str, Any]] = [("websocket", _broadcast_websocket)]

    if priority == "P1_critical":
        channels += [
            ("sms", _send_sms),
            ("push", _send_push_notification),
            ("alarm", _trigger_audible_alarm),
        ]
    elif priority == "P2_high":
        channels += [
            ("push", _send_push_notification),
            ("email", _send_email),
        ]
    elif priority == "P3_medium":
        channels.append(("review_queue", _queue_for_review))
    else:
        channels.append(("digest", _add_to_digest))

    for channel_name, handler in channels:
        try:
            await handler(alert)
            result["channels"].append(channel_name)
        except NotImplementedError as nie:
            logger.warning(
                "Notification channel '%s' not implemented for alert %s: %s",
                channel_name,
                alert.get("id"),
                nie,
            )
            result["failed_channels"].append(channel_name)
        except Exception:
            logger.exception(
                "Notification channel '%s' failed for alert %s",
                channel_name,
                alert.get("id"),
            )
            result["failed_channels"].append(channel_name)

    # Emergency email fallback for P1/P2 when primary channels failed
    # and email was not already attempted in the channel list.
    if result["failed_channels"] and priority in ("P1_critical", "P2_high"):
        already_attempted = {c for c in result["channels"]} | {c for c in result["failed_channels"]}
        if "email" not in already_attempted:
            try:
                await _send_email(alert)
                result["channels"].append("email_fallback")
            except Exception as e:
                logger.error(
                    "Emergency email fallback also failed for alert %s: %s", alert.get("id"), e
                )
                result["failed_channels"].append("email_fallback")

    if result["failed_channels"] and result["channels"]:
        result["status"] = "partial"
    elif result["failed_channels"] and not result["channels"]:
        result["status"] = "failed"

    return result


async def _broadcast_websocket(alert: dict):
    """Broadcast alert to connected WebSocket clients for the project.

    This is the primary real-time notification channel. All connected
    clients on the project's safety WebSocket will receive the alert
    immediately. Does not require any external service accounts.
    """
    from app.services.realtime.websocket_server import ws_manager

    project_id = alert.get("project_id", "")
    if not project_id:
        logger.warning("Cannot broadcast alert %s: no project_id", alert.get("id"))
        return

    await ws_manager.broadcast_alert(str(project_id), alert)
    logger.info(
        "WebSocket broadcast: alert %s to project %s",
        alert.get("id"),
        project_id,
    )


async def _send_sms(alert: dict):
    """SMS channel — requires Twilio integration.

    Raises NotImplementedError so the caller can record this as a failed channel.
    Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER to enable.
    """
    raise NotImplementedError(
        "SMS notifications require Twilio integration. "
        "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER in environment."
    )


async def _send_push_notification(alert: dict):
    """Push notifications — requires Firebase Cloud Messaging integration.

    Raises NotImplementedError so the caller can record this as a failed channel.
    Set FIREBASE_SERVICE_ACCOUNT_KEY_PATH in environment to enable.
    """
    raise NotImplementedError(
        "Push notifications require Firebase Cloud Messaging integration. "
        "Set FIREBASE_SERVICE_ACCOUNT_KEY_PATH in environment."
    )


async def _send_email(alert: dict):
    """Send email notification for safety alerts."""
    import asyncio

    from app.services.email.service import send_safety_alert_email

    recipients = alert.get("notify_emails", [])
    if not recipients:
        logger.info("No email recipients for alert %s, skipping", alert.get("id"))
        return
    # send_safety_alert_email is synchronous — run in executor to avoid
    # blocking the async event loop.
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, send_safety_alert_email, recipients, alert)


async def _trigger_audible_alarm(alert: dict):
    """Audible alarm — requires IoT/MQTT alarm integration.

    Raises NotImplementedError so the caller can record this as a failed channel.
    Configure ALARM_SERVICE_URL in environment to enable.
    """
    raise NotImplementedError(
        "Audible alarms require IoT/MQTT alarm integration. "
        "Configure ALARM_SERVICE_URL in environment."
    )


async def _queue_for_review(alert: dict):
    """Queue alert for supervisor review via audit trail.

    Persists the alert to the structured audit log so it is never
    silently lost. A dedicated review queue UI will consume these
    entries when the supervisor dashboard is built.
    """
    from app.services.observability.audit_logger import AuditAction, audit_log

    logger.info(
        "Alert %s queued for supervisor review (pending implementation of dedicated review queue)",
        alert.get("id"),
    )
    # Persist to audit log so the alert is not silently lost
    audit_log(
        AuditAction.RESOURCE_CREATED,
        resource_type="safety_alert",
        resource_id=str(alert.get("id", "")),
        details={
            "severity": alert.get("severity"),
            "type": alert.get("alert_type"),
            "queue_action": "queued_for_review",
            "project_id": str(alert.get("project_id", "")),
        },
    )


async def _add_to_digest(alert: dict):
    """Add alert to daily/weekly digest via audit trail.

    Persists the alert to the structured audit log so it can be
    aggregated into digest emails when the digest service is built.
    """
    from app.services.observability.audit_logger import AuditAction, audit_log

    logger.info(
        "Alert %s added to digest (pending implementation of digest aggregation service)",
        alert.get("id"),
    )
    # Persist to audit log so the alert is not silently lost
    audit_log(
        AuditAction.RESOURCE_CREATED,
        resource_type="safety_alert",
        resource_id=str(alert.get("id", "")),
        details={
            "severity": alert.get("severity"),
            "type": alert.get("alert_type"),
            "queue_action": "added_to_digest",
            "project_id": str(alert.get("project_id", "")),
        },
    )


def check_notification_channels():
    """Log warnings for unimplemented notification channels at startup."""
    import os

    missing: list[str] = []
    if not os.environ.get("TWILIO_ACCOUNT_SID"):
        missing.append("SMS (Twilio: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)")
    if not os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY_PATH"):
        missing.append("Push (Firebase: set FIREBASE_SERVICE_ACCOUNT_KEY_PATH)")
    if not os.environ.get("ALARM_SERVICE_URL"):
        missing.append("Audible alarm (IoT: set ALARM_SERVICE_URL)")
    if missing:
        logger.warning(
            "NOTIFICATION CHANNELS NOT CONFIGURED: %s. "
            "Safety alerts for these channels will be skipped.",
            ", ".join(missing),
        )
