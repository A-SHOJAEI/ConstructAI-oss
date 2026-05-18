"""CloseoutIQ API endpoints — spec-driven closeout tracking and warranty management."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.closeout import (
    CloseoutDashboardResponse,
    CloseoutDocumentRequestCreate,
    CloseoutGenerateRequest,
    CloseoutRequirementListResponse,
    CloseoutRequirementResponse,
    CloseoutRequirementUpdate,
    CloseoutReviewRequest,
    WarrantyCheckResponse,
    WarrantyClaimCreate,
    WarrantyClaimResponse,
    WarrantyRecordResponse,
)
from app.services.products.closeout_iq.service import (
    file_warranty_claim,
    generate_requirements,
    get_dashboard,
    list_requirements,
    list_warranties,
    review_document,
    send_document_request,
    update_requirement,
    warranty_check,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Generate closeout requirements from spec
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/closeout/generate",
    response_model=list[CloseoutRequirementResponse],
    status_code=status.HTTP_201_CREATED,
)
async def generate_closeout_requirements(
    project_id: uuid.UUID,
    request: CloseoutGenerateRequest,
    current_user: User = Depends(require_permission("closeout", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Generate closeout requirements by parsing a project specification document."""
    await verify_project_access(project_id, current_user, db)

    try:
        requirements = await generate_requirements(
            db,
            project_id=project_id,
            org_id=current_user.org_id,
            spec_document_id=request.spec_document_id,
        )
    except Exception as exc:
        logger.error("Failed to generate closeout requirements: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate closeout requirements",
        ) from exc

    return [CloseoutRequirementResponse.model_validate(r) for r in requirements]


# ---------------------------------------------------------------------------
# List / update closeout requirements
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/closeout/requirements",
    response_model=CloseoutRequirementListResponse,
)
async def list_closeout_requirements(
    project_id: uuid.UUID,
    req_status: str | None = Query(default=None, alias="status"),
    csi_division: str | None = Query(default=None),
    responsible_sub_id: uuid.UUID | None = Query(default=None),
    overdue_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    current_user: User = Depends(require_permission("closeout", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List closeout requirements with optional filtering and pagination."""
    await verify_project_access(project_id, current_user, db)

    items, total = await list_requirements(
        db,
        project_id,
        status=req_status,
        csi_division=csi_division,
        responsible_sub_id=responsible_sub_id,
        overdue_only=overdue_only,
        page=page,
        page_size=page_size,
    )

    return CloseoutRequirementListResponse(
        data=[CloseoutRequirementResponse.model_validate(r) for r in items],
        total=total,
    )


@router.patch(
    "/{project_id}/closeout/requirements/{requirement_id}",
    response_model=CloseoutRequirementResponse,
)
async def update_closeout_requirement(
    project_id: uuid.UUID,
    requirement_id: uuid.UUID,
    request: CloseoutRequirementUpdate,
    current_user: User = Depends(require_permission("closeout", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update fields on a closeout requirement."""
    await verify_project_access(project_id, current_user, db)

    try:
        updated = await update_requirement(
            db,
            requirement_id=requirement_id,
            project_id=project_id,
            updates=request.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return CloseoutRequirementResponse.model_validate(updated)


# ---------------------------------------------------------------------------
# Document request (magic link) & review
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/closeout/requirements/{requirement_id}/request",
    status_code=status.HTTP_201_CREATED,
)
async def send_closeout_document_request(
    project_id: uuid.UUID,
    requirement_id: uuid.UUID,
    request: CloseoutDocumentRequestCreate,
    current_user: User = Depends(require_permission("closeout", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Send a magic-link document request to a subcontractor."""
    await verify_project_access(project_id, current_user, db)

    try:
        result = await send_document_request(
            db,
            project_id=project_id,
            requirement_id=requirement_id,
            user_id=current_user.id,
            recipient_email=request.recipient_email,
            recipient_name=request.recipient_name,
            message=request.message,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return result


@router.post(
    "/{project_id}/closeout/requirements/{requirement_id}/review",
    response_model=CloseoutRequirementResponse,
)
async def review_closeout_document(
    project_id: uuid.UUID,
    requirement_id: uuid.UUID,
    request: CloseoutReviewRequest,
    current_user: User = Depends(require_permission("closeout", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Accept or reject a submitted closeout document."""
    await verify_project_access(project_id, current_user, db)

    try:
        reviewed = await review_document(
            db,
            requirement_id=requirement_id,
            project_id=project_id,
            accepted=request.accepted,
            reviewer_id=current_user.id,
            notes=request.notes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return CloseoutRequirementResponse.model_validate(reviewed)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/closeout/dashboard",
    response_model=CloseoutDashboardResponse,
)
async def get_closeout_dashboard(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("closeout", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get closeout progress dashboard with analytics."""
    await verify_project_access(project_id, current_user, db)

    dashboard = await get_dashboard(db, project_id)
    return CloseoutDashboardResponse(**dashboard)


# ---------------------------------------------------------------------------
# Warranties
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/closeout/warranties",
    response_model=list[WarrantyRecordResponse],
)
async def list_project_warranties(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("closeout", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List all warranty records for a project."""
    await verify_project_access(project_id, current_user, db)

    warranties = await list_warranties(db, project_id)
    return [WarrantyRecordResponse.model_validate(w) for w in warranties]


@router.get(
    "/{project_id}/closeout/warranty-check",
    response_model=WarrantyCheckResponse,
)
async def check_expiring_warranties(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("closeout", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Check for warranties expiring within 90 days or already expired."""
    await verify_project_access(project_id, current_user, db)

    result = await warranty_check(db, project_id)
    return WarrantyCheckResponse(
        expiring_soon=[WarrantyRecordResponse.model_validate(w) for w in result["expiring_soon"]],
        expired=[WarrantyRecordResponse.model_validate(w) for w in result["expired"]],
    )


@router.post(
    "/{project_id}/closeout/warranties/{warranty_id}/claims",
    response_model=WarrantyClaimResponse,
    status_code=status.HTTP_201_CREATED,
)
async def file_claim(
    project_id: uuid.UUID,
    warranty_id: uuid.UUID,
    request: WarrantyClaimCreate,
    current_user: User = Depends(require_permission("closeout", "create")),
    db: AsyncSession = Depends(get_db),
):
    """File a warranty claim against a warranty record."""
    await verify_project_access(project_id, current_user, db)

    try:
        claim = await file_warranty_claim(
            db,
            warranty_id=warranty_id,
            issue_description=request.issue_description,
            photos=request.photos,
            reporter_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return WarrantyClaimResponse.model_validate(claim)
