"""SiteScribe API endpoints — source management and daily report generation.

Routes for uploading field sources (photos, voice memos, text), generating
AI narratives, and managing the daily report lifecycle.
All routes are project-scoped: ``/projects/{project_id}/sitescribe/...``
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.sitescribe import (
    ReportSourceCreate,
    ReportSourceResponse,
    SiteScribeApproveRequest,
    SiteScribeDashboardResponse,
    SiteScribeGenerateRequest,
    SiteScribeReportResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Create report
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sitescribe/reports",
    response_model=SiteScribeReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_report(
    project_id: uuid.UUID,
    body: SiteScribeGenerateRequest,
    current_user: User = Depends(require_permission("daily_reports", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new draft daily report for the given date."""
    await verify_project_access(project_id, current_user, db)

    from app.services.products.sitescribe.service import (
        create_report as svc_create_report,
    )

    try:
        report = await svc_create_report(
            db=db,
            project_id=project_id,
            org_id=current_user.org_id,
            report_date=body.report_date,
            user_id=current_user.id,
        )
        return SiteScribeReportResponse(
            id=report.id,
            project_id=report.project_id,
            report_date=report.report_date,
            status=report.status,
            weather_data=None,
            manpower_data=[],
            work_performed=[],
            delays=[],
            deliveries=[],
            narrative_draft=report.content_markdown,
            narrative_final=None,
            sources=[],
            created_at=report.created_at,
            updated_at=report.updated_at,
        )
    except Exception as exc:
        logger.error("Failed to create SiteScribe report: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create report",
        ) from exc


# ---------------------------------------------------------------------------
# Source management
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sitescribe/reports/{report_id}/sources",
    response_model=ReportSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_source(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    body: ReportSourceCreate,
    current_user: User = Depends(require_permission("daily_reports", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a source (photo, voice memo, text) to a daily report."""
    await verify_project_access(project_id, current_user, db)

    from app.services.products.sitescribe.service import (
        upload_source as svc_upload_source,
    )

    try:
        source = await svc_upload_source(
            db=db,
            report_id=report_id,
            project_id=project_id,
            org_id=current_user.org_id,
            source_type=body.source_type,
            user_id=current_user.id,
            s3_key=body.s3_key,
            filename=body.filename,
            mime_type=body.mime_type,
            text_content=body.text_content,
        )
        return source
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "/{project_id}/sitescribe/reports/{report_id}/sources",
    response_model=list[ReportSourceResponse],
)
async def list_sources(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List all sources attached to a daily report."""
    await verify_project_access(project_id, current_user, db)

    from app.services.products.sitescribe.service import (
        list_sources as svc_list_sources,
    )

    return await svc_list_sources(db=db, report_id=report_id)


# ---------------------------------------------------------------------------
# Narrative generation
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sitescribe/reports/{report_id}/generate",
    response_model=SiteScribeReportResponse,
)
async def generate_narrative(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_reports", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Generate an AI narrative from all completed sources for the report."""
    await verify_project_access(project_id, current_user, db)

    from app.services.products.sitescribe.service import (
        generate_narrative as svc_generate_narrative,
    )
    from app.services.products.sitescribe.service import (
        list_sources as svc_list_sources,
    )

    try:
        report = await svc_generate_narrative(
            db=db,
            report_id=report_id,
            project_id=project_id,
            org_id=current_user.org_id,
        )
        sources = await svc_list_sources(db=db, report_id=report_id)
        return SiteScribeReportResponse(
            id=report.id,
            project_id=report.project_id,
            report_date=report.report_date,
            status=report.status,
            weather_data=report.sections.get("weather") if report.sections else None,
            manpower_data=report.sections.get("manpower", []) if report.sections else [],
            work_performed=report.sections.get("work_performed", []) if report.sections else [],
            delays=report.sections.get("delays", []) if report.sections else [],
            deliveries=report.sections.get("deliveries", []) if report.sections else [],
            narrative_draft=report.content_markdown,
            narrative_final=None,
            sources=[ReportSourceResponse.model_validate(s) for s in sources],
            created_at=report.created_at,
            updated_at=report.updated_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sitescribe/reports/{report_id}/approve",
    response_model=SiteScribeReportResponse,
)
async def approve_report(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    body: SiteScribeApproveRequest | None = None,
    current_user: User = Depends(require_permission("daily_reports", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Approve a daily report."""
    await verify_project_access(project_id, current_user, db)

    from app.services.products.sitescribe.service import (
        approve_report as svc_approve_report,
    )
    from app.services.products.sitescribe.service import (
        list_sources as svc_list_sources,
    )

    try:
        notes = body.reviewer_notes if body else None
        report = await svc_approve_report(
            db=db,
            report_id=report_id,
            project_id=project_id,
            user_id=current_user.id,
            reviewer_notes=notes,
        )
        sources = await svc_list_sources(db=db, report_id=report_id)
        return SiteScribeReportResponse(
            id=report.id,
            project_id=report.project_id,
            report_date=report.report_date,
            status=report.status,
            weather_data=None,
            manpower_data=[],
            work_performed=[],
            delays=[],
            deliveries=[],
            narrative_draft=report.content_markdown,
            narrative_final=report.content_markdown if report.status == "approved" else None,
            sources=[ReportSourceResponse.model_validate(s) for s in sources],
            created_at=report.created_at,
            updated_at=report.updated_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Report retrieval
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/sitescribe/reports/{report_id}",
    response_model=SiteScribeReportResponse,
)
async def get_report(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single daily report with all its sources."""
    await verify_project_access(project_id, current_user, db)

    from app.services.products.sitescribe.service import get_report_with_sources

    try:
        data = await get_report_with_sources(
            db=db,
            report_id=report_id,
            project_id=project_id,
        )
        report = data["report"]
        sources = data["sources"]
        return SiteScribeReportResponse(
            id=report.id,
            project_id=report.project_id,
            report_date=report.report_date,
            status=report.status,
            weather_data=None,
            manpower_data=[],
            work_performed=[],
            delays=[],
            deliveries=[],
            narrative_draft=report.content_markdown,
            narrative_final=report.content_markdown if report.status == "approved" else None,
            sources=[ReportSourceResponse.model_validate(s) for s in sources],
            created_at=report.created_at,
            updated_at=report.updated_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/{project_id}/sitescribe/reports",
    response_model=list[SiteScribeReportResponse],
)
async def list_reports(
    project_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    current_user: User = Depends(require_permission("daily_reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List daily reports with optional filters and pagination."""
    await verify_project_access(project_id, current_user, db)

    from app.services.products.sitescribe.service import (
        list_reports as svc_list_reports,
    )
    from app.services.products.sitescribe.service import (
        list_sources as svc_list_sources,
    )

    reports, _total = await svc_list_reports(
        db=db,
        project_id=project_id,
        status=status_filter,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )

    results: list[SiteScribeReportResponse] = []
    for report in reports:
        sources = await svc_list_sources(db=db, report_id=report.id)
        results.append(
            SiteScribeReportResponse(
                id=report.id,
                project_id=report.project_id,
                report_date=report.report_date,
                status=report.status,
                weather_data=None,
                manpower_data=[],
                work_performed=[],
                delays=[],
                deliveries=[],
                narrative_draft=report.content_markdown,
                narrative_final=(report.content_markdown if report.status == "approved" else None),
                sources=[ReportSourceResponse.model_validate(s) for s in sources],
                created_at=report.created_at,
                updated_at=report.updated_at,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/sitescribe/dashboard",
    response_model=SiteScribeDashboardResponse,
)
async def get_sitescribe_dashboard(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("daily_reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get SiteScribe dashboard metrics for the project."""
    await verify_project_access(project_id, current_user, db)

    from app.services.products.sitescribe.service import (
        get_dashboard as svc_get_dashboard,
    )

    data = await svc_get_dashboard(db=db, project_id=project_id)
    return SiteScribeDashboardResponse(**data)
