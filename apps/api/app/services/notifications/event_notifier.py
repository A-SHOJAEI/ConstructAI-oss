"""Centralized event-driven notification dispatcher.

Provides a unified notification system for all ConstructAI features.
Supports email and WebSocket channels with per-user preference checking.
Includes scheduled checks for overdue items and EVM threshold breaches.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Notification event definitions
# ---------------------------------------------------------------------------

NOTIFICATION_EVENTS: dict[str, dict[str, Any]] = {
    "payment.received": {
        "template": "Payment of {amount} received for {project}",
        "channels": ["email", "websocket"],
        "pref_key": "payment_updates",
    },
    "rfi.response_added": {
        "template": "RFI {rfi_number} has a new response",
        "channels": ["email", "websocket"],
        "pref_key": "rfi_updates",
    },
    "rfi.overdue": {
        "template": "RFI {rfi_number} is overdue (due {due_date})",
        "channels": ["email", "websocket"],
        "pref_key": "rfi_updates",
    },
    "submittal.overdue": {
        "template": "Submittal {submittal_number} is overdue",
        "channels": ["email", "websocket"],
        "pref_key": "submittal_updates",
    },
    "schedule.spi_threshold": {
        "template": "SPI dropped to {spi} on {project} (threshold: {threshold})",
        "channels": ["email", "websocket"],
        "pref_key": "schedule_changes",
    },
    "contract.deviation_found": {
        "template": "Contract deviation: {clause_type} - {description}",
        "channels": ["email"],
        "pref_key": "contract_alerts",
    },
    "punch_list.overdue": {
        "template": "Punch list item '{description}' is overdue",
        "channels": ["email", "websocket"],
        "pref_key": "field_updates",
    },
    "daily_log.not_submitted": {
        "template": "Daily log for {date} has not been submitted",
        "channels": ["email"],
        "pref_key": "field_updates",
    },
    "sensor.anomaly": {
        "template": "Sensor anomaly: {sensor_type} at {location} - {description}",
        "channels": ["email", "websocket"],
        "pref_key": "safety_alerts",
    },
    "defect.detected": {
        "template": "Defect detected: {defect_type} at {location}",
        "channels": ["email", "websocket"],
        "pref_key": "quality_alerts",
    },
}


# ---------------------------------------------------------------------------
# M-26: Notification idempotency helpers (Redis-backed dedup)
# ---------------------------------------------------------------------------

# Window during which identical notifications collapse into one. 5 minutes
# is long enough to catch retries + typical duplicate-publish bursts, short
# enough that a genuine re-trigger (e.g. "still overdue after an hour")
# still reaches the user.
_NOTIFICATION_DEDUP_TTL_SECONDS = 300


async def _compute_dedup_key(
    event_type: str,
    project_id: uuid.UUID,
    context_data: dict[str, Any],
) -> str:
    """Deterministic hash of the notification payload."""
    import hashlib
    import json

    # Sort keys for deterministic serialization across re-orderings.
    payload = json.dumps(
        {
            "event_type": event_type,
            "project_id": str(project_id),
            "context": dict(sorted(context_data.items())),
        },
        default=str,
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"cai:notify_dedup:{digest}"


async def _is_duplicate_notification(dedup_key: str, user_id: uuid.UUID) -> bool:
    """Return True if we've already dispatched this event to this user recently.

    Uses Redis SET NX — atomic check-and-set. Falls through to False (send
    the notification) when Redis is unavailable, because silently dropping
    alerts is more dangerous than occasional duplicates.
    """
    try:
        from app.services.security.redis_state import _get_redis

        r = await _get_redis()
        if r is None:
            return False
        user_key = f"{dedup_key}:{user_id}"
        was_set = await r.set(user_key, "1", nx=True, ex=_NOTIFICATION_DEDUP_TTL_SECONDS)
        return not was_set
    except Exception:
        # Never block a notification on Redis issues.
        return False


# ---------------------------------------------------------------------------
# Notification model (DB persistence)
# ---------------------------------------------------------------------------


async def _log_notification(
    db: AsyncSession,
    event_type: str,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    channel: str,
    message: str,
    context_data: dict[str, Any] | None = None,
) -> None:
    """Persist a notification record to the database.

    Uses a lightweight approach: stores in the user's notifications JSONB
    or a dedicated table if available. Silently skips if the model is
    not yet migrated.
    """
    try:
        from app.models.notification import Notification

        notification = Notification(
            project_id=project_id,
            user_id=user_id,
            event_type=event_type,
            channel=channel,
            message=message,
            context_data=context_data or {},
            created_at=datetime.now(UTC),
        )
        db.add(notification)
        await db.flush()
    except ImportError:
        # Notification model not yet created -- log only
        logger.debug(
            "Notification model not available; skipping DB persistence for %s",
            event_type,
        )
    except Exception:
        logger.warning(
            "Failed to persist notification for event %s",
            event_type,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# User preference checking
# ---------------------------------------------------------------------------


async def _check_user_preference(
    db: AsyncSession,
    user_id: uuid.UUID,
    pref_key: str,
) -> bool:
    """Check whether a user has enabled notifications for *pref_key*.

    Looks up ``User.settings`` JSONB field at path
    ``notifications.<pref_key>``.  Defaults to ``True`` if the preference
    is not explicitly set (opt-out model).
    """
    try:
        from app.models.user import User

        user = await db.get(User, user_id)
        if user is None:
            return False

        settings = getattr(user, "settings", None) or {}
        notifications_prefs = settings.get("notifications", {})
        # Default to True (opt-out model)
        return bool(notifications_prefs.get(pref_key, True))
    except ImportError:
        logger.debug("User model not available for preference check")
        return True
    except Exception:
        logger.warning(
            "Failed to check notification preference for user %s / %s",
            user_id,
            pref_key,
            exc_info=True,
        )
        # Default to sending the notification on preference-check failure
        return True


# ---------------------------------------------------------------------------
# Channel dispatchers
# ---------------------------------------------------------------------------


async def _send_email_notification(
    user_id: uuid.UUID,
    subject: str,
    body: str,
) -> bool:
    """Send an email notification to a user.

    Uses the existing email service if available.  Returns True on
    success, False on failure.
    """
    try:
        from app.database import async_session
        from app.models.user import User
        from app.services.communication.email_service import send_email

        async with async_session() as db:
            user = await db.get(User, user_id)
            if user is None or not getattr(user, "email", None):
                logger.debug("No email address for user %s; skipping email", user_id)
                return False
            email = user.email

        await send_email(to=email, subject=subject, body=body)
        return True

    except ImportError:
        logger.debug("Email service not available; skipping email notification")
        return False
    except Exception:
        logger.warning(
            "Failed to send email notification to user %s",
            user_id,
            exc_info=True,
        )
        return False


async def _send_websocket_notification(
    project_id: uuid.UUID,
    event_type: str,
    data: dict[str, Any],
) -> bool:
    """Broadcast a notification via WebSocket to project subscribers.

    Uses the existing WebSocket manager if available.  Returns True on
    success, False on failure.
    """
    try:
        from app.services.realtime.websocket_server import ws_manager

        await ws_manager.broadcast(
            project_id=str(project_id),
            message={
                "type": "notification",
                "event": event_type,
                "data": data,
            },
        )
        return True
    except ImportError:
        logger.debug("WebSocket manager not available; skipping WS notification")
        return False
    except Exception:
        logger.warning(
            "Failed to send WebSocket notification for project %s / %s",
            project_id,
            event_type,
            exc_info=True,
        )
        return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def notify(
    db: AsyncSession,
    event_type: str,
    project_id: uuid.UUID,
    user_ids: list[uuid.UUID],
    context_data: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch a notification event to the specified users.

    Looks up the event configuration from ``NOTIFICATION_EVENTS``, checks
    each user's notification preferences, and dispatches to all enabled
    channels.  Logs the notification for audit.

    Args:
        db: Async database session.
        event_type: Key in ``NOTIFICATION_EVENTS``.
        project_id: Project the event belongs to.
        user_ids: Users to notify.
        context_data: Dict of template variables (e.g. ``amount``,
            ``rfi_number``, ``project``).

    Returns:
        Summary dict with counts of sent/skipped notifications.
    """
    event_config = NOTIFICATION_EVENTS.get(event_type)
    if event_config is None:
        logger.warning("Unknown notification event type: %s", event_type)
        return {"sent": 0, "skipped": 0, "error": "unknown_event_type"}

    template = event_config["template"]
    channels = event_config["channels"]
    pref_key = event_config["pref_key"]

    # Render message from template
    try:
        message = template.format(**context_data)
    except KeyError as exc:
        logger.warning("Missing template variable for event %s: %s", event_type, exc)
        message = template  # Use raw template as fallback

    sent_count = 0
    skipped_count = 0

    # M-26: Idempotency. A deterministic hash of the event payload is stored
    # in Redis with a short TTL; if the same notification was dispatched in
    # the last window, skip it. Protects against retries and duplicate
    # publishes sending double emails to users.
    dedup_key = await _compute_dedup_key(event_type, project_id, context_data)

    for user_id in user_ids:
        # Check user preference
        pref_enabled = await _check_user_preference(db, user_id, pref_key)
        if not pref_enabled:
            skipped_count += 1
            continue

        if await _is_duplicate_notification(dedup_key, user_id):
            logger.info(
                "Skipping duplicate notification %s to user %s",
                event_type,
                user_id,
            )
            skipped_count += 1
            continue

        # Dispatch to each channel
        for channel in channels:
            try:
                if channel == "email":
                    subject = f"[ConstructAI] {message}"
                    await _send_email_notification(user_id, subject, message)
                elif channel == "websocket":
                    await _send_websocket_notification(
                        project_id,
                        event_type,
                        {"message": message, **context_data},
                    )

                # Log the notification
                await _log_notification(
                    db,
                    event_type=event_type,
                    project_id=project_id,
                    user_id=user_id,
                    channel=channel,
                    message=message,
                    context_data=context_data,
                )
                sent_count += 1
            except Exception:
                logger.warning(
                    "Failed to dispatch %s notification for user %s via %s",
                    event_type,
                    user_id,
                    channel,
                    exc_info=True,
                )

    logger.info(
        "Notification dispatched: event=%s, project=%s, sent=%d, skipped=%d",
        event_type,
        project_id,
        sent_count,
        skipped_count,
    )
    return {"sent": sent_count, "skipped": skipped_count}


# ---------------------------------------------------------------------------
# Scheduled task: check overdue items
# ---------------------------------------------------------------------------


async def check_overdue_items(db: AsyncSession) -> dict[str, int]:
    """Check for overdue RFIs, submittals, and punch list items.

    Sends notifications for items that have become overdue.  Designed to
    be called by Celery Beat or a similar scheduler.

    Returns:
        Dict with counts of overdue items found per category.
    """
    results: dict[str, int] = {"rfis": 0, "submittals": 0, "punch_list": 0}
    today = date.today()

    # --- Overdue RFIs ---
    try:
        from app.models.communication import RFI

        rfi_query = select(RFI).where(
            RFI.status.in_(["open", "pending_review"]),
            RFI.due_date < today,
        )
        rfi_result = await db.execute(rfi_query)
        overdue_rfis = list(rfi_result.scalars().all())

        for rfi in overdue_rfis:
            user_ids = []
            if rfi.assigned_to:
                user_ids.append(rfi.assigned_to)
            if rfi.submitted_by and rfi.submitted_by not in user_ids:
                user_ids.append(rfi.submitted_by)

            if user_ids:
                try:
                    await notify(
                        db,
                        event_type="rfi.overdue",
                        project_id=rfi.project_id,
                        user_ids=user_ids,
                        context_data={
                            "rfi_number": rfi.rfi_number,
                            "due_date": rfi.due_date.isoformat() if rfi.due_date else "N/A",
                            "subject": rfi.subject,
                        },
                    )
                except Exception:
                    logger.warning(
                        "Failed to send overdue notification for RFI %s",
                        rfi.id,
                        exc_info=True,
                    )
                results["rfis"] += 1
    except ImportError:
        logger.debug("RFI model not available; skipping overdue RFI check")
    except Exception:
        logger.warning("Overdue RFI check failed", exc_info=True)

    # --- Overdue Submittals ---
    try:
        from app.models.submittal import Submittal

        sub_query = select(Submittal).where(
            Submittal.status.in_(["open", "pending", "submitted"]),
            Submittal.due_date < today,
        )
        sub_result = await db.execute(sub_query)
        overdue_submittals = list(sub_result.scalars().all())

        for submittal in overdue_submittals:
            user_ids = []
            if hasattr(submittal, "assigned_to") and submittal.assigned_to:
                user_ids.append(submittal.assigned_to)
            if hasattr(submittal, "submitted_by") and submittal.submitted_by:
                if submittal.submitted_by not in user_ids:
                    user_ids.append(submittal.submitted_by)

            if user_ids:
                try:
                    await notify(
                        db,
                        event_type="submittal.overdue",
                        project_id=submittal.project_id,
                        user_ids=user_ids,
                        context_data={
                            "submittal_number": getattr(
                                submittal, "submittal_number", str(submittal.id)
                            ),
                        },
                    )
                except Exception:
                    logger.warning(
                        "Failed to send overdue notification for submittal %s",
                        submittal.id,
                        exc_info=True,
                    )
                results["submittals"] += 1
    except ImportError:
        logger.debug("Submittal model not available; skipping overdue submittal check")
    except Exception:
        logger.warning("Overdue submittal check failed", exc_info=True)

    # --- Overdue Punch List Items ---
    try:
        from app.models.punch_list import PunchListItem

        punch_query = select(PunchListItem).where(
            PunchListItem.status.in_(["open", "in_progress"]),
            PunchListItem.due_date < today,
        )
        punch_result = await db.execute(punch_query)
        overdue_items = list(punch_result.scalars().all())

        for item in overdue_items:
            user_ids = []
            if hasattr(item, "assigned_to") and item.assigned_to:
                user_ids.append(item.assigned_to)
            if hasattr(item, "created_by") and item.created_by:
                if item.created_by not in user_ids:
                    user_ids.append(item.created_by)

            if user_ids:
                try:
                    await notify(
                        db,
                        event_type="punch_list.overdue",
                        project_id=item.project_id,
                        user_ids=user_ids,
                        context_data={
                            "description": getattr(item, "description", "")[:100],
                        },
                    )
                except Exception:
                    logger.warning(
                        "Failed to send overdue notification for punch list item %s",
                        item.id,
                        exc_info=True,
                    )
                results["punch_list"] += 1
    except ImportError:
        logger.debug("PunchListItem model not available; skipping overdue punch list check")
    except Exception:
        logger.warning("Overdue punch list check failed", exc_info=True)

    logger.info(
        "Overdue check complete: %d RFIs, %d submittals, %d punch list items",
        results["rfis"],
        results["submittals"],
        results["punch_list"],
    )
    return results


# ---------------------------------------------------------------------------
# Scheduled task: check EVM thresholds
# ---------------------------------------------------------------------------


async def check_evm_thresholds(
    db: AsyncSession,
    project_id: uuid.UUID,
    spi_threshold: float = 0.9,
    cpi_threshold: float = 0.9,
) -> dict[str, Any]:
    """Check the latest EVM snapshot against performance thresholds.

    If SPI or CPI has dropped below the threshold, sends a notification
    to project stakeholders.

    Args:
        db: Async database session.
        project_id: Project to check.
        spi_threshold: SPI warning threshold (default 0.9).
        cpi_threshold: CPI warning threshold (default 0.9).

    Returns:
        Dict with check results: spi, cpi, notifications_sent.
    """
    result: dict[str, Any] = {
        "project_id": str(project_id),
        "spi_below_threshold": False,
        "cpi_below_threshold": False,
        "notifications_sent": 0,
    }

    try:
        from app.models.evm import EVMSnapshot
        from app.models.project import Project

        # Get the latest EVM snapshot
        snap_query = (
            select(EVMSnapshot)
            .where(EVMSnapshot.project_id == project_id)
            .order_by(EVMSnapshot.snapshot_date.desc())
            .limit(1)
        )
        snap_result = await db.execute(snap_query)
        snapshot = snap_result.scalar_one_or_none()

        if snapshot is None:
            result["skipped"] = "no_evm_data"
            return result

        spi = float(snapshot.spi) if snapshot.spi is not None else None
        cpi = float(snapshot.cpi) if snapshot.cpi is not None else None

        result["spi"] = spi
        result["cpi"] = cpi

        # Get project info for notification context
        project = await db.get(Project, project_id)
        project_name = project.name if project else str(project_id)

        # Collect stakeholder user IDs from the project
        user_ids: list[uuid.UUID] = []
        if project:
            # Project manager
            if hasattr(project, "manager_id") and project.manager_id:
                user_ids.append(project.manager_id)
            # Project owner/creator
            if hasattr(project, "created_by") and project.created_by:
                if project.created_by not in user_ids:
                    user_ids.append(project.created_by)

        if not user_ids:
            result["skipped"] = "no_stakeholders"
            return result

        # Check SPI threshold
        if spi is not None and spi < spi_threshold:
            result["spi_below_threshold"] = True
            try:
                notify_result = await notify(
                    db,
                    event_type="schedule.spi_threshold",
                    project_id=project_id,
                    user_ids=user_ids,
                    context_data={
                        "spi": f"{spi:.2f}",
                        "project": project_name,
                        "threshold": f"{spi_threshold:.2f}",
                    },
                )
                result["notifications_sent"] += notify_result.get("sent", 0)
            except Exception:
                logger.warning(
                    "Failed to send SPI threshold notification for project %s",
                    project_id,
                    exc_info=True,
                )

        # Check CPI threshold (reuse similar event type or log for now)
        if cpi is not None and cpi < cpi_threshold:
            result["cpi_below_threshold"] = True
            logger.warning(
                "CPI below threshold for project %s: %.2f < %.2f",
                project_id,
                cpi,
                cpi_threshold,
            )

    except ImportError:
        logger.debug("EVM models not available; skipping threshold check")
        result["skipped"] = "models_not_available"
    except Exception:
        logger.warning(
            "EVM threshold check failed for project %s",
            project_id,
            exc_info=True,
        )
        result["error"] = "check_failed"

    return result
