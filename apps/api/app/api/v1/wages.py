"""WageGuard: Davis-Bacon prevailing wage compliance API endpoints."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.wage_compliance import (
    ApprenticeshipStatusResponse,
    AuditPackageResponse,
    ClassificationMapRequest,
    ClassificationMapResponse,
    PayrollCreate,
    PayrollLineItemCreate,
    PayrollLineItemResponse,
    PayrollResponse,
    PayrollStatusUpdate,
    SubInviteRequest,
    ValidationResult,
    WageConfigResponse,
    WageConfigUpdate,
    WageDeterminationResponse,
)
from app.services.products.wageguard.service import (
    add_line_item,
    configure_project,
    create_payroll,
    generate_audit_package,
    get_apprenticeship_status,
    list_payrolls,
    map_classification,
    search_determinations,
    seed_determinations,
    update_payroll_status,
    validate_payroll,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/{project_id}/wages/determinations",
    response_model=list[WageDeterminationResponse],
)
async def get_wage_determinations(
    project_id: uuid.UUID,
    state: str | None = Query(default=None),
    county: str | None = Query(default=None),
    project_type: str | None = Query(default=None),
    current_user: User = Depends(require_permission("wages", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Search wage determinations for a project, optionally filtered."""
    await verify_project_access(project_id, current_user, db)

    # Seed determinations on first access
    await seed_determinations(db)

    results = await search_determinations(db, state=state, county=county, project_type=project_type)
    return results


@router.patch(
    "/{project_id}/wages/config",
    response_model=WageConfigResponse,
)
async def update_wage_config(
    project_id: uuid.UUID,
    request: WageConfigUpdate,
    current_user: User = Depends(require_permission("wages", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Create or update the wage compliance configuration for a project."""
    await verify_project_access(project_id, current_user, db)
    config = await configure_project(
        db,
        project_id,
        current_user.org_id,
        request.model_dump(exclude_unset=True),
    )
    return config


@router.post(
    "/{project_id}/wages/classify",
    response_model=ClassificationMapResponse,
)
async def classify_worker(
    project_id: uuid.UUID,
    request: ClassificationMapRequest,
    current_user: User = Depends(require_permission("wages", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Map a company classification to the closest Davis-Bacon classification."""
    await verify_project_access(project_id, current_user, db)
    result = await map_classification(
        request.company_classification,
        request.project_type,
    )
    return result


@router.post(
    "/{project_id}/wages/payrolls",
    response_model=PayrollResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_new_payroll(
    project_id: uuid.UUID,
    request: PayrollCreate,
    current_user: User = Depends(require_permission("wages", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new certified payroll record."""
    await verify_project_access(project_id, current_user, db)
    payroll = await create_payroll(
        db,
        project_id,
        current_user.org_id,
        request.contractor_name,
        request.week_ending,
    )
    return payroll


@router.post(
    "/{project_id}/wages/payrolls/{payroll_id}/items",
    response_model=PayrollLineItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_payroll_line_item(
    project_id: uuid.UUID,
    payroll_id: uuid.UUID,
    request: PayrollLineItemCreate,
    current_user: User = Depends(require_permission("wages", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Add a worker line item to a payroll."""
    await verify_project_access(project_id, current_user, db)
    line_item = await add_line_item(
        db,
        payroll_id,
        project_id,
        request.model_dump(),
    )
    return line_item


@router.post(
    "/{project_id}/wages/payrolls/{payroll_id}/validate",
    response_model=ValidationResult,
)
async def validate_payroll_endpoint(
    project_id: uuid.UUID,
    payroll_id: uuid.UUID,
    current_user: User = Depends(require_permission("wages", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Validate a payroll for Davis-Bacon compliance."""
    await verify_project_access(project_id, current_user, db)
    result = await validate_payroll(db, payroll_id, project_id)
    return result


@router.patch(
    "/{project_id}/wages/payrolls/{payroll_id}",
    response_model=PayrollResponse,
)
async def update_payroll(
    project_id: uuid.UUID,
    payroll_id: uuid.UUID,
    request: PayrollStatusUpdate,
    current_user: User = Depends(require_permission("wages", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update the status of a payroll."""
    await verify_project_access(project_id, current_user, db)
    try:
        payroll = await update_payroll_status(
            db,
            payroll_id,
            project_id,
            request.status,
            user_id=current_user.id,
            notes=request.review_notes,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return payroll


@router.get(
    "/{project_id}/wages/payrolls",
    response_model=list[PayrollResponse],
)
async def get_payrolls(
    project_id: uuid.UUID,
    contractor_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(require_permission("wages", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List payrolls for a project."""
    await verify_project_access(project_id, current_user, db)
    payrolls = await list_payrolls(db, project_id, contractor_id=contractor_id)
    return payrolls


@router.get(
    "/{project_id}/wages/apprenticeship",
    response_model=ApprenticeshipStatusResponse,
)
async def get_apprenticeship(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("wages", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get apprenticeship compliance status for a project."""
    await verify_project_access(project_id, current_user, db)
    status_data = await get_apprenticeship_status(db, project_id)
    return status_data


@router.post(
    "/{project_id}/wages/invite-sub",
    status_code=status.HTTP_201_CREATED,
)
async def invite_subcontractor(
    project_id: uuid.UUID,
    request: SubInviteRequest,
    current_user: User = Depends(require_permission("wages", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Invite a subcontractor to submit payrolls for this project."""
    await verify_project_access(project_id, current_user, db)
    # Placeholder: in production, this would send an email with a magic link
    return {
        "message": f"Invitation sent to {request.email} for {request.contractor_name}",
        "project_id": str(project_id),
    }


@router.post(
    "/{project_id}/wages/audit-package",
    response_model=AuditPackageResponse,
)
async def get_audit_package(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("wages", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a compliance audit package for the project."""
    await verify_project_access(project_id, current_user, db)
    package = await generate_audit_package(db, project_id)
    return package
