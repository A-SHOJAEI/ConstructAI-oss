"""AI Plan Takeoff API endpoints."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.pagination import PaginationMeta
from app.schemas.plan_takeoff import (
    ConvertToEstimateRequest,
    ConvertToEstimateResponse,
    PlanTakeoffListResponse,
    PlanTakeoffResponse,
)
from app.services.estimating.plan_takeoff_service import (
    convert_takeoff_to_estimate,
    get_takeoff,
    list_takeoffs,
    process_plan_upload,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Maximum upload size: 50 MB
_MAX_UPLOAD_SIZE = 50 * 1024 * 1024


# ---------------------------------------------------------------------------
# Upload & Process
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=PlanTakeoffResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_plan_for_takeoff(
    project_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    drawing_type: str | None = Form(default=None),
    location_state: str | None = Form(default=None),
    location_region: str | None = Form(default=None),
    current_user: User = Depends(require_permission("estimating", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a construction plan PDF for AI-powered quantity takeoff.

    Processes the uploaded PDF through the plan takeoff pipeline:
    1. Parses PDF text and tables
    2. Extracts construction elements via LLM
    3. Maps elements to CSI MasterFormat codes
    4. Enriches with cost database pricing
    5. Applies regional cost factors

    Returns the completed takeoff with all line items.
    """
    await verify_project_access(project_id, current_user, db)

    # Validate file
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File name is required",
        )
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported",
        )

    # Read file bytes with size check
    file_bytes = await file.read()
    if len(file_bytes) > _MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds {_MAX_UPLOAD_SIZE // (1024 * 1024)} MB limit",
        )
    if len(file_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    # Build location dict
    location = None
    if location_state or location_region:
        location = {}
        if location_state:
            location["state"] = location_state
        if location_region:
            location["region"] = location_region

    try:
        takeoff = await process_plan_upload(
            db=db,
            project_id=project_id,
            file_bytes=file_bytes,
            file_name=file.filename,
            drawing_type=drawing_type,
            location=location,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except Exception:
        logger.exception("Plan takeoff failed for project %s", project_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Plan takeoff processing failed. Please try again.",
        )

    return takeoff


# ---------------------------------------------------------------------------
# List & Get
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=PlanTakeoffListResponse,
)
async def list_project_takeoffs(
    project_id: uuid.UUID = Query(...),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("estimating", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List plan takeoffs for a project."""
    await verify_project_access(project_id, current_user, db)

    takeoffs = await list_takeoffs(db, project_id, skip=skip, limit=limit + 1)
    has_more = len(takeoffs) > limit
    if has_more:
        takeoffs = takeoffs[:limit]

    return PlanTakeoffListResponse(
        data=takeoffs,
        meta=PaginationMeta(has_more=has_more),
    )


@router.get(
    "/{takeoff_id}",
    response_model=PlanTakeoffResponse,
)
async def get_plan_takeoff(
    takeoff_id: uuid.UUID,
    current_user: User = Depends(require_permission("estimating", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a plan takeoff with all line items."""
    takeoff = await get_takeoff(db, takeoff_id)
    if takeoff is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan takeoff not found",
        )
    await verify_project_access(takeoff.project_id, current_user, db)
    return takeoff


# ---------------------------------------------------------------------------
# Convert to Estimate
# ---------------------------------------------------------------------------


@router.post(
    "/{takeoff_id}/convert-to-estimate",
    response_model=ConvertToEstimateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def convert_plan_takeoff_to_estimate(
    takeoff_id: uuid.UUID,
    request: ConvertToEstimateRequest,
    current_user: User = Depends(require_permission("estimating", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Convert a completed plan takeoff into a cost estimate.

    Creates a new CostEstimate with EstimateLineItems from the takeoff's
    priced line items. Adds a configurable contingency percentage.
    """
    takeoff = await get_takeoff(db, takeoff_id)
    if takeoff is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan takeoff not found",
        )
    await verify_project_access(takeoff.project_id, current_user, db)

    try:
        estimate = await convert_takeoff_to_estimate(
            db=db,
            takeoff_id=takeoff_id,
            estimate_name=request.estimate_name,
            contingency_pct=request.contingency_pct,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    return ConvertToEstimateResponse(
        estimate_id=estimate.id,
        estimate_name=estimate.name,
        total_cost=estimate.total_cost,
        line_item_count=len(estimate.line_items) if estimate.line_items else 0,
        contingency_pct=estimate.contingency_pct,
        takeoff_status=takeoff.status,
    )
