"""Change order lifecycle API endpoints (PCO / COR)."""

from __future__ import annotations

import csv
import io
import logging
import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.change_order_lifecycle import (
    ChangeOrderRequest,
    PotentialChangeOrder,
)
from app.models.evm import ChangeOrder
from app.models.user import User
from app.schemas.change_order_lifecycle import (
    CORCreate,
    CORListResponse,
    CORResponse,
    CORUpdate,
    PCOCreate,
    PCOListResponse,
    PCOResponse,
    PCOUpdate,
)
from app.schemas.controls import ChangeOrderResponse
from app.schemas.pagination import PaginationMeta
from app.services.controls.change_order_lifecycle import (
    approve_cor_to_co,
    create_cor,
    create_pco,
    get_cumulative_co_impact,
    update_cor,
    update_pco,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# PCO endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/pcos",
    response_model=PCOResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_potential_change_order(
    request: PCOCreate,
    current_user: User = Depends(require_permission("change_orders", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a Potential Change Order with AI risk analysis."""
    await verify_project_access(request.project_id, current_user, db)

    cb = request.cost_breakdown
    pco = await create_pco(
        db,
        project_id=request.project_id,
        title=request.title,
        description=request.description,
        change_type=request.change_type,
        originated_by=current_user.id,
        labor_cost=cb.labor_cost,
        material_cost=cb.material_cost,
        equipment_cost=cb.equipment_cost,
        subcontractor_cost=cb.subcontractor_cost,
        overhead_cost=cb.overhead_cost,
        profit_markup_pct=cb.profit_markup_pct,
        schedule_impact_days=request.schedule_impact_days,
        spec_section=request.spec_section,
        drawing_reference=request.drawing_reference,
        attachments=request.attachments,
    )
    return pco


@router.get(
    "/pcos",
    response_model=PCOListResponse,
)
async def list_pcos(
    project_id: uuid.UUID = Query(...),
    pco_status: str | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0, description="Records to skip (offset pagination)"),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("change_orders", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List PCOs for a project, optionally filtered by status.

    Supports both cursor-based and offset-based pagination. When *cursor*
    is provided it takes precedence; otherwise *skip*/*limit* are applied.
    """
    await verify_project_access(project_id, current_user, db)

    query = (
        select(PotentialChangeOrder)
        .where(PotentialChangeOrder.project_id == project_id)
        .order_by(PotentialChangeOrder.pco_number.asc())
    )
    if pco_status:
        query = query.where(PotentialChangeOrder.status == pco_status)
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_pco = await db.get(PotentialChangeOrder, cursor_uuid)
        if cursor_pco:
            query = query.where(PotentialChangeOrder.pco_number > cursor_pco.pco_number)
    elif skip > 0:
        query = query.offset(skip)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    pcos = list(result.scalars().all())

    has_more = len(pcos) > limit
    if has_more:
        pcos = pcos[:limit]

    next_cursor = str(pcos[-1].id) if has_more and pcos else None
    return PCOListResponse(
        data=cast(list[PCOResponse], pcos),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


@router.get(
    "/pcos/{pco_id}",
    response_model=PCOResponse,
)
async def get_pco(
    pco_id: uuid.UUID,
    current_user: User = Depends(require_permission("change_orders", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a PCO by ID."""
    pco = await db.get(PotentialChangeOrder, pco_id)
    if pco is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PCO not found")
    await verify_project_access(pco.project_id, current_user, db)
    return pco


@router.patch(
    "/pcos/{pco_id}",
    response_model=PCOResponse,
)
async def update_potential_change_order(
    pco_id: uuid.UUID,
    request: PCOUpdate,
    current_user: User = Depends(require_permission("change_orders", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update PCO fields or transition status."""
    pco = await db.get(PotentialChangeOrder, pco_id, with_for_update=True)
    if pco is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PCO not found")
    await verify_project_access(pco.project_id, current_user, db)

    update_data = request.model_dump(exclude_unset=True)
    if (
        "cost_breakdown" in update_data
        and update_data["cost_breakdown"] is not None
        and request.cost_breakdown is not None
    ):
        update_data["cost_breakdown"] = request.cost_breakdown.model_dump()

    if request.status is not None and pco.status != request.status:
        update_data["reviewed_by"] = current_user.id

    try:
        updated = await update_pco(db, pco, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return updated


# ---------------------------------------------------------------------------
# COR endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/cors",
    response_model=CORResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_change_order_request(
    request: CORCreate,
    current_user: User = Depends(require_permission("change_orders", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a COR from approved PCOs."""
    await verify_project_access(request.project_id, current_user, db)

    try:
        cor = await create_cor(
            db,
            project_id=request.project_id,
            title=request.title,
            pco_ids=request.pco_ids,
            description=request.description,
            markup_pct=request.markup_pct,
            overhead_pct=request.overhead_pct,
            cor_adjustment=request.cor_adjustment,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    # Build response with pco_ids
    return _cor_to_response(cor)


@router.get(
    "/cors",
    response_model=CORListResponse,
)
async def list_cors(
    project_id: uuid.UUID = Query(...),
    cor_status: str | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0, description="Records to skip (offset pagination)"),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("change_orders", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List CORs for a project.

    Supports both cursor-based and offset-based pagination.
    """
    await verify_project_access(project_id, current_user, db)

    query = (
        select(ChangeOrderRequest)
        .where(ChangeOrderRequest.project_id == project_id)
        .order_by(ChangeOrderRequest.cor_number.asc())
    )
    if cor_status:
        query = query.where(ChangeOrderRequest.status == cor_status)
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_cor = await db.get(ChangeOrderRequest, cursor_uuid)
        if cursor_cor:
            query = query.where(ChangeOrderRequest.cor_number > cursor_cor.cor_number)
    elif skip > 0:
        query = query.offset(skip)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    cors = list(result.scalars().all())

    has_more = len(cors) > limit
    if has_more:
        cors = cors[:limit]

    next_cursor = str(cors[-1].id) if has_more and cors else None
    data = [_cor_to_response(c) for c in cors]
    return CORListResponse(
        data=data,
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


@router.get(
    "/cors/{cor_id}",
    response_model=CORResponse,
)
async def get_cor(
    cor_id: uuid.UUID,
    current_user: User = Depends(require_permission("change_orders", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a COR by ID."""
    cor = await db.get(ChangeOrderRequest, cor_id)
    if cor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="COR not found")
    await verify_project_access(cor.project_id, current_user, db)
    return _cor_to_response(cor)


@router.patch(
    "/cors/{cor_id}",
    response_model=CORResponse,
)
async def update_change_order_request(
    cor_id: uuid.UUID,
    request: CORUpdate,
    current_user: User = Depends(require_permission("change_orders", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update COR fields or transition status."""
    cor = await db.get(ChangeOrderRequest, cor_id, with_for_update=True)
    if cor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="COR not found")
    await verify_project_access(cor.project_id, current_user, db)

    try:
        updated = await update_cor(db, cor, **request.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return _cor_to_response(updated)


@router.post(
    "/cors/{cor_id}/approve",
    response_model=ChangeOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def approve_change_order_request(
    cor_id: uuid.UUID,
    current_user: User = Depends(require_permission("change_orders", "approve")),
    db: AsyncSession = Depends(get_db),
):
    """Approve a COR, generating a Change Order and updating SOV."""
    cor = await db.get(ChangeOrderRequest, cor_id, with_for_update=True)
    if cor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="COR not found")
    await verify_project_access(cor.project_id, current_user, db)

    try:
        co = await approve_cor_to_co(db, cor_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return co


# ---------------------------------------------------------------------------
# Change Order CSV Export
# ---------------------------------------------------------------------------


@router.get("/{project_id}/change-orders/export")
async def export_change_orders(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("change_orders", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Export all change orders for a project as a CSV file.

    Columns: co_number, title, description, cost_impact, schedule_impact_days,
    status, change_type, created_at.
    """
    await verify_project_access(project_id, current_user, db)

    query = (
        select(ChangeOrder)
        .where(ChangeOrder.project_id == project_id)
        .order_by(ChangeOrder.co_number.asc())
    )
    result = await db.execute(query)
    change_orders = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "co_number",
            "title",
            "description",
            "cost_impact",
            "schedule_impact_days",
            "status",
            "change_type",
            "created_at",
        ]
    )
    for co in change_orders:
        writer.writerow(
            [
                co.co_number,
                co.title,
                co.description,
                str(co.cost_impact),
                co.schedule_impact_days,
                co.status,
                co.change_type,
                co.created_at.isoformat() if co.created_at else "",
            ]
        )

    csv_bytes = output.getvalue().encode("utf-8")
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=change_orders_export.csv"},
    )


# ---------------------------------------------------------------------------
# Cumulative impact
# ---------------------------------------------------------------------------


@router.get("/change-orders/cumulative-impact")
async def get_change_order_cumulative_impact(
    project_id: uuid.UUID = Query(...),
    current_user: User = Depends(require_permission("change_orders", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get cumulative CO impact summary for a project."""
    await verify_project_access(project_id, current_user, db)
    return await get_cumulative_co_impact(db, project_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cor_to_response(cor: ChangeOrderRequest) -> CORResponse:
    """Convert COR model to response, flattening pco_links to pco_ids."""
    pco_ids = [link.pco_id for link in (cor.pco_links or [])]
    return CORResponse(
        id=cor.id,
        project_id=cor.project_id,
        cor_number=cor.cor_number,
        title=cor.title,
        description=cor.description,
        status=cor.status,
        markup_pct=cor.markup_pct,
        overhead_pct=cor.overhead_pct,
        cor_adjustment=cor.cor_adjustment,
        total_cost=cor.total_cost,
        schedule_impact_days=cor.schedule_impact_days,
        pco_ids=pco_ids,
        submitted_to=cor.submitted_to,
        approved_by=cor.approved_by,
        submitted_at=cor.submitted_at,
        approved_at=cor.approved_at,
        created_at=cor.created_at,
        updated_at=cor.updated_at,
    )
