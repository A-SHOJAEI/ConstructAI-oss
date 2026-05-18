"""Automated daily report API endpoints.

Routes for generating, listing, editing, approving, and saving
AI-generated daily construction reports.
All routes are project-scoped: ``/projects/{project_id}/daily-reports/...``
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.generated_report import GeneratedDailyReport
from app.models.user import User
from app.schemas.daily_report import (
    DailyReportApproveRequest,
    DailyReportEditRequest,
    DailyReportGenerateRequest,
    GeneratedDailyReportListResponse,
    GeneratedDailyReportResponse,
    SaveAsLogResponse,
)
from app.schemas.pagination import PaginationMeta

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Generate a new daily report
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/daily-reports/generate",
    response_model=GeneratedDailyReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_report(
    project_id: uuid.UUID,
    request: DailyReportGenerateRequest,
    current_user: User = Depends(require_permission("daily_reports", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Generate an AI daily report for the specified date.

    Aggregates data from weather, safety, workforce, equipment, deliveries,
    schedule, and quality sources, then produces a narrative via LLM.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.reporting.daily_report_generator import create_daily_report

    try:
        report = await create_daily_report(
            db=db,
            project_id=project_id,
            report_date=request.report_date,
            generated_by=current_user.id,
        )
        return report
    except Exception as exc:
        logger.error("Report generation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate daily report",
        )


# ---------------------------------------------------------------------------
# List reports
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/daily-reports",
    response_model=GeneratedDailyReportListResponse,
)
async def list_reports(
    project_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("daily_reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List generated daily reports with optional filters."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(GeneratedDailyReport)
        .where(GeneratedDailyReport.project_id == project_id)
        .order_by(GeneratedDailyReport.report_date.desc())
    )

    if status_filter:
        query = query.where(GeneratedDailyReport.status == status_filter)
    if date_from:
        query = query.where(GeneratedDailyReport.report_date >= date_from)
    if date_to:
        query = query.where(GeneratedDailyReport.report_date <= date_to)

    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
            cursor_obj = await db.get(GeneratedDailyReport, cursor_uuid)
            if cursor_obj:
                query = query.where(GeneratedDailyReport.report_date < cursor_obj.report_date)
        except ValueError:
            pass

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return GeneratedDailyReportListResponse(
        data=cast(list[GeneratedDailyReportResponse], items),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


# ---------------------------------------------------------------------------
# Get single report
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/daily-reports/{report_id}",
    response_model=GeneratedDailyReportResponse,
)
async def get_report(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single generated daily report."""
    await verify_project_access(project_id, current_user, db)

    result = await db.execute(
        select(GeneratedDailyReport).where(
            GeneratedDailyReport.id == report_id,
            GeneratedDailyReport.project_id == project_id,
        )
    )
    report = result.scalars().first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


# ---------------------------------------------------------------------------
# Edit report narrative
# ---------------------------------------------------------------------------


@router.patch(
    "/{project_id}/daily-reports/{report_id}",
    response_model=GeneratedDailyReportResponse,
)
async def edit_report(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    request: DailyReportEditRequest,
    current_user: User = Depends(require_permission("daily_reports", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Edit the narrative of a draft report."""
    await verify_project_access(project_id, current_user, db)

    result = await db.execute(
        select(GeneratedDailyReport).where(
            GeneratedDailyReport.id == report_id,
            GeneratedDailyReport.project_id == project_id,
        )
    )
    report = result.scalars().first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.status == "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot edit an approved report",
        )

    report.narrative_markdown = request.narrative_markdown
    report.status = "reviewed"
    report.reviewed_by = current_user.id
    await db.flush()
    await db.refresh(report)
    return report


# ---------------------------------------------------------------------------
# Approve report
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/daily-reports/{report_id}/approve",
    response_model=GeneratedDailyReportResponse,
)
async def approve_report(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    request: DailyReportApproveRequest | None = None,
    current_user: User = Depends(require_permission("daily_reports", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Approve a generated daily report (optionally with edits)."""
    await verify_project_access(project_id, current_user, db)

    from app.services.reporting.daily_report_generator import review_and_approve_report

    try:
        edits = request.edits if request else None
        report = await review_and_approve_report(
            db=db,
            report_id=report_id,
            approved_by=current_user.id,
            edits=edits,
        )
        return report
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Save as daily log
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/daily-reports/{report_id}/save-as-log",
    response_model=SaveAsLogResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_as_log(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_reports", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Save an approved report as an official DailyLog record."""
    await verify_project_access(project_id, current_user, db)

    from app.services.reporting.daily_report_generator import save_report_as_daily_log

    try:
        daily_log = await save_report_as_daily_log(db=db, report_id=report_id)
        return SaveAsLogResponse(
            daily_log_id=daily_log.id,
            report_id=report_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
