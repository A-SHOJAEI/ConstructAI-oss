"""CarbonLens API endpoints — LEED v5 embodied carbon tracking."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.carbon import (
    CarbonConfigResponse,
    CarbonConfigUpdate,
    CarbonDashboardResponse,
    CarbonReportResponse,
    EpdResponse,
    EpdUploadRequest,
    EpdVerifyRequest,
    GwpCalculationResponse,
    MaterialCreate,
    MaterialResponse,
    MaterialUpdate,
    ReportGenerateRequest,
    ScenarioInput,
    ScenarioResponse,
)
from app.services.products.carbonlens import service as carbonlens

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@router.patch(
    "/{project_id}/carbon/config",
    response_model=CarbonConfigResponse,
)
async def update_carbon_config(
    project_id: uuid.UUID,
    request: CarbonConfigUpdate,
    current_user: User = Depends(require_permission("carbon", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Create or update the carbon configuration for a project."""
    await verify_project_access(project_id, current_user, db)
    config = await carbonlens.configure_project(
        db,
        project_id,
        current_user.org_id,
        request.model_dump(exclude_none=True),
    )
    return CarbonConfigResponse.model_validate(config)


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/carbon/materials",
    response_model=MaterialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_material(
    project_id: uuid.UUID,
    request: MaterialCreate,
    current_user: User = Depends(require_permission("carbon", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Add a material to the carbon inventory."""
    await verify_project_access(project_id, current_user, db)
    material = await carbonlens.add_material(
        db, project_id, current_user.org_id, request.model_dump()
    )
    return MaterialResponse.model_validate(material)


@router.patch(
    "/{project_id}/carbon/materials/{material_id}",
    response_model=MaterialResponse,
)
async def update_material(
    project_id: uuid.UUID,
    material_id: uuid.UUID,
    request: MaterialUpdate,
    current_user: User = Depends(require_permission("carbon", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a material in the carbon inventory."""
    await verify_project_access(project_id, current_user, db)
    try:
        material = await carbonlens.update_material(
            db, material_id, project_id, request.model_dump(exclude_none=True)
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MaterialResponse.model_validate(material)


@router.get(
    "/{project_id}/carbon/materials",
    response_model=list[MaterialResponse],
)
async def list_materials(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("carbon", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List all materials in the carbon inventory."""
    await verify_project_access(project_id, current_user, db)
    materials = await carbonlens.list_materials(db, project_id)
    return [MaterialResponse.model_validate(m) for m in materials]


# ---------------------------------------------------------------------------
# EPDs
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/carbon/epds",
    response_model=EpdResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_epd(
    project_id: uuid.UUID,
    request: EpdUploadRequest,
    current_user: User = Depends(require_permission("carbon", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create an EPD record (PDF already in S3)."""
    await verify_project_access(project_id, current_user, db)
    epd = await carbonlens.upload_epd(
        db,
        project_id,
        current_user.org_id,
        request.supplier,
        request.product_name,
        request.pdf_s3_key,
    )
    return EpdResponse.model_validate(epd)


@router.post(
    "/{project_id}/carbon/epds/{epd_id}/parse",
    response_model=EpdResponse,
)
async def parse_epd(
    project_id: uuid.UUID,
    epd_id: uuid.UUID,
    current_user: User = Depends(require_permission("carbon", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Parse an EPD PDF using LLM to extract GWP data.

    Note: In a full implementation, the PDF text would be extracted from S3.
    For now, the endpoint triggers extraction with any available text.
    """
    await verify_project_access(project_id, current_user, db)
    try:
        epd = await carbonlens.parse_epd(db, epd_id, "", project_id, current_user.org_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return EpdResponse.model_validate(epd)


@router.patch(
    "/{project_id}/carbon/epds/{epd_id}/verify",
    response_model=EpdResponse,
)
async def verify_epd(
    project_id: uuid.UUID,
    epd_id: uuid.UUID,
    request: EpdVerifyRequest,
    current_user: User = Depends(require_permission("carbon", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Verify or reject an EPD."""
    await verify_project_access(project_id, current_user, db)
    if not request.verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set verified=true to verify an EPD.",
        )
    try:
        epd = await carbonlens.verify_epd(db, epd_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return EpdResponse.model_validate(epd)


@router.get(
    "/{project_id}/carbon/epds",
    response_model=list[EpdResponse],
)
async def list_epds(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("carbon", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List all EPD records for a project."""
    await verify_project_access(project_id, current_user, db)
    epds = await carbonlens.list_epds(db, project_id)
    return [EpdResponse.model_validate(e) for e in epds]


# ---------------------------------------------------------------------------
# GWP Calculation & Scenario Modelling
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/carbon/gwp",
    response_model=GwpCalculationResponse,
)
async def get_gwp(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("carbon", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Calculate current project GWP."""
    await verify_project_access(project_id, current_user, db)
    gwp_data = await carbonlens.calculate_gwp(db, project_id)
    return GwpCalculationResponse(**gwp_data)


@router.post(
    "/{project_id}/carbon/scenario",
    response_model=ScenarioResponse,
)
async def run_scenario(
    project_id: uuid.UUID,
    request: ScenarioInput,
    current_user: User = Depends(require_permission("carbon", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Run a what-if scenario: change one material's GWP per unit."""
    await verify_project_access(project_id, current_user, db)
    try:
        result = await carbonlens.model_scenario(
            db, project_id, request.material_id, request.new_gwp_per_unit
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ScenarioResponse(**result)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/carbon/reports",
    response_model=CarbonReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_report(
    project_id: uuid.UUID,
    request: ReportGenerateRequest,
    current_user: User = Depends(require_permission("carbon", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a LEED carbon report."""
    await verify_project_access(project_id, current_user, db)
    report = await carbonlens.generate_mrp2_report(
        db, project_id, current_user.org_id, user_id=current_user.id
    )
    return CarbonReportResponse.model_validate(report)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/carbon/dashboard",
    response_model=CarbonDashboardResponse,
)
async def get_dashboard(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("carbon", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated carbon dashboard data."""
    await verify_project_access(project_id, current_user, db)
    data = await carbonlens.get_dashboard(db, project_id)
    return CarbonDashboardResponse(**data)
