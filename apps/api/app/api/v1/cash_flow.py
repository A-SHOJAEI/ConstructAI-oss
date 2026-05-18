"""Predictive Cash Flow API endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.cash_flow import (
    CashFlowConfigRequest,
    CashFlowForecastResponse,
    LienWaiverAnalysisResponse,
    LienWaiverCreate,
    LienWaiverListResponse,
    LienWaiverResponse,
    LienWaiverUpdate,
)
from app.schemas.pagination import PaginationMeta
from app.services.controls.cash_flow_service import (
    create_lien_waiver,
    evaluate_project_lien_coverage,
    generate_cash_flow_forecast,
    get_cash_flow_history,
    list_lien_waivers,
    update_lien_waiver,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Cash Flow Forecast
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/cash-flow/forecast",
    response_model=CashFlowForecastResponse,
    status_code=status.HTTP_200_OK,
)
async def generate_forecast(
    project_id: uuid.UUID,
    config: CashFlowConfigRequest | None = None,
    current_user: User = Depends(require_permission("controls", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a cash flow forecast for a project.

    Combines SOV, pay application history, schedule activities, and change orders
    to produce a monthly cash flow projection with optional Monte Carlo confidence intervals.
    """
    await verify_project_access(project_id, current_user, db)

    cfg = config.model_dump() if config else {}
    try:
        forecast_data = await generate_cash_flow_forecast(
            db, project_id, config=cfg, created_by=current_user.id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    return forecast_data


@router.get(
    "/{project_id}/cash-flow/history",
)
async def get_history(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("controls", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get historical monthly cash flow data from pay applications."""
    await verify_project_access(project_id, current_user, db)

    history = await get_cash_flow_history(db, project_id)
    return {"project_id": str(project_id), "monthly_history": history}


# ---------------------------------------------------------------------------
# Lien Waivers
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/lien-waivers",
    response_model=LienWaiverResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_waiver(
    project_id: uuid.UUID,
    request: LienWaiverCreate,
    current_user: User = Depends(require_permission("controls", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new lien waiver for a project."""
    await verify_project_access(project_id, current_user, db)

    try:
        waiver = await create_lien_waiver(db, project_id, request.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return waiver


@router.get(
    "/{project_id}/lien-waivers",
    response_model=LienWaiverListResponse,
)
async def list_waivers(
    project_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_permission("controls", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List lien waivers for a project with optional status filter and pagination."""
    await verify_project_access(project_id, current_user, db)

    try:
        waivers, total = await list_lien_waivers(
            db, project_id, status=status_filter, skip=skip, limit=limit
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    has_more = (skip + limit) < total
    return LienWaiverListResponse(
        data=cast(list[LienWaiverResponse], waivers),
        meta=PaginationMeta(has_more=has_more),
    )


@router.put(
    "/{project_id}/lien-waivers/{waiver_id}",
    response_model=LienWaiverResponse,
)
async def update_waiver(
    project_id: uuid.UUID,
    waiver_id: uuid.UUID,
    request: LienWaiverUpdate,
    current_user: User = Depends(require_permission("controls", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a lien waiver's status or metadata."""
    await verify_project_access(project_id, current_user, db)

    # Verify the waiver belongs to this project before updating
    from app.models.cash_flow import LienWaiver

    waiver_check = await db.get(LienWaiver, waiver_id)
    if waiver_check is None or str(waiver_check.project_id) != str(project_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lien waiver not found",
        )

    update_data = request.model_dump(exclude_unset=True)
    try:
        waiver = await update_lien_waiver(db, waiver_id, update_data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return waiver


@router.get(
    "/{project_id}/lien-waivers/analysis",
    response_model=LienWaiverAnalysisResponse,
)
async def analyze_lien_coverage(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("controls", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Analyze lien waiver coverage against pay applications.

    Returns coverage percentage, missing waivers, and overdue deadlines.
    """
    await verify_project_access(project_id, current_user, db)

    analysis = await evaluate_project_lien_coverage(db, project_id)
    return LienWaiverAnalysisResponse(
        coverage_pct=str(analysis.coverage_pct),
        missing_waivers=analysis.missing_waivers,
        upcoming_deadlines=analysis.upcoming_deadlines,
    )
