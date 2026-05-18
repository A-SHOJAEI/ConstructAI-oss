"""Punch list v2 API endpoints.

All routes are project-scoped: ``/projects/{project_id}/punch-list/...``
"""

from __future__ import annotations

import io
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.field_management import PunchListItem
from app.models.user import User
from app.schemas.field_management import (
    PunchListBulkCreateRequest,
    PunchListBulkStatusUpdate,
    PunchListCreate,
    PunchListDetailListResponse,
    PunchListDetailResponse,
    PunchListItemCreateV2,
    PunchListItemUpdateV2,
    PunchListListResponse,
    PunchListResponse,
    PunchListStatsResponse,
    PunchListUpdate,
)
from app.services.productivity.punch_list_pdf import generate_punch_list_pdf
from app.services.productivity.punch_list_service import (
    bulk_create,
    bulk_status_update,
    create_punch_list_item,
    export_punch_list_csv,
    get_punch_list_item_detail,
    get_punch_list_stats,
    list_punch_list_items,
    update_punch_list_item,
)
from app.services.productivity.punch_list_service import (
    create_punch_list as create_punch_list_svc,
)
from app.services.productivity.punch_list_service import (
    get_punch_list_detail as get_punch_list_detail_svc,
)
from app.services.productivity.punch_list_service import (
    list_punch_lists as list_punch_lists_svc,
)
from app.services.productivity.punch_list_service import (
    update_punch_list as update_punch_list_svc,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Punch List (walkthrough) CRUD — must come before /{item_id}
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/punch-lists",
    response_model=PunchListResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_walkthrough(
    project_id: uuid.UUID,
    request: PunchListCreate,
    current_user: User = Depends(require_permission("punch_lists", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a punch list (walkthrough grouping)."""
    await verify_project_access(project_id, current_user, db)
    pl = await create_punch_list_svc(db, project_id, request.model_dump(), current_user.id)
    return pl


@router.get(
    "/{project_id}/punch-lists",
    response_model=PunchListListResponse,
)
async def list_walkthroughs(
    project_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("punch_lists", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List punch lists (walkthroughs) for a project."""
    await verify_project_access(project_id, current_user, db)
    return await list_punch_lists_svc(
        db, project_id, status=status_filter, cursor=cursor, limit=limit
    )


@router.get(
    "/{project_id}/punch-lists/{punch_list_id}",
    response_model=PunchListResponse,
)
async def get_walkthrough(
    project_id: uuid.UUID,
    punch_list_id: uuid.UUID,
    current_user: User = Depends(require_permission("punch_lists", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get punch list (walkthrough) detail."""
    await verify_project_access(project_id, current_user, db)
    try:
        return await get_punch_list_detail_svc(db, punch_list_id, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch(
    "/{project_id}/punch-lists/{punch_list_id}",
    response_model=PunchListResponse,
)
async def patch_walkthrough(
    project_id: uuid.UUID,
    punch_list_id: uuid.UUID,
    request: PunchListUpdate,
    current_user: User = Depends(require_permission("punch_lists", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a punch list (walkthrough)."""
    await verify_project_access(project_id, current_user, db)
    try:
        pl = await update_punch_list_svc(
            db, punch_list_id, project_id, request.model_dump(exclude_unset=True)
        )
        return pl
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# IMPORTANT: /export, /export-pdf, /stats, /bulk-* MUST come before /{item_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/punch-list/export-pdf",
)
async def export_items_pdf(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("punch_lists", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Export punch list items as PDF grouped by responsible subcontractor."""
    await verify_project_access(project_id, current_user, db)

    export_max_rows = 5000
    result = await db.execute(
        select(PunchListItem)
        .where(PunchListItem.project_id == project_id)
        .order_by(PunchListItem.created_at.desc())
        .limit(export_max_rows)
    )
    items = list(result.scalars().all())
    pdf_bytes = generate_punch_list_pdf(items)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=punch_list_report.pdf"},
    )


@router.get(
    "/{project_id}/punch-list/export",
)
async def export_items(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("punch_lists", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Export punch list items as CSV grouped by company."""
    await verify_project_access(project_id, current_user, db)

    export_max_rows = 5000
    result = await db.execute(
        select(PunchListItem)
        .where(PunchListItem.project_id == project_id)
        .order_by(PunchListItem.created_at.desc())
        .limit(export_max_rows)
    )
    items = list(result.scalars().all())
    csv_bytes = export_punch_list_csv(items)

    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=punch_list_export.csv"},
    )


@router.get(
    "/{project_id}/punch-list/stats",
    response_model=PunchListStatsResponse,
)
async def get_stats(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("punch_lists", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated punch list stats."""
    await verify_project_access(project_id, current_user, db)
    return await get_punch_list_stats(db, project_id)


@router.post(
    "/{project_id}/punch-list/bulk-create",
    status_code=status.HTTP_201_CREATED,
)
async def bulk_create_items(
    project_id: uuid.UUID,
    request: PunchListBulkCreateRequest,
    current_user: User = Depends(require_permission("punch_lists", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create multiple punch list items at once (max 50)."""
    await verify_project_access(project_id, current_user, db)
    items_data = [item.model_dump() for item in request.items]
    items = await bulk_create(db, project_id, items_data, current_user.id)
    return {
        "created": len(items),
        "items": [{"id": str(i.id), "item_number": i.item_number} for i in items],
    }


@router.post(
    "/{project_id}/punch-list/bulk-status-update",
)
async def bulk_update_status(
    project_id: uuid.UUID,
    request: PunchListBulkStatusUpdate,
    current_user: User = Depends(require_permission("punch_lists", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update status of multiple punch list items."""
    await verify_project_access(project_id, current_user, db)
    try:
        items = await bulk_status_update(db, project_id, request.item_ids, request.status)
        return {"updated": len(items)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/punch-list",
    response_model=PunchListDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    project_id: uuid.UUID,
    request: PunchListItemCreateV2,
    current_user: User = Depends(require_permission("punch_lists", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a single punch list item with auto-numbering."""
    await verify_project_access(project_id, current_user, db)
    item = await create_punch_list_item(db, project_id, request.model_dump(), current_user.id)
    return item


@router.get(
    "/{project_id}/punch-list",
    response_model=PunchListDetailListResponse,
)
async def list_items(
    project_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    priority: str | None = Query(default=None),
    category: str | None = Query(default=None),
    company: str | None = Query(default=None),
    assigned_to: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("punch_lists", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List punch list items with filters."""
    await verify_project_access(project_id, current_user, db)
    return await list_punch_list_items(
        db,
        project_id,
        status=status_filter,
        priority=priority,
        category=category,
        company=company,
        assigned_to=assigned_to,
        search=search,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/{project_id}/punch-list/{item_id}",
    response_model=PunchListDetailResponse,
)
async def get_item(
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    current_user: User = Depends(require_permission("punch_lists", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get punch list item detail."""
    await verify_project_access(project_id, current_user, db)
    try:
        return await get_punch_list_item_detail(db, item_id, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch(
    "/{project_id}/punch-list/{item_id}",
    response_model=PunchListDetailResponse,
)
async def patch_item(
    project_id: uuid.UUID,
    item_id: uuid.UUID,
    request: PunchListItemUpdateV2,
    current_user: User = Depends(require_permission("punch_lists", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a punch list item."""
    await verify_project_access(project_id, current_user, db)
    try:
        item = await update_punch_list_item(
            db,
            item_id,
            project_id,
            request.model_dump(exclude_unset=True),
        )
        return item
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
