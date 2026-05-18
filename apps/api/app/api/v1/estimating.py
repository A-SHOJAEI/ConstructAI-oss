"""Estimating API endpoints for cost estimates, Monte Carlo simulation, and cost items."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.estimating import CostEstimate, CostItem, EstimateLineItem
from app.models.user import User
from app.schemas.estimating import (
    CostEstimateCreate,
    CostEstimateResponse,
    EstimateListResponse,
    LocationInput,
    MonteCarloRequest,
    MonteCarloResponse,
    RegionalFactorResponse,
    RunEstimateResponse,
)
from app.schemas.pagination import PaginationMeta
from app.services.estimating.monte_carlo import run_monte_carlo

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/estimates/parametric-predict")
async def parametric_predict_endpoint(
    body: dict,
    current_user: User = Depends(require_permission("estimates", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Predict project cost from high-level parameters.

    Body fields: project_id (uuid), building_type (str), gross_area (number,
    sqft), num_stories (int), quality_level (1-5 int OR string), and
    location_factor (float, default 1.0).

    Returns the shape the estimating UI expects:
      predicted_cost_per_sf, total_predicted_cost, confidence_intervals,
      model_version, is_heuristic_fallback.
    """
    project_id = body.get("project_id")
    if project_id:
        try:
            await verify_project_access(uuid.UUID(str(project_id)), current_user, db)
        except (ValueError, TypeError):
            pass

    # Map the UI's 1-5 quality scale to model strings.
    quality_map = {
        "1": "economy",
        "2": "standard",
        "3": "standard",
        "4": "premium",
        "5": "luxury",
    }
    raw_quality = body.get("quality_level", 3)
    if isinstance(raw_quality, int | float):
        quality = quality_map.get(str(int(raw_quality)), "standard")
    else:
        quality = str(raw_quality).lower()

    project_params = {
        "sqft": body.get("gross_area", 0),
        "stories": int(body.get("num_stories", 1) or 1),
        "type": body.get("building_type", "office"),
        "quality_level": quality,
        "location_factor": body.get("location_factor"),
        "region": body.get("region", "national"),
    }

    from app.services.estimating.parametric_model import predict_cost

    try:
        result = await predict_cost(project_params)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Parametric prediction failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Parametric prediction failed: {exc}",
        )

    ci = result.get("confidence_interval") or {}
    total = float(result["total_predicted_cost"])
    intervals = [
        {
            "level": "80%",
            "lower": float(ci.get("low", total)),
            "upper": float(ci.get("high", total)),
        }
    ]

    return {
        "predicted_cost_per_sf": float(result["predicted_cost_per_sqft"]),
        "total_predicted_cost": total,
        "confidence_intervals": intervals,
        "model_version": result.get("model_used", "heuristic"),
        "is_heuristic_fallback": result.get("model_used") == "heuristic",
    }


@router.post("/estimates", response_model=CostEstimateResponse, status_code=status.HTTP_201_CREATED)
async def create_estimate(
    request: CostEstimateCreate,
    current_user: User = Depends(require_permission("estimates", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new cost estimate for a project."""
    await verify_project_access(request.project_id, current_user, db)

    assumptions = dict(request.assumptions)
    if request.location:
        assumptions["location"] = request.location.to_dict()

    estimate = CostEstimate(
        project_id=request.project_id,
        name=request.name,
        estimate_type=request.estimate_type,
        status=request.status,
        contingency_pct=request.contingency_pct,
        assumptions=assumptions,
        created_by=current_user.id,
    )
    db.add(estimate)
    await db.flush()
    await db.refresh(estimate)
    return estimate


@router.get("/estimates", response_model=EstimateListResponse)
async def list_estimates(
    project_id: uuid.UUID = Query(..., description="Project to list estimates for"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("estimates", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List cost estimates for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(CostEstimate)
        .where(CostEstimate.project_id == project_id)
        .order_by(CostEstimate.created_at.desc())
    )

    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_estimate = await db.get(CostEstimate, cursor_uuid)
        if cursor_estimate:
            query = query.where(CostEstimate.created_at < cursor_estimate.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    estimates = list(result.scalars().all())

    has_more = len(estimates) > limit
    if has_more:
        estimates = estimates[:limit]

    next_cursor = str(estimates[-1].id) if has_more and estimates else None

    return EstimateListResponse(
        data=cast(list[CostEstimateResponse], estimates),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


@router.get("/estimates/{estimate_id}", response_model=CostEstimateResponse)
async def get_estimate(
    estimate_id: uuid.UUID,
    current_user: User = Depends(require_permission("estimates", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a cost estimate by ID."""
    estimate = await db.get(CostEstimate, estimate_id)
    if estimate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cost estimate not found",
        )
    await verify_project_access(estimate.project_id, current_user, db)
    return estimate


@router.delete(
    "/{project_id}/estimates/{estimate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_estimate(
    project_id: uuid.UUID,
    estimate_id: uuid.UUID,
    current_user: User = Depends(require_permission("estimates", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a cost estimate and its line items."""
    await verify_project_access(project_id, current_user, db)
    estimate = await db.get(CostEstimate, estimate_id)
    if estimate is None or estimate.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cost estimate not found",
        )
    # Delete line items first
    li_stmt = select(EstimateLineItem).where(EstimateLineItem.estimate_id == estimate_id)
    li_result = await db.execute(li_stmt)
    for li in li_result.scalars().all():
        await db.delete(li)
    await db.delete(estimate)
    await db.flush()


@router.post(
    "/{project_id}/estimates/bulk-create",
    response_model=CostEstimateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bulk_create_estimate(
    project_id: uuid.UUID,
    request: CostEstimateCreate,
    line_items: list[dict] | None = None,
    current_user: User = Depends(require_permission("estimates", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a cost estimate with multiple line items in a single request.

    Accepts a CostEstimateCreate body. The ``assumptions`` field may include
    a ``line_items`` list of dicts, each with: csi_code, description, quantity,
    unit, unit_cost, total_cost, source.
    """
    if line_items is None:
        line_items = []
    await verify_project_access(project_id, current_user, db)

    assumptions = dict(request.assumptions)
    bulk_items = assumptions.pop("line_items", [])
    if request.location:
        assumptions["location"] = request.location.to_dict()

    estimate = CostEstimate(
        project_id=project_id,
        name=request.name,
        estimate_type=request.estimate_type,
        status=request.status,
        contingency_pct=request.contingency_pct,
        assumptions=assumptions,
        created_by=current_user.id,
    )
    db.add(estimate)
    await db.flush()

    total_cost = Decimal("0")
    for item_data in bulk_items:
        li_total = Decimal(str(item_data.get("total_cost", 0)))
        if li_total == 0 and item_data.get("quantity") and item_data.get("unit_cost"):
            li_total = Decimal(str(item_data["quantity"])) * Decimal(str(item_data["unit_cost"]))
        total_cost += li_total

        line_item = EstimateLineItem(
            estimate_id=estimate.id,
            csi_code=item_data.get("csi_code"),
            description=item_data.get("description", ""),
            quantity=Decimal(str(item_data.get("quantity", 0))),
            unit=item_data.get("unit", "EA"),
            unit_cost=Decimal(str(item_data.get("unit_cost", 0))),
            total_cost=li_total,
            source=item_data.get("source", "bulk"),
        )
        db.add(line_item)

    if bulk_items:
        estimate.total_cost = total_cost

    await db.flush()
    await db.refresh(estimate)
    return estimate


@router.post("/estimates/{estimate_id}/run", response_model=RunEstimateResponse)
async def run_estimate(
    estimate_id: uuid.UUID,
    current_user: User = Depends(require_permission("estimates", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Run the estimating agent to generate/update an estimate.

    Retrieves the estimate and its line items, runs the estimating agent to
    produce cost data, and optionally runs a Monte Carlo simulation.
    """
    estimate = await db.get(CostEstimate, estimate_id)
    if estimate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cost estimate not found",
        )
    await verify_project_access(estimate.project_id, current_user, db)

    # Import estimating agent lazily to avoid hard dependency at module level
    try:
        from app.services.agents.estimating_agent import run_estimating_agent

        agent_result = await run_estimating_agent(
            project_id=str(estimate.project_id),
            estimate_type=estimate.estimate_type,
        )

        # Create line items from agent results
        for item_data in agent_result.get("line_items", []):
            line_item = EstimateLineItem(
                estimate_id=estimate.id,
                csi_code=item_data.get("csi_code"),
                description=item_data.get("description", ""),
                quantity=Decimal(str(item_data.get("quantity", 0))),
                unit=item_data.get("unit", "EA"),
                unit_cost=Decimal(str(item_data.get("unit_cost", 0))),
                total_cost=Decimal(str(item_data.get("total_cost", 0))),
                source=item_data.get("source", "agent"),
                confidence=(
                    Decimal(str(item_data.get("confidence", 0)))
                    if item_data.get("confidence")
                    else None
                ),
            )
            db.add(line_item)

        # Update estimate totals
        estimate.total_cost = Decimal(str(agent_result.get("total_cost", 0)))
        estimate.status = "completed"

    except (ImportError, Exception) as e:
        estimate.status = "failed"
        estimate.assumptions = {
            **(estimate.assumptions or {}),
            "error": "Estimation failed. Please contact support.",
        }
        await db.flush()
        logger.exception("Estimate agent failed for estimate %s: %s", estimate_id, e)

    await db.flush()
    await db.refresh(estimate)

    return RunEstimateResponse(
        estimate=CostEstimateResponse.model_validate(estimate, from_attributes=True),
        monte_carlo=None,
    )


@router.post("/estimates/{estimate_id}/monte-carlo", response_model=MonteCarloResponse)
async def run_monte_carlo_endpoint(
    estimate_id: uuid.UUID,
    request: MonteCarloRequest,
    current_user: User = Depends(require_permission("estimates", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Run Monte Carlo simulation on an estimate.

    If a location is provided (in request or estimate assumptions),
    regional cost factors are applied to line item unit costs before
    simulation, so Monte Carlo uses region-adjusted base costs.
    """
    estimate = await db.get(CostEstimate, estimate_id)
    if estimate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cost estimate not found",
        )
    await verify_project_access(estimate.project_id, current_user, db)

    # Resolve location: explicit request > estimate assumptions
    location_dict = None
    regional_factor_resp = None
    if request.location:
        location_dict = request.location.to_dict()
    elif estimate.assumptions and estimate.assumptions.get("location"):
        location_dict = estimate.assumptions["location"]

    # Apply regional factor to unit costs if location available
    region_factor = 1.0
    if location_dict:
        from app.services.estimating.regional_factors import get_regional_factor

        rf = get_regional_factor(
            city=location_dict.get("city"),
            state=location_dict.get("state"),
            zip_code=location_dict.get("zip_code"),
            latitude=location_dict.get("latitude"),
            longitude=location_dict.get("longitude"),
        )
        region_factor = rf.composite_factor
        regional_factor_resp = RegionalFactorResponse(
            metro=rf.metro,
            state_abbr=rf.state_abbr,
            material_factor=rf.material_factor,
            labor_factor=rf.labor_factor,
            equipment_factor=rf.equipment_factor,
            composite_factor=rf.composite_factor,
            is_fallback=rf.is_fallback,
            distance_km=rf.distance_km,
            warning=rf.warning,
        )

    # Build line item dicts for the simulation
    line_items_query = select(EstimateLineItem).where(EstimateLineItem.estimate_id == estimate_id)
    result = await db.execute(line_items_query)
    line_items = result.scalars().all()

    if not line_items:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Estimate has no line items to simulate.",
        )

    line_item_dicts = [
        {
            "description": li.description,
            "quantity": float(li.quantity),
            "unit_cost": float(li.unit_cost) * region_factor,
            "csi_code": li.csi_code,
        }
        for li in line_items
    ]

    mc_result = await run_monte_carlo(
        line_items=line_item_dicts,
        num_simulations=request.num_simulations,
        contingency_pct=float(estimate.contingency_pct),
        org_id=str(current_user.org_id),
    )

    # Update estimate with Monte Carlo results
    estimate.monte_carlo_p50 = Decimal(str(mc_result["p50"]))
    estimate.monte_carlo_p80 = Decimal(str(mc_result["p80"]))
    estimate.confidence_low = Decimal(str(mc_result["p10"]))
    estimate.confidence_high = Decimal(str(mc_result["p90"]))
    await db.flush()

    return MonteCarloResponse(
        p10=mc_result["p10"],
        p50=mc_result["p50"],
        p80=mc_result["p80"],
        p90=mc_result["p90"],
        mean=mc_result["mean"],
        std_dev=mc_result["std_dev"],
        histogram_data=mc_result["histogram_data"],
        regional_factor=regional_factor_resp,
    )


@router.get("/cost-items")
async def list_cost_items(
    category: str | None = Query(default=None, description="Filter by category"),
    search: str | None = Query(default=None, description="Search description"),
    cursor: str | None = Query(default=None, description="Pagination cursor (UUID)"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_permission("estimates", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List available cost items with cursor-based pagination."""
    query = select(CostItem).order_by(CostItem.category, CostItem.description, CostItem.id)

    if category:
        query = query.where(CostItem.category == category)
    if search:
        escaped_search = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(CostItem.description.ilike(f"%{escaped_search}%"))
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(CostItem, cursor_uuid)
        if cursor_obj:
            query = query.where(
                (CostItem.category > cursor_obj.category)
                | (
                    (CostItem.category == cursor_obj.category)
                    & (CostItem.description > cursor_obj.description)
                )
                | (
                    (CostItem.category == cursor_obj.category)
                    & (CostItem.description == cursor_obj.description)
                    & (CostItem.id > cursor_obj.id)
                )
            )

    query = query.limit(limit + 1)
    result = await db.execute(query)
    cost_items = list(result.scalars().all())

    has_more = len(cost_items) > limit
    if has_more:
        cost_items = cost_items[:limit]

    return {
        "data": cost_items,
        "meta": {
            "has_more": has_more,
            "cursor": str(cost_items[-1].id) if has_more and cost_items else None,
        },
    }


@router.post("/regional-factors/lookup", response_model=RegionalFactorResponse)
async def lookup_regional_factor(
    location: LocationInput,
    current_user: User = Depends(require_permission("estimates", "read")),
):
    """Look up regional cost factors for a location.

    Accepts city/state, zip code, or lat/lon coordinates.
    Returns material, labor, equipment, and composite cost multipliers
    relative to the national average (1.0).
    """
    from app.services.estimating.regional_factors import get_regional_factor

    rf = get_regional_factor(
        city=location.city,
        state=location.state,
        zip_code=location.zip_code,
        latitude=location.latitude,
        longitude=location.longitude,
    )
    return RegionalFactorResponse(
        metro=rf.metro,
        state_abbr=rf.state_abbr,
        material_factor=rf.material_factor,
        labor_factor=rf.labor_factor,
        equipment_factor=rf.equipment_factor,
        composite_factor=rf.composite_factor,
        is_fallback=rf.is_fallback,
        distance_km=rf.distance_km,
        warning=rf.warning,
    )
