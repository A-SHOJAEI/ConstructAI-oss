"""Data export endpoints (CSV, PDF) for dashboard resources."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User

router = APIRouter()


def _csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    """Build a CSV StreamingResponse from a list of dicts."""
    if not rows:
        output = io.StringIO("No data\n")
    else:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# SECURITY [M-23]: Reduced default export limit from 5000 to 1000 to prevent OOM
# from concurrent large exports. Added pagination via `page` parameter.
_EXPORT_PAGE_SIZE = 1000


@router.get("/{project_id}/export/safety-alerts")
async def export_safety_alerts(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
    format: str = Query(default="csv", pattern="^(csv)$"),
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
):
    """Export safety alerts as CSV."""
    await verify_project_access(project_id, current_user, db)

    from app.models.safety_incident import SafetyAlert

    offset = (page - 1) * _EXPORT_PAGE_SIZE
    result = await db.execute(
        select(SafetyAlert)
        .where(SafetyAlert.project_id == project_id)
        .order_by(SafetyAlert.created_at.desc())
        .limit(_EXPORT_PAGE_SIZE)
        .offset(offset)
    )
    alerts = result.scalars().all()
    rows = [
        {
            "id": str(a.id),
            "type": getattr(a, "alert_type", ""),
            "severity": getattr(a, "severity", ""),
            "description": getattr(a, "description", ""),
            "status": getattr(a, "status", ""),
            "created_at": str(getattr(a, "created_at", "")),
        }
        for a in alerts
    ]
    ts = datetime.now(UTC).strftime("%Y%m%d")
    return _csv_response(rows, f"safety_alerts_{ts}.csv")


@router.get("/{project_id}/export/rfis")
async def export_rfis(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("rfis", "read")),
    db: AsyncSession = Depends(get_db),
    format: str = Query(default="csv", pattern="^(csv)$"),
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
):
    """Export RFIs as CSV."""
    await verify_project_access(project_id, current_user, db)

    from app.models.communication import RFI

    # SECURITY [M-23]: Paginated export to prevent OOM
    offset = (page - 1) * _EXPORT_PAGE_SIZE
    result = await db.execute(
        select(RFI)
        .where(RFI.project_id == project_id)
        .order_by(RFI.created_at.desc())
        .limit(_EXPORT_PAGE_SIZE)
        .offset(offset)
    )
    rfis = result.scalars().all()
    rows = [
        {
            "id": str(r.id),
            "number": getattr(r, "rfi_number", ""),
            "subject": getattr(r, "subject", ""),
            "status": getattr(r, "status", ""),
            "priority": getattr(r, "priority", ""),
            "assigned_to": getattr(r, "assigned_to", ""),
            "created_at": str(getattr(r, "created_at", "")),
            "due_date": str(getattr(r, "due_date", "")),
        }
        for r in rfis
    ]
    ts = datetime.now(UTC).strftime("%Y%m%d")
    return _csv_response(rows, f"rfis_{ts}.csv")


@router.get("/{project_id}/export/daily-logs")
async def export_daily_logs(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
    format: str = Query(default="csv", pattern="^(csv)$"),
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
):
    """Export daily logs as CSV."""
    await verify_project_access(project_id, current_user, db)

    from app.models.productivity import DailyLog

    # SECURITY [M-23]: Paginated export to prevent OOM
    offset = (page - 1) * _EXPORT_PAGE_SIZE
    result = await db.execute(
        select(DailyLog)
        .where(DailyLog.project_id == project_id)
        .order_by(DailyLog.log_date.desc())
        .limit(_EXPORT_PAGE_SIZE)
        .offset(offset)
    )
    logs = result.scalars().all()
    rows = [
        {
            "id": str(log.id),
            "date": str(getattr(log, "log_date", "")),
            "weather": str(getattr(log, "weather", "")),
            "summary": getattr(log, "notes", ""),
            "created_by": str(getattr(log, "created_by", "")),
        }
        for log in logs
    ]
    ts = datetime.now(UTC).strftime("%Y%m%d")
    return _csv_response(rows, f"daily_logs_{ts}.csv")
