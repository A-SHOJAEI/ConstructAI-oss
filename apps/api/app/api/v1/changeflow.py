"""ChangeFlow T&M API endpoints.

Provides field-captured T&M entry management, pricing summaries,
COR generation from aggregated T&M data, negotiation tracking,
and a project-level dashboard.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.changeflow import (
    ChangeFlowDashboardResponse,
    CorGenerateRequest,
    CorNegotiationResponse,
    NegotiationCreate,
    PricingSummaryResponse,
    TmEntryCreate,
    TmEntryResponse,
    TmSummaryResponse,
)
from app.services.products.changeflow.service import (
    add_tm_entry,
    calculate_pricing_summary,
    generate_cor,
    get_dashboard,
    get_tm_summary,
    list_negotiations,
    list_tm_entries,
    record_negotiation,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# T&M entries
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/changeflow/tm-entries",
    response_model=TmEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tm_entry(
    project_id: uuid.UUID,
    request: TmEntryCreate,
    change_event_id: uuid.UUID | None = None,
    current_user: User = Depends(require_permission("change_orders", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a T&M line item for a project.

    Optionally link to a change event via ``change_event_id`` query param.
    """
    project = await verify_project_access(project_id, current_user, db)

    entry = await add_tm_entry(
        db,
        project_id=project_id,
        org_id=project.org_id,
        change_event_id=change_event_id,
        data=request.model_dump(exclude_unset=False),
        user_id=current_user.id,
    )
    return entry


@router.get(
    "/{project_id}/changeflow/events/{change_event_id}/tm-entries",
    response_model=list[TmEntryResponse],
)
async def get_event_tm_entries(
    project_id: uuid.UUID,
    change_event_id: uuid.UUID,
    current_user: User = Depends(require_permission("change_orders", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List all T&M entries for a change event."""
    await verify_project_access(project_id, current_user, db)
    entries = await list_tm_entries(db, change_event_id)
    return entries


@router.get(
    "/{project_id}/changeflow/events/{change_event_id}/tm-summary",
    response_model=TmSummaryResponse,
)
async def get_event_tm_summary(
    project_id: uuid.UUID,
    change_event_id: uuid.UUID,
    current_user: User = Depends(require_permission("change_orders", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated T&M summary for a change event."""
    await verify_project_access(project_id, current_user, db)
    summary = await get_tm_summary(db, change_event_id)
    return TmSummaryResponse(**summary)


@router.get(
    "/{project_id}/changeflow/events/{change_event_id}/pricing",
    response_model=PricingSummaryResponse,
)
async def get_event_pricing(
    project_id: uuid.UUID,
    change_event_id: uuid.UUID,
    current_user: User = Depends(require_permission("change_orders", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get full pricing breakdown with markup cascade for a change event."""
    await verify_project_access(project_id, current_user, db)
    entries = await list_tm_entries(db, change_event_id)
    pricing = calculate_pricing_summary(entries)
    return PricingSummaryResponse(**pricing)


# ---------------------------------------------------------------------------
# COR generation
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/changeflow/cors/generate",
    status_code=status.HTTP_201_CREATED,
)
async def generate_cor_from_tm(
    project_id: uuid.UUID,
    request: CorGenerateRequest,
    current_user: User = Depends(require_permission("change_orders", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a COR from aggregated T&M entries for a change event."""
    project = await verify_project_access(project_id, current_user, db)

    try:
        cor_data = await generate_cor(
            db,
            project_id=project_id,
            org_id=project.org_id,
            change_event_id=request.change_event_id,
            subject=request.subject,
            user_id=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    return cor_data


# ---------------------------------------------------------------------------
# Negotiation tracking
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/changeflow/cors/{cor_id}/negotiate",
    response_model=CorNegotiationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def negotiate_cor(
    project_id: uuid.UUID,
    cor_id: uuid.UUID,
    request: NegotiationCreate,
    current_user: User = Depends(require_permission("change_orders", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Record a negotiation action on a COR."""
    await verify_project_access(project_id, current_user, db)

    negotiation = await record_negotiation(
        db,
        cor_id=cor_id,
        action=request.action,
        amount=request.amount,
        notes=request.notes,
        user_id=current_user.id,
    )
    return negotiation


@router.get(
    "/{project_id}/changeflow/cors/{cor_id}/negotiations",
    response_model=list[CorNegotiationResponse],
)
async def get_cor_negotiations(
    project_id: uuid.UUID,
    cor_id: uuid.UUID,
    current_user: User = Depends(require_permission("change_orders", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List negotiation history for a COR."""
    await verify_project_access(project_id, current_user, db)
    negotiations = await list_negotiations(db, cor_id)
    return negotiations


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/changeflow/dashboard",
    response_model=ChangeFlowDashboardResponse,
)
async def get_changeflow_dashboard(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("change_orders", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get ChangeFlow dashboard metrics for a project."""
    await verify_project_access(project_id, current_user, db)
    dashboard = await get_dashboard(db, project_id)
    return ChangeFlowDashboardResponse(**dashboard)
