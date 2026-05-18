"""Subcontractor portal API endpoints.

All routes enforce the SUBCONTRACTOR role via ``require_permission("sub_portal", ...)``.
Subcontractors can view their filtered SOV, submit manpower logs, delivery receipts,
and pay applications scoped to their assigned SOV line items.
"""

from __future__ import annotations

import logging
import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.pagination import PaginationMeta
from app.schemas.subcontractor import (
    DeliveryReceiptRequest,
    FilteredSOVItem,
    FilteredSOVResponse,
    ManpowerSubmissionRequest,
    PaymentStatusEntry,
    PaymentStatusResponse,
    ReviewSubmissionRequest,
    SubcontractorProfileCreate,
    SubcontractorProfileResponse,
    SubmissionListResponse,
    SubmissionResponse,
    SubPayApplicationRequest,
    TranslatedBriefingRequest,
    TranslatedBriefingResponse,
)
from app.services.productivity.subcontractor_service import (
    create_subcontractor_profile,
    get_filtered_sov,
    get_payment_status,
    get_subcontractor_profile,
    get_translated_safety_briefing,
    list_submissions,
    review_submission,
    submit_manpower,
    submit_sub_pay_application,
    upload_delivery_receipt,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_profile_or_404(db: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID):
    """Fetch the subcontractor profile or raise 404."""
    profile = await get_subcontractor_profile(db, user_id, project_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subcontractor profile not found for this project",
        )
    return profile


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/sub-portal/profile",
    response_model=SubcontractorProfileResponse,
)
async def get_profile(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("sub_portal", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the current user's subcontractor profile for a project."""
    await verify_project_access(project_id, current_user, db)
    profile = await _get_profile_or_404(db, current_user.id, project_id)
    return profile


@router.post(
    "/{project_id}/sub-portal/profile",
    response_model=SubcontractorProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    project_id: uuid.UUID,
    request: SubcontractorProfileCreate,
    current_user: User = Depends(require_permission("sub_portal", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a subcontractor profile for the current user on a project.

    Typically called by a project admin on behalf of the sub, or by the sub
    after being invited.
    """
    await verify_project_access(project_id, current_user, db)

    # Check for existing profile
    existing = await get_subcontractor_profile(db, current_user.id, project_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Subcontractor profile already exists for this project",
        )

    try:
        profile = await create_subcontractor_profile(
            db,
            user_id=current_user.id,
            project_id=project_id,
            company_name=request.company_name,
            trade=request.trade,
            sov_item_ids=request.sov_item_ids,
            contact_info=request.contact_info,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    return profile


# ---------------------------------------------------------------------------
# Manpower
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sub-portal/manpower",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_daily_manpower(
    project_id: uuid.UUID,
    request: ManpowerSubmissionRequest,
    current_user: User = Depends(require_permission("sub_portal", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Submit a daily manpower report."""
    await verify_project_access(project_id, current_user, db)
    profile = await _get_profile_or_404(db, current_user.id, project_id)

    try:
        submission = await submit_manpower(
            db,
            profile_id=profile.id,
            date_=request.date,
            workers_by_trade=request.workers_by_trade,
            total_hours=request.total_hours,
            notes=request.notes,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    return submission


# ---------------------------------------------------------------------------
# Delivery receipts
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sub-portal/deliveries",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_delivery_receipt(
    project_id: uuid.UUID,
    request: DeliveryReceiptRequest,
    current_user: User = Depends(require_permission("sub_portal", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a delivery receipt for materials received on site."""
    await verify_project_access(project_id, current_user, db)
    profile = await _get_profile_or_404(db, current_user.id, project_id)

    try:
        submission = await upload_delivery_receipt(
            db,
            profile_id=profile.id,
            material_description=request.material_description,
            quantity=request.quantity,
            unit=request.unit,
            supplier=request.supplier,
            delivery_date=request.delivery_date,
            document_url=request.document_url,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    return submission


# ---------------------------------------------------------------------------
# Filtered SOV (scope isolation)
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/sub-portal/scope",
    response_model=FilteredSOVResponse,
)
async def view_filtered_sov(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("sub_portal", "read")),
    db: AsyncSession = Depends(get_db),
):
    """View the Schedule of Values filtered to this subcontractor's scope.

    Returns only the SOV line items assigned to the subcontractor's profile.
    """
    await verify_project_access(project_id, current_user, db)
    profile = await _get_profile_or_404(db, current_user.id, project_id)

    try:
        items = await get_filtered_sov(db, project_id, profile.id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    return FilteredSOVResponse(data=cast(list[FilteredSOVItem], items))


# ---------------------------------------------------------------------------
# Sub pay application
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sub-portal/pay-app",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_pay_application(
    project_id: uuid.UUID,
    request: SubPayApplicationRequest,
    current_user: User = Depends(require_permission("sub_portal", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Submit a subcontractor pay application.

    All line items must reference SOV items within the sub's assigned scope.
    """
    await verify_project_access(project_id, current_user, db)
    profile = await _get_profile_or_404(db, current_user.id, project_id)

    line_items_dicts = [li.model_dump() for li in request.line_items]
    try:
        submission = await submit_sub_pay_application(
            db,
            profile_id=profile.id,
            line_items=line_items_dicts,
            period_to=request.period_to,
            notes=request.notes,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    return submission


# ---------------------------------------------------------------------------
# Payment status
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/sub-portal/payment-status",
    response_model=PaymentStatusResponse,
)
async def view_payment_status(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("sub_portal", "read")),
    db: AsyncSession = Depends(get_db),
):
    """View payment status for all submitted pay applications."""
    await verify_project_access(project_id, current_user, db)
    profile = await _get_profile_or_404(db, current_user.id, project_id)

    try:
        entries = await get_payment_status(db, profile.id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    return PaymentStatusResponse(
        data=[
            PaymentStatusEntry(
                period_to=e.period_to,
                submission_id=e.submission_id,
                submitted_amount=e.submitted_amount,
                approved_amount=e.approved_amount,
                paid_amount=e.paid_amount,
                retainage_held=e.retainage_held,
                status=e.status,
            )
            for e in entries
        ]
    )


# ---------------------------------------------------------------------------
# Translated safety briefing
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sub-portal/safety-briefing",
    response_model=TranslatedBriefingResponse,
)
async def translate_safety_briefing(
    project_id: uuid.UUID,
    request: TranslatedBriefingRequest,
    current_user: User = Depends(require_permission("sub_portal", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a safety briefing translated to the target language.

    Uses construction-domain-aware translation with safety_alert context
    for maximum clarity.
    """
    await verify_project_access(project_id, current_user, db)

    try:
        translated = await get_translated_safety_briefing(
            briefing_text=request.briefing_text,
            target_language=request.target_language,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    return TranslatedBriefingResponse(
        translated_text=translated,
        target_language=request.target_language,
    )


# ---------------------------------------------------------------------------
# Submissions list
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/sub-portal/submissions",
    response_model=SubmissionListResponse,
)
async def list_my_submissions(
    project_id: uuid.UUID,
    submission_type: str | None = Query(
        default=None,
        description="Filter by type: manpower, delivery_receipt, pay_application",
    ),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_permission("sub_portal", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List the current subcontractor's submissions with optional filters."""
    await verify_project_access(project_id, current_user, db)
    profile = await _get_profile_or_404(db, current_user.id, project_id)

    try:
        submissions, total = await list_submissions(
            db,
            profile_id=profile.id,
            submission_type=submission_type,
            skip=skip,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    return SubmissionListResponse(
        data=cast(list[SubmissionResponse], submissions),
        total=total,
        meta=PaginationMeta(has_more=(skip + limit) < total),
    )


# ---------------------------------------------------------------------------
# Review (project admin / PM only — routed via separate permission)
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sub-portal/submissions/{submission_id}/review",
    response_model=SubmissionResponse,
)
async def review_a_submission(
    project_id: uuid.UUID,
    submission_id: uuid.UUID,
    request: ReviewSubmissionRequest,
    current_user: User = Depends(require_permission("sub_portal", "approve")),
    db: AsyncSession = Depends(get_db),
):
    """Review (approve/reject) a subcontractor submission.

    Requires sub_portal:approve permission (typically PROJECT_ADMIN or PM).
    """
    await verify_project_access(project_id, current_user, db)

    try:
        submission = await review_submission(
            db,
            submission_id=submission_id,
            reviewed_by=current_user.id,
            status=request.status,
            notes=request.notes,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    return submission
