"""Quality management API endpoints."""

from __future__ import annotations

import csv
import io
import logging
import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.quality import (
    NCR,
    ComplianceCheck,
    DefectReport,
    Inspection,
)
from app.models.user import User
from app.schemas.pagination import PaginationMeta
from app.schemas.quality import (
    ComplianceChecklistItem,
    ComplianceChecklistListResponse,
    ComplianceCheckListResponse,
    ComplianceChecklistSummary,
    ComplianceCheckResponse,
    DefectReportCreate,
    DefectReportListResponse,
    DefectReportResponse,
    InspectionCreate,
    InspectionListResponse,
    InspectionResponse,
    NCRCreate,
    NCRListResponse,
    NCRResponse,
)
from app.services.quality.compliance_checker import (
    get_checklist_by_id,
    get_checklist_summary,
    get_checklists,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/inspections",
    response_model=InspectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_inspection(
    request: InspectionCreate,
    current_user: User = Depends(require_permission("quality", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new inspection record."""
    await verify_project_access(request.project_id, current_user, db)

    inspection = Inspection(
        project_id=request.project_id,
        inspection_type=request.inspection_type,
        inspector_id=current_user.id,
        location=request.location,
        checklist_data=request.checklist_data,
        scheduled_at=request.scheduled_at,
    )
    db.add(inspection)
    await db.flush()
    await db.refresh(inspection)
    return inspection


@router.get(
    "/inspections",
    response_model=InspectionListResponse,
)
async def list_inspections(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0, description="Records to skip (offset pagination)"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_permission("quality", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List inspections for a project.

    Supports both cursor-based and offset-based pagination.  When *cursor*
    is provided it takes precedence; otherwise *skip*/*limit* are applied.
    """
    await verify_project_access(project_id, current_user, db)

    query = (
        select(Inspection)
        .where(Inspection.project_id == project_id)
        .order_by(Inspection.created_at.desc())
    )
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(
            Inspection,
            cursor_uuid,
        )
        if cursor_obj:
            query = query.where(Inspection.created_at < cursor_obj.created_at)
    elif skip > 0:
        query = query.offset(skip)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return InspectionListResponse(
        data=cast(list[InspectionResponse], items),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )


@router.post(
    "/defects",
    response_model=DefectReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_defect_report(
    request: DefectReportCreate,
    current_user: User = Depends(require_permission("quality", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a defect report."""
    await verify_project_access(request.project_id, current_user, db)

    defect = DefectReport(
        project_id=request.project_id,
        inspection_id=request.inspection_id,
        defect_type=request.defect_type,
        severity=request.severity,
        description=request.description,
        location=request.location,
        image_urls=request.image_urls,
    )
    db.add(defect)
    await db.flush()
    await db.refresh(defect)
    return defect


@router.get(
    "/defects",
    response_model=DefectReportListResponse,
)
async def list_defect_reports(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0, description="Records to skip (offset pagination)"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_permission("quality", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List defect reports for a project.

    Supports both cursor-based and offset-based pagination.  When *cursor*
    is provided it takes precedence; otherwise *skip*/*limit* are applied.
    """
    await verify_project_access(project_id, current_user, db)

    query = (
        select(DefectReport)
        .where(DefectReport.project_id == project_id)
        .order_by(DefectReport.created_at.desc())
    )
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(
            DefectReport,
            cursor_uuid,
        )
        if cursor_obj:
            query = query.where(DefectReport.created_at < cursor_obj.created_at)
    elif skip > 0:
        query = query.offset(skip)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return DefectReportListResponse(
        data=cast(list[DefectReportResponse], items),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )


@router.post(
    "/ncrs",
    response_model=NCRResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ncr(
    request: NCRCreate,
    current_user: User = Depends(require_permission("quality", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a Non-Conformance Report."""
    await verify_project_access(request.project_id, current_user, db)

    ncr = NCR(
        project_id=request.project_id,
        ncr_number=request.ncr_number,
        title=request.title,
        description=request.description,
        severity=request.severity,
        reported_by=current_user.id,
    )
    db.add(ncr)
    await db.flush()
    await db.refresh(ncr)
    return ncr


@router.get("/ncrs", response_model=NCRListResponse)
async def list_ncrs(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("quality", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List NCRs for a project."""
    await verify_project_access(project_id, current_user, db)

    query = select(NCR).where(NCR.project_id == project_id).order_by(NCR.created_at.desc())
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(NCR, cursor_uuid)
        if cursor_obj:
            query = query.where(NCR.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return NCRListResponse(
        data=cast(list[NCRResponse], items),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )


@router.get(
    "/compliance",
    response_model=ComplianceCheckListResponse,
)
async def list_compliance_checks(
    project_id: uuid.UUID = Query(...),
    limit: int = Query(50, ge=1, le=100),
    cursor: uuid.UUID | None = Query(None),
    current_user: User = Depends(require_permission("quality", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List compliance checks for a project with pagination."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(ComplianceCheck)
        .where(ComplianceCheck.project_id == project_id)
        .order_by(ComplianceCheck.created_at.desc(), ComplianceCheck.id.desc())
    )
    if cursor:
        query = query.where(ComplianceCheck.id < cursor)
    query = query.limit(limit + 1)

    result = await db.execute(query)
    items = list(result.scalars().all())
    has_more = len(items) > limit
    if has_more:
        items = items[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None

    return ComplianceCheckListResponse(
        data=cast(list[ComplianceCheckResponse], items),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )


# ---------------------------------------------------------------------------
# Compliance checklists from seed data
# ---------------------------------------------------------------------------


@router.get(
    "/compliance-checklists",
    response_model=ComplianceChecklistListResponse,
)
async def list_compliance_checklists(
    category: str | None = Query(
        default=None,
        description="Filter by category: osha_safety, ibc_inspection, "
        "environmental_swppp, quality_control",
    ),
    severity: str | None = Query(
        default=None,
        description="Filter by severity: critical, major, minor",
    ),
    phase: str | None = Query(
        default=None,
        description="Filter by construction phase: preconstruction, sitework, "
        "foundation, structure, rough_in, finishes, closeout",
    ),
    project_type: str | None = Query(
        default=None,
        description="Filter by project type: commercial, residential, "
        "industrial, infrastructure, renovation",
    ),
    _current_user: User = Depends(require_permission("quality", "read")),
):
    """List compliance checklists from curated seed data.

    Returns filterable checklist items covering OSHA safety, IBC inspections,
    environmental/SWPPP, and quality control checks.
    """
    items = get_checklists(
        category=category,
        severity=severity,
        phase=phase,
        project_type=project_type,
    )
    return ComplianceChecklistListResponse(
        data=cast(list[ComplianceChecklistItem], items), total=len(items)
    )


@router.get(
    "/compliance-checklists/summary",
    response_model=ComplianceChecklistSummary,
)
async def compliance_checklist_summary(
    _current_user: User = Depends(require_permission("quality", "read")),
):
    """Get summary statistics of compliance checklists."""
    return get_checklist_summary()


@router.get(
    "/compliance-checklists/{check_id}",
    response_model=ComplianceChecklistItem,
)
async def get_compliance_checklist(
    check_id: str,
    _current_user: User = Depends(require_permission("quality", "read")),
):
    """Get a single compliance checklist item by check_id."""
    item = get_checklist_by_id(check_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Checklist '{check_id}' not found")
    return item


# ---------------------------------------------------------------------------
# CSV Export (AC-16)
# ---------------------------------------------------------------------------


@router.get("/{project_id}/quality/inspections/export")
async def export_quality_inspections(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("quality", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Export all quality inspections for a project as a CSV file."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(Inspection)
        .where(Inspection.project_id == project_id)
        .order_by(Inspection.created_at.desc())
    )
    result = await db.execute(query)
    inspections = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "inspection_type",
            "status",
            "location",
            "inspector_id",
            "score",
            "scheduled_at",
            "completed_at",
            "created_at",
        ]
    )
    for insp in inspections:
        writer.writerow(
            [
                str(insp.id),
                insp.inspection_type,
                insp.status,
                insp.location,
                str(insp.inspector_id) if insp.inspector_id else "",
                str(insp.score) if insp.score else "",
                insp.scheduled_at.isoformat() if insp.scheduled_at else "",
                insp.completed_at.isoformat() if insp.completed_at else "",
                insp.created_at.isoformat() if insp.created_at else "",
            ]
        )

    csv_bytes = output.getvalue().encode("utf-8")
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=quality_inspections_export.csv"},
    )


# ---------------------------------------------------------------------------
# Bulk Defect Status Update (AC-20)
# ---------------------------------------------------------------------------


class BulkDefectUpdate(BaseModel):
    defect_id: uuid.UUID
    status: str = Field(..., pattern="^(open|in_progress|resolved|closed|deferred)$")


class BulkDefectUpdateRequest(BaseModel):
    updates: list[BulkDefectUpdate] = Field(..., max_length=100)


@router.post("/{project_id}/quality/defects/bulk-update")
async def bulk_update_defect_status(
    project_id: uuid.UUID,
    request: BulkDefectUpdateRequest,
    current_user: User = Depends(require_permission("quality", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Bulk update defect report statuses.

    Accepts a validated request with a list of defect_id/status pairs.
    Returns counts of updated and failed records.
    """
    await verify_project_access(project_id, current_user, db)

    updated = 0
    failed = 0
    errors: list[dict] = []

    for entry in request.updates:
        defect = await db.get(DefectReport, entry.defect_id)
        if defect is None or defect.project_id != project_id:
            failed += 1
            errors.append({"defect_id": str(entry.defect_id), "error": "Not found"})
            continue

        defect.status = entry.status
        updated += 1

    await db.flush()

    return {"updated": updated, "failed": failed, "errors": errors}
