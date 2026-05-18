from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_db
from app.dependencies import require_permission, verify_project_access
from app.models.project import Project
from app.models.safety_incident import SafetyAlert
from app.models.user import User
from app.schemas.safety import (
    AlertAcknowledgeRequest,
    AlertListResponse,
    SafetyAlertResponse,
    SafetyStatsResponse,
)
from app.services.realtime.websocket_server import (
    MAX_MESSAGE_SIZE,
    MAX_MESSAGES_PER_SECOND,
    ws_manager,
)
from app.utils.security import decode_access_token

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/alerts", response_model=AlertListResponse)
async def query_alerts(
    project_id: uuid.UUID = Query(...),
    priority: str | None = Query(default=None),
    alert_type: str | None = Query(default=None),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Query safety alerts with optional filters."""
    await verify_project_access(project_id, current_user, db)

    query = select(SafetyAlert).where(SafetyAlert.project_id == project_id)
    if priority:
        query = query.where(SafetyAlert.priority == priority)
    if alert_type:
        query = query.where(SafetyAlert.alert_type == alert_type)
    if from_date:
        try:
            parsed_from = datetime.fromisoformat(from_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid 'from' date format. Use ISO 8601 (e.g. 2024-01-15).",
            )
        query = query.where(SafetyAlert.created_at >= parsed_from)
    if to_date:
        try:
            parsed_to = datetime.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid 'to' date format. Use ISO 8601 (e.g. 2024-01-15).",
            )
        query = query.where(SafetyAlert.created_at <= parsed_to)
    query = query.order_by(SafetyAlert.created_at.desc()).limit(limit)
    result = await db.execute(query)
    alerts = result.scalars().all()
    return AlertListResponse(data=cast(list[SafetyAlertResponse], alerts), total=len(alerts))


@router.get("/alerts/{alert_id}", response_model=SafetyAlertResponse)
async def get_alert(
    alert_id: uuid.UUID,
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single safety alert by ID."""
    alert = await db.get(SafetyAlert, alert_id)
    if not alert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    await verify_project_access(alert.project_id, current_user, db)
    return alert


@router.patch("/alerts/{alert_id}/acknowledge", response_model=SafetyAlertResponse)
async def acknowledge_alert(
    alert_id: uuid.UUID,
    request: AlertAcknowledgeRequest,
    current_user: User = Depends(require_permission("safety", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Acknowledge a safety alert."""
    alert = await db.get(SafetyAlert, alert_id)
    if not alert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    await verify_project_access(alert.project_id, current_user, db)
    alert.is_acknowledged = True
    alert.is_false_positive = request.is_false_positive
    alert.response_notes = request.notes
    alert.acknowledged_by = current_user.id
    alert.acknowledged_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(alert)

    # Audit logging for false-positive markings
    if request.is_false_positive:
        logger.info(
            "AUDIT: Alert %s marked as false positive by user %s (org=%s, project=%s, notes=%s)",
            alert_id,
            current_user.id,
            getattr(current_user, "org_id", "unknown"),
            alert.project_id,
            (request.notes or "")[:200],
        )

        # Check false positive rate: if >50% of recent alerts are false positives, warn
        since = datetime.now(UTC) - timedelta(days=7)
        recent_total_result = await db.execute(
            select(func.count(SafetyAlert.id)).where(
                SafetyAlert.project_id == alert.project_id,
                SafetyAlert.is_acknowledged.is_(True),
                SafetyAlert.acknowledged_at >= since,
            )
        )
        recent_total = recent_total_result.scalar() or 0

        recent_fp_result = await db.execute(
            select(func.count(SafetyAlert.id)).where(
                SafetyAlert.project_id == alert.project_id,
                SafetyAlert.is_false_positive.is_(True),
                SafetyAlert.acknowledged_at >= since,
            )
        )
        recent_fp = recent_fp_result.scalar() or 0

        if recent_total >= 5 and recent_fp > recent_total * 0.5:
            logger.warning(
                "AUDIT WARNING: High false positive rate for project %s: "
                "%d/%d (%.0f%%) of recent alerts marked false positive in last 7 days. "
                "Possible model calibration issue or alert suppression attempt.",
                alert.project_id,
                recent_fp,
                recent_total,
                (recent_fp / recent_total) * 100,
            )

    return alert


@router.get("/stats", response_model=SafetyStatsResponse)
async def get_safety_stats(
    project_id: uuid.UUID = Query(...),
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated safety statistics for a project."""
    await verify_project_access(project_id, current_user, db)

    # Count total
    total_result = await db.execute(
        select(func.count(SafetyAlert.id)).where(SafetyAlert.project_id == project_id)
    )
    total = total_result.scalar() or 0

    # Count by priority
    priority_result = await db.execute(
        select(SafetyAlert.priority, func.count())
        .where(SafetyAlert.project_id == project_id)
        .group_by(SafetyAlert.priority)
    )
    by_priority = {row[0]: row[1] for row in priority_result}

    # Count by type
    type_result = await db.execute(
        select(SafetyAlert.alert_type, func.count())
        .where(SafetyAlert.project_id == project_id)
        .group_by(SafetyAlert.alert_type)
    )
    by_type = {row[0]: row[1] for row in type_result}

    # Count acknowledged
    ack_result = await db.execute(
        select(func.count(SafetyAlert.id)).where(
            SafetyAlert.project_id == project_id,
            SafetyAlert.is_acknowledged == True,  # noqa: E712
        )
    )
    acknowledged = ack_result.scalar() or 0

    # Count false positives
    fp_result = await db.execute(
        select(func.count(SafetyAlert.id)).where(
            SafetyAlert.project_id == project_id,
            SafetyAlert.is_false_positive == True,  # noqa: E712
        )
    )
    false_positives = fp_result.scalar() or 0

    return SafetyStatsResponse(
        total_alerts=total,
        alerts_by_priority=by_priority,
        alerts_by_type=by_type,
        acknowledged_count=acknowledged,
        false_positive_count=false_positives,
        period="all",
    )


@router.websocket("/ws/{project_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    project_id: str,
    token: str | None = Query(default=None),
    reconnect_token: str | None = Query(default=None),
):
    """WebSocket endpoint for real-time safety alerts.

    Authentication is **required**. The token can be provided as a query
    parameter or via a first-message handshake (``{"type": "auth", "token": "..."}``).
    Cross-tenant protection verifies the user's org owns the project.
    """

    # ── Helper: validate token + cross-tenant check ──────────────────
    async def _authenticate(jwt_token: str) -> bool:
        payload = decode_access_token(jwt_token)
        if not payload or "sub" not in payload or "org_id" not in payload:
            return False
        user_org_id = payload["org_id"]
        async with async_session() as db:
            result = await db.execute(
                select(Project).where(
                    Project.id == project_id,
                    Project.org_id == user_org_id,
                )
            )
            if result.scalar_one_or_none() is None:
                return False
        return True

    # ── Authenticate via query param or first-message handshake ─────
    handshake_accepted = False
    if token:
        if not await _authenticate(token):
            await websocket.close(code=4003, reason="Access denied")
            return
    else:
        # No query-param token — accept temporarily for handshake auth
        await websocket.accept()
        handshake_accepted = True
        try:
            import asyncio

            raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("type") == "auth":
                handshake_token = data.get("token")
            else:
                handshake_token = None
            if not handshake_token or not await _authenticate(handshake_token):
                await websocket.close(code=4001, reason="Authentication required")
                return
            token = handshake_token
        except Exception:
            await websocket.close(code=4001, reason="Authentication required")
            return

    connected = await ws_manager.connect(
        websocket,
        project_id,
        token,
        reconnect_token=reconnect_token,
        already_accepted=handshake_accepted,
    )
    if not connected:
        return

    # Rate-limit tracking: sliding window per client
    message_timestamps: list[float] = []

    # Periodic re-authentication: re-validate token every 5 minutes
    _REAUTH_INTERVAL = 300  # seconds
    last_auth_check = time.monotonic()

    try:
        while True:
            raw = await websocket.receive_text()

            # --- Message size guard ---
            if len(raw) > MAX_MESSAGE_SIZE:
                await websocket.close(code=4013, reason="Message too large")
                break

            # --- Rate-limit guard (sliding window) ---
            now = time.monotonic()
            message_timestamps = [t for t in message_timestamps if now - t < 1.0]
            if len(message_timestamps) >= MAX_MESSAGES_PER_SECOND:
                await websocket.close(code=4029, reason="Rate limit exceeded")
                break
            message_timestamps.append(now)

            # --- Periodic token re-validation ---
            if now - last_auth_check >= _REAUTH_INTERVAL:
                if not token or not await _authenticate(token):
                    logger.warning(
                        "WebSocket re-auth failed for project %s; closing",
                        project_id,
                    )
                    await websocket.close(code=4001, reason="Re-authentication failed")
                    break
                last_auth_check = now

            try:
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("type") == "pong":
                    ws_manager.handle_pong(websocket)
            except (json.JSONDecodeError, TypeError):
                pass
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket, project_id)
