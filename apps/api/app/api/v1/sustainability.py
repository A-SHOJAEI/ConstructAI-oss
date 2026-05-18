"""API endpoints for LEED v5 sustainability tracking and embodied carbon calculations.

Routes for carbon analysis, material tracking, LEED credit evaluation,
and the full sustainability dashboard.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.sustainability import CarbonFactor, ProjectSustainability
from app.models.user import User
from app.schemas.sustainability import (
    CarbonCalcRequest,
    LEEDEvalRequest,
    RecycledContentUpdate,
    SalvagedMaterialsUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Carbon Factor browsing (in-memory + DB)
# ---------------------------------------------------------------------------


@router.get("/carbon-factors")
async def list_carbon_factors(
    division: str | None = Query(default=None, description="Filter by CSI division prefix"),
    search: str | None = Query(default=None, description="Search material name"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_permission("estimates", "read")),
):
    """Browse available embodied carbon factors from ICE/CLF/EPD sources."""
    from app.services.estimating.carbon_database import CARBON_FACTORS

    results = []
    for code, factor in CARBON_FACTORS.items():
        if division and not code.startswith(division):
            continue
        if search and search.lower() not in factor.material_name.lower():
            continue
        results.append(
            {
                "csi_code": factor.csi_code,
                "material_name": factor.material_name,
                "embodied_carbon_kgco2e": factor.embodied_carbon_kgco2e,
                "unit": factor.unit,
                "data_source": factor.data_source,
                "gwp_category": factor.gwp_category,
                "notes": factor.notes,
            }
        )
        if len(results) >= limit:
            break

    return {"data": results, "total": len(results)}


@router.get("/{project_id}/sustainability/carbon-factors")
async def list_project_carbon_factors(
    project_id: uuid.UUID,
    csi_code: str | None = Query(default=None, description="Filter by CSI code prefix"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(require_permission("projects", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List carbon factors from database, optionally filtered by CSI code."""
    await verify_project_access(project_id, current_user, db)

    query = select(CarbonFactor).order_by(CarbonFactor.csi_code)
    if csi_code:
        query = query.where(CarbonFactor.csi_code.startswith(csi_code))

    result = await db.execute(query.offset(skip).limit(limit))
    factors = list(result.scalars().all())
    return {
        "data": [
            {
                "id": str(f.id),
                "csi_code": f.csi_code,
                "material_name": f.material_name,
                "embodied_carbon_kgco2e": float(f.embodied_carbon_kgco2e),
                "unit": f.unit,
                "data_source": f.data_source,
                "gwp_category": f.gwp_category,
            }
            for f in factors
        ],
        "count": len(factors),
    }


# ---------------------------------------------------------------------------
# Embodied Carbon calculation
# ---------------------------------------------------------------------------


@router.post("/carbon/calculate")
async def calculate_carbon(
    request: CarbonCalcRequest,
    current_user: User = Depends(require_permission("estimates", "read")),
):
    """Calculate embodied carbon for a set of line items.

    Matches each line item CSI code against the carbon factor database and
    returns total kgCO2e, per-division breakdown, and per-item details.
    """
    from app.services.estimating.carbon_database import calculate_embodied_carbon

    line_items = [item.model_dump() for item in request.line_items]
    result = calculate_embodied_carbon(
        line_items=line_items,
        gross_area_sf=request.gross_area_sf,
    )

    return {
        "total_kgco2e": result.total_kgco2e,
        "total_tonco2e": result.total_tonco2e,
        "carbon_per_sf": result.carbon_per_sf,
        "by_division": result.by_division,
        "by_item": result.by_item,
        "item_count": result.item_count,
        "unmatched_items": result.unmatched_items,
        "gross_area_sf": result.gross_area_sf,
    }


# ---------------------------------------------------------------------------
# LEED v5 Credit Evaluation
# ---------------------------------------------------------------------------


@router.post("/leed/evaluate")
async def evaluate_leed_credits_endpoint(
    request: LEEDEvalRequest,
    current_user: User = Depends(require_permission("estimates", "read")),
):
    """Evaluate LEED v5 credit eligibility based on project data.

    Returns credit-by-credit evaluation with achievability status,
    earned points, and supporting evidence.
    """
    from app.services.estimating.carbon_database import (
        evaluate_leed_credits as _evaluate,
    )

    project_data = request.model_dump()
    project_data["type"] = request.project_type

    credits = _evaluate(
        project_data=project_data,
        recycled_content_pct=request.recycled_content_pct,
    )

    credit_responses = [
        {
            "credit_id": c.credit_id,
            "credit_name": c.credit_name,
            "category": c.category,
            "max_points": c.max_points,
            "status": c.status,
            "earned_points": c.earned_points,
            "reasoning": c.reasoning,
            "requirements": c.requirements,
            "evidence": c.evidence,
        }
        for c in credits
    ]

    total_earned = sum(c.earned_points for c in credits)
    max_possible = sum(c.max_points for c in credits)

    if total_earned >= 80:
        cert_level = "platinum"
    elif total_earned >= 60:
        cert_level = "gold"
    elif total_earned >= 50:
        cert_level = "silver"
    elif total_earned >= 40:
        cert_level = "certified"
    else:
        cert_level = "none"

    return {
        "credits": credit_responses,
        "total_earned_points": total_earned,
        "max_possible_points": max_possible,
        "certification_level": cert_level,
    }


# ---------------------------------------------------------------------------
# Project Sustainability Dashboard
# ---------------------------------------------------------------------------


@router.get("/{project_id}/sustainability/dashboard")
async def get_sustainability_dashboard(
    project_id: uuid.UUID,
    gross_area_sf: float | None = Query(default=None, description="Building gross area in SF"),
    current_user: User = Depends(require_permission("estimates", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the full sustainability dashboard for a project.

    Calculates embodied carbon from estimate line items, evaluates LEED
    credits, and compares against CLF baselines.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.estimating.carbon_database import calculate_project_sustainability

    stmt = select(ProjectSustainability).where(ProjectSustainability.project_id == project_id)
    result = await db.execute(stmt)
    existing = result.scalars().first()

    salvaged: list[dict] = list(existing.salvaged_materials) if existing else []
    recycled_pct = float(existing.recycled_content_pct) if existing else 0.0

    dashboard = await calculate_project_sustainability(
        db=db,
        project_id=str(project_id),
        gross_area_sf=gross_area_sf,
        salvaged_materials=salvaged,
        recycled_content_pct=recycled_pct,
    )

    return {
        "project_id": dashboard.project_id,
        "total_embodied_carbon_kgco2e": dashboard.total_embodied_carbon_kgco2e,
        "carbon_per_sf": dashboard.carbon_per_sf,
        "baseline_comparison_pct": dashboard.baseline_comparison_pct,
        "embodied_carbon": {
            "total_kgco2e": dashboard.embodied_carbon.total_kgco2e,
            "total_tonco2e": dashboard.embodied_carbon.total_tonco2e,
            "carbon_per_sf": dashboard.embodied_carbon.carbon_per_sf,
            "by_division": dashboard.embodied_carbon.by_division,
            "item_count": dashboard.embodied_carbon.item_count,
            "unmatched_items": dashboard.embodied_carbon.unmatched_items,
        },
        "leed_credits": [
            {
                "credit_id": c.credit_id,
                "credit_name": c.credit_name,
                "category": c.category,
                "status": c.status,
                "earned_points": c.earned_points,
                "max_points": c.max_points,
                "reasoning": c.reasoning,
            }
            for c in dashboard.leed_credits
        ],
        "salvaged_materials": dashboard.salvaged_materials,
        "recycled_content_pct": dashboard.recycled_content_pct,
        "total_leed_points": dashboard.total_leed_points,
        "max_possible_points": dashboard.max_possible_points,
        "calculated_at": dashboard.calculated_at,
    }


@router.get("/{project_id}/sustainability/summary")
async def get_sustainability_summary(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("projects", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the stored sustainability summary for a project."""
    await verify_project_access(project_id, current_user, db)

    result = await db.execute(
        select(ProjectSustainability).where(ProjectSustainability.project_id == project_id)
    )
    ps = result.scalars().first()

    if ps is None:
        return {
            "project_id": str(project_id),
            "total_embodied_carbon_kgco2e": 0,
            "carbon_per_sf": None,
            "salvaged_materials": [],
            "recycled_content_pct": 0,
            "leed_credits": [],
            "baseline_comparison_pct": None,
        }

    return {
        "project_id": str(project_id),
        "id": str(ps.id),
        "total_embodied_carbon_kgco2e": float(ps.total_embodied_carbon_kgco2e),
        "carbon_per_sf": float(ps.carbon_per_sf) if ps.carbon_per_sf else None,
        "salvaged_materials": ps.salvaged_materials,
        "recycled_content_pct": float(ps.recycled_content_pct),
        "leed_credits": ps.leed_credits,
        "energy_data": ps.energy_data,
        "baseline_comparison_pct": (
            float(ps.baseline_comparison_pct) if ps.baseline_comparison_pct else None
        ),
        "last_calculated": ps.last_calculated.isoformat() if ps.last_calculated else None,
    }


# ---------------------------------------------------------------------------
# Salvaged Materials tracking
# ---------------------------------------------------------------------------


@router.put("/{project_id}/sustainability/salvaged-materials")
async def update_salvaged_materials(
    project_id: uuid.UUID,
    request: SalvagedMaterialsUpdate,
    current_user: User = Depends(require_permission("estimates", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Track salvaged/reused materials for LEED MR credit."""
    await verify_project_access(project_id, current_user, db)

    stmt = select(ProjectSustainability).where(ProjectSustainability.project_id == project_id)
    result = await db.execute(stmt)
    ps = result.scalars().first()

    if ps is None:
        ps = ProjectSustainability(project_id=project_id)
        db.add(ps)

    ps.salvaged_materials = [m.model_dump() for m in request.salvaged_materials]
    await db.flush()

    return {
        "project_id": str(project_id),
        "salvaged_materials": ps.salvaged_materials,
        "total_salvaged_cost": sum(m.get("cost", 0) for m in ps.salvaged_materials),
        "item_count": len(ps.salvaged_materials),
    }


# ---------------------------------------------------------------------------
# Recycled Content tracking
# ---------------------------------------------------------------------------


@router.put("/{project_id}/sustainability/recycled-content")
async def update_recycled_content(
    project_id: uuid.UUID,
    request: RecycledContentUpdate,
    current_user: User = Depends(require_permission("estimates", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update the recycled content percentage for a project."""
    await verify_project_access(project_id, current_user, db)

    stmt = select(ProjectSustainability).where(ProjectSustainability.project_id == project_id)
    result = await db.execute(stmt)
    ps = result.scalars().first()

    if ps is None:
        ps = ProjectSustainability(project_id=project_id)
        db.add(ps)

    from decimal import Decimal

    ps.recycled_content_pct = Decimal(str(request.recycled_content_pct))
    await db.flush()

    return {
        "project_id": str(project_id),
        "recycled_content_pct": float(ps.recycled_content_pct),
    }


# ---------------------------------------------------------------------------
# Recalculate sustainability
# ---------------------------------------------------------------------------


@router.post("/{project_id}/sustainability/recalculate")
async def recalculate_sustainability(
    project_id: uuid.UUID,
    gross_area_sf: float | None = Query(default=None),
    current_user: User = Depends(require_permission("estimates", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Force recalculation of project sustainability metrics."""
    await verify_project_access(project_id, current_user, db)

    from app.services.estimating.carbon_database import calculate_project_sustainability

    stmt = select(ProjectSustainability).where(ProjectSustainability.project_id == project_id)
    result = await db.execute(stmt)
    existing = result.scalars().first()

    salvaged: list[dict] = list(existing.salvaged_materials) if existing else []
    recycled_pct = float(existing.recycled_content_pct) if existing else 0.0

    dashboard = await calculate_project_sustainability(
        db=db,
        project_id=str(project_id),
        gross_area_sf=gross_area_sf,
        salvaged_materials=salvaged,
        recycled_content_pct=recycled_pct,
    )

    await db.commit()

    return {
        "project_id": str(project_id),
        "total_embodied_carbon_kgco2e": dashboard.total_embodied_carbon_kgco2e,
        "carbon_per_sf": dashboard.carbon_per_sf,
        "baseline_comparison_pct": dashboard.baseline_comparison_pct,
        "total_leed_points": dashboard.total_leed_points,
        "calculated_at": dashboard.calculated_at,
        "status": "recalculated",
    }
