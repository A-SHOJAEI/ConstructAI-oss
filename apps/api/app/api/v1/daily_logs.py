"""Daily log v2 API endpoints.

All routes are project-scoped: ``/projects/{project_id}/daily-logs/...``
"""

from __future__ import annotations

import io
import logging
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.productivity import DailyLog
from app.models.user import User
from app.schemas.productivity import (
    DailyLogCreateV2,
    DailyLogDetailListResponse,
    DailyLogDetailResponse,
    DailyLogUpdateV2,
)
from app.services.productivity.daily_log_pdf import generate_daily_log_pdf
from app.services.productivity.daily_log_service import (
    approve_daily_log,
    auto_populate_weather,
    copy_previous_day,
    create_daily_log,
    export_daily_logs_csv,
    get_daily_log_detail,
    get_weekly_summary,
    list_daily_logs,
    reject_to_draft,
    submit_daily_log,
    update_daily_log,
)

logger = logging.getLogger(__name__)

# Strong-ref registry for fire-and-forget asyncio tasks (prevents RUF006 GC).
_BACKGROUND_TASKS: set = set()

router = APIRouter()


# ---------------------------------------------------------------------------
# IMPORTANT: /export, /weekly-summary, /weather MUST come before /{log_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/daily-logs/export",
)
async def export_logs(
    project_id: uuid.UUID,
    from_date: str | None = Query(default=None, description="Start date filter (ISO format)"),
    to_date: str | None = Query(default=None, description="End date filter (ISO format)"),
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Export daily logs as CSV."""
    await verify_project_access(project_id, current_user, db)

    # Parse optional date range filters
    parsed_from_date = None
    parsed_to_date = None
    if from_date is not None:
        try:
            parsed_from_date = datetime.fromisoformat(from_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid from_date format: must be ISO 8601",
            )
    if to_date is not None:
        try:
            parsed_to_date = datetime.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid to_date format: must be ISO 8601",
            )

    query = (
        select(DailyLog).where(DailyLog.project_id == project_id).order_by(DailyLog.log_date.desc())
    )
    if parsed_from_date is not None:
        query = query.where(DailyLog.log_date >= parsed_from_date)
    if parsed_to_date is not None:
        query = query.where(DailyLog.log_date <= parsed_to_date)
    query = query.limit(5000)

    result = await db.execute(query)
    logs = list(result.scalars().all())
    csv_bytes = export_daily_logs_csv(logs)

    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=daily_logs_export.csv"},
    )


@router.get(
    "/{project_id}/daily-logs/{log_id}/export-pdf",
)
async def export_log_pdf(
    project_id: uuid.UUID,
    log_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Export a single daily log as a PDF daily report."""
    await verify_project_access(project_id, current_user, db)
    try:
        log_data = await get_daily_log_detail(db, log_id, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    pdf_bytes = generate_daily_log_pdf(log_data)
    log_date = log_data.get("log_date", "report")

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=daily_report_{log_date}.pdf"},
    )


@router.get(
    "/{project_id}/daily-logs/weekly-summary",
)
async def weekly_summary(
    project_id: uuid.UUID,
    week_start: date = Query(...),
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get weekly summary of daily logs."""
    await verify_project_access(project_id, current_user, db)
    return await get_weekly_summary(db, project_id, week_start)


@router.get(
    "/{project_id}/daily-logs/weather",
)
async def get_weather(
    project_id: uuid.UUID,
    log_date: date = Query(...),
    lat: float = Query(...),
    lon: float = Query(...),
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Auto-populate weather data for a date and location."""
    await verify_project_access(project_id, current_user, db)
    weather = await auto_populate_weather(lat, lon, log_date)
    return weather


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/daily-logs",
    response_model=DailyLogDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_log(
    project_id: uuid.UUID,
    request: DailyLogCreateV2,
    current_user: User = Depends(require_permission("daily_logs", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new daily log in draft status."""
    await verify_project_access(project_id, current_user, db)
    log = await create_daily_log(db, project_id, request.model_dump(), current_user.id)
    return log


@router.get(
    "/{project_id}/daily-logs",
    response_model=DailyLogDetailListResponse,
)
async def list_logs(
    project_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List daily logs with filters."""
    await verify_project_access(project_id, current_user, db)
    return await list_daily_logs(
        db,
        project_id,
        status=status_filter,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/{project_id}/daily-logs/{log_id}",
    response_model=DailyLogDetailResponse,
)
async def get_log(
    project_id: uuid.UUID,
    log_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get daily log detail."""
    await verify_project_access(project_id, current_user, db)
    try:
        return await get_daily_log_detail(db, log_id, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch(
    "/{project_id}/daily-logs/{log_id}",
    response_model=DailyLogDetailResponse,
)
async def patch_log(
    project_id: uuid.UUID,
    log_id: uuid.UUID,
    request: DailyLogUpdateV2,
    current_user: User = Depends(require_permission("daily_logs", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a draft daily log."""
    await verify_project_access(project_id, current_user, db)
    try:
        log = await update_daily_log(
            db,
            log_id,
            project_id,
            request.model_dump(exclude_unset=True),
        )
        return log
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/daily-logs/{log_id}/submit",
    response_model=DailyLogDetailResponse,
)
async def submit_log(
    project_id: uuid.UUID,
    log_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Submit a draft daily log for approval."""
    await verify_project_access(project_id, current_user, db)
    try:
        log = await submit_daily_log(db, log_id, project_id, current_user.id)
        return log
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/{project_id}/daily-logs/{log_id}/approve",
    response_model=DailyLogDetailResponse,
)
async def approve_log(
    project_id: uuid.UUID,
    log_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Approve a submitted daily log."""
    await verify_project_access(project_id, current_user, db)
    try:
        log = await approve_daily_log(db, log_id, project_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # IG-13: After approval, trigger daily report generation if one does not
    # already exist for this date. Fire-and-forget — never blocks approval.
    try:
        from app.models.generated_report import GeneratedDailyReport

        log_date = log.get("log_date") if isinstance(log, dict) else getattr(log, "log_date", None)
        if log_date is not None:
            if isinstance(log_date, str):
                log_date = date.fromisoformat(log_date)
            existing_report = await db.execute(
                select(GeneratedDailyReport).where(
                    GeneratedDailyReport.project_id == project_id,
                    GeneratedDailyReport.report_date == log_date,
                )
            )
            if existing_report.scalars().first() is None:
                import asyncio

                # Build the daily log dict for the report generator
                log_dict = log if isinstance(log, dict) else None
                # Store reference so the task isn't garbage-collected mid-flight (RUF006).
                # Attached to module-level registry below to keep a strong ref.
                _report_task = asyncio.ensure_future(
                    _trigger_report_generation(str(project_id), log_date, log_dict)
                )
                _BACKGROUND_TASKS.add(_report_task)
                _report_task.add_done_callback(_BACKGROUND_TASKS.discard)
                logger.info(
                    "Triggered daily report generation for project %s date %s",
                    project_id,
                    log_date,
                )
            else:
                logger.debug(
                    "Daily report already exists for project %s date %s; skipping generation",
                    project_id,
                    log_date,
                )
    except Exception:
        logger.warning(
            "Failed to trigger daily report generation after log approval",
            exc_info=True,
        )

    return log


async def _trigger_report_generation(
    project_id: str,
    report_date: date,
    daily_log: dict | None,
) -> None:
    """Fire-and-forget wrapper for daily report generation."""
    try:
        from app.services.communication.report_generator import generate_daily_report

        await generate_daily_report(
            project_id=project_id,
            report_date=report_date,
            daily_log=daily_log,
        )
    except Exception:
        logger.warning(
            "Background daily report generation failed for project %s date %s",
            project_id,
            report_date,
            exc_info=True,
        )


@router.post(
    "/{project_id}/daily-logs/{log_id}/reject",
    response_model=DailyLogDetailResponse,
)
async def reject_log(
    project_id: uuid.UUID,
    log_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_logs", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Reject a submitted daily log back to draft."""
    await verify_project_access(project_id, current_user, db)
    try:
        log = await reject_to_draft(db, log_id, project_id)
        return log
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Copy previous day
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/daily-logs/copy-previous",
    response_model=DailyLogDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def copy_previous(
    project_id: uuid.UUID,
    target_date: date = Query(...),
    current_user: User = Depends(require_permission("daily_logs", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new draft log by copying the previous day's template."""
    await verify_project_access(project_id, current_user, db)
    try:
        log = await copy_previous_day(db, project_id, target_date, current_user.id)
        return log
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
