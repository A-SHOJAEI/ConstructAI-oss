"""Logistics API endpoints for site layout optimization, delivery routing, and simulation."""

from __future__ import annotations

import logging
import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.logistics import DeliveryRoute, SiteLayout
from app.models.user import User
from app.schemas.logistics import (
    DeliveryRouteResponse,
    OptimizeSiteRequest,
    OptimizeSiteResponse,
    RouteOptimizeRequest,
    RouteOptimizeResponse,
    SimulationRequest,
    SimulationResponse,
    SiteLayoutListResponse,
    SiteLayoutResponse,
)
from app.schemas.pagination import PaginationMeta

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/site-layouts/optimize", response_model=OptimizeSiteResponse)
async def optimize_site(
    request: OptimizeSiteRequest,
    current_user: User = Depends(require_permission("schedules", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Run NSGA-II site layout optimization.

    Accepts facility definitions, site boundary, and constraints, then runs a
    multi-objective optimization to produce a Pareto front of layout solutions.
    """
    await verify_project_access(request.project_id, current_user, db)

    try:
        from app.services.logistics.site_layout import optimize_site_layout

        optimization_result = await optimize_site_layout(
            facilities=request.facilities,
            site_boundary=request.site_boundary,
            constraints=request.constraints,
        )
    except ImportError:
        logger.warning("Site layout optimization module not available; returning placeholder")
        optimization_result = {
            "layouts": [],
            "pareto_front": [],
            "generations": 0,
        }

    # Persist optimized layouts to the database
    saved_layouts: list[SiteLayout] = []
    for i, layout_data in enumerate(optimization_result.get("layouts", [])):
        layout = SiteLayout(
            project_id=request.project_id,
            name=f"Optimized Layout {i + 1}",
            layout_data=layout_data.get("layout_data", layout_data),
            optimization_score=layout_data.get("optimization_score"),
            safety_score=layout_data.get("safety_score"),
            efficiency_score=layout_data.get("efficiency_score"),
            constraints=request.constraints,
            pareto_rank=layout_data.get("pareto_rank"),
            generation=layout_data.get("generation"),
            status="optimized",
            created_by=current_user.id,
        )
        db.add(layout)
        saved_layouts.append(layout)

    await db.flush()
    for layout in saved_layouts:
        await db.refresh(layout)

    return OptimizeSiteResponse(
        layouts=cast(list[SiteLayoutResponse], saved_layouts),
        pareto_front=optimization_result.get("pareto_front", []),
        generations=optimization_result.get("generations", 0),
    )


@router.get("/site-layouts", response_model=SiteLayoutListResponse)
async def list_site_layouts(
    project_id: uuid.UUID = Query(..., description="Project to list site layouts for"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("schedules", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List site layouts for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(SiteLayout)
        .where(SiteLayout.project_id == project_id)
        .order_by(SiteLayout.created_at.desc())
    )

    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_layout = await db.get(SiteLayout, cursor_uuid)
        if cursor_layout:
            query = query.where(SiteLayout.created_at < cursor_layout.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    layouts = list(result.scalars().all())

    has_more = len(layouts) > limit
    if has_more:
        layouts = layouts[:limit]

    next_cursor = str(layouts[-1].id) if has_more and layouts else None

    return SiteLayoutListResponse(
        data=cast(list[SiteLayoutResponse], layouts),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


@router.get("/site-layouts/{layout_id}", response_model=SiteLayoutResponse)
async def get_site_layout(
    layout_id: uuid.UUID,
    current_user: User = Depends(require_permission("schedules", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a site layout by ID."""
    layout = await db.get(SiteLayout, layout_id)
    if layout is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Site layout not found",
        )
    await verify_project_access(layout.project_id, current_user, db)
    return layout


@router.post("/delivery-routes/optimize", response_model=RouteOptimizeResponse)
async def optimize_routes(
    request: RouteOptimizeRequest,
    current_user: User = Depends(require_permission("schedules", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Optimize delivery routes using VRPTW (Vehicle Routing Problem with Time Windows).

    Accepts delivery locations, vehicle fleet, and constraints, then runs
    route optimization to minimize cost and distance.
    """
    await verify_project_access(request.project_id, current_user, db)

    try:
        from app.services.logistics.delivery_router import optimize_delivery_routes

        route_result = await optimize_delivery_routes(
            deliveries=request.deliveries,
            vehicles=request.vehicles,
            depot=request.depot,
            date=request.date.isoformat()
            if hasattr(request.date, "isoformat")
            else str(request.date),
        )
    except ImportError:
        logger.warning("Delivery router module not available; returning placeholder")
        route_result = {
            "routes": [],
            "total_cost": 0.0,
            "total_distance": 0.0,
            "unassigned": [],
        }

    # Persist routes to the database
    saved_routes: list[DeliveryRoute] = []
    for route_data in route_result.get("routes", []):
        route = DeliveryRoute(
            project_id=request.project_id,
            route_date=request.date,
            vehicle_id=route_data.get("vehicle_id"),
            stops=route_data.get("stops", {}),
            total_distance_km=route_data.get("total_distance_km"),
            total_duration_minutes=route_data.get("total_duration_minutes"),
            total_cost=route_data.get("total_cost"),
            optimization_status="optimized",
            constraints=route_data.get("constraints", {}),
        )
        db.add(route)
        saved_routes.append(route)

    await db.flush()
    for route in saved_routes:
        await db.refresh(route)

    return RouteOptimizeResponse(
        routes=cast(list[DeliveryRouteResponse], saved_routes),
        total_cost=route_result.get("total_cost", 0.0),
        total_distance=route_result.get("total_distance", 0.0),
        unassigned=route_result.get("unassigned", []),
    )


@router.post("/simulate", response_model=SimulationResponse)
async def run_simulation(
    request: SimulationRequest,
    current_user: User = Depends(require_permission("schedules", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Run SimPy construction site simulation.

    Accepts a simulation scenario and duration, then runs a discrete-event
    simulation to identify bottlenecks and utilization patterns.
    """
    await verify_project_access(request.project_id, current_user, db)

    try:
        from app.services.logistics.simulation import run_site_simulation

        sim_result = await run_site_simulation(
            scenario=request.scenario,
            duration_days=request.duration_days,
        )
    except ImportError:
        logger.warning("Simulation module not available; returning placeholder")
        sim_result = {
            "timeline": [],
            "bottlenecks": [],
            "utilization": {},
            "recommendations": [
                "Simulation engine not yet available. Install SimPy to enable.",
            ],
        }

    return SimulationResponse(
        timeline=sim_result.get("timeline", []),
        bottlenecks=sim_result.get("bottlenecks", []),
        utilization=sim_result.get("utilization", {}),
        recommendations=sim_result.get("recommendations", []),
    )
