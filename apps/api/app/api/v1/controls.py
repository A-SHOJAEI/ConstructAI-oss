"""Project controls API endpoints."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.evm import (
    ChangeOrder,
    EVMSnapshot,
    ScheduleRiskSimulation,
)
from app.models.user import User
from app.schemas.controls import (
    ChangeOrderCreate,
    ChangeOrderListResponse,
    ChangeOrderResponse,
    EVMSnapshotCreate,
    EVMSnapshotListResponse,
    EVMSnapshotResponse,
    MonteCarloScheduleRequest,
    MonteCarloScheduleResponse,
    SCurveResponse,
)
from app.schemas.pagination import PaginationMeta
from app.services.controls.change_order_analyzer import (
    analyze_change_order,
)
from app.services.controls.evm_engine import calculate_evm_metrics

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/evm-snapshots",
    response_model=EVMSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_evm_snapshot(
    request: EVMSnapshotCreate,
    current_user: User = Depends(require_permission("reports", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new EVM snapshot with computed metrics."""
    await verify_project_access(request.project_id, current_user, db)

    metrics = calculate_evm_metrics(
        bac=request.bac,
        pv=request.pv,
        ev=request.ev,
        ac=request.ac,
    )

    if not metrics["is_valid"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Insufficient data for EVM calculation (PV and AC must be non-zero).",
        )

    snapshot = EVMSnapshot(
        project_id=request.project_id,
        snapshot_date=request.snapshot_date,
        bac=request.bac,
        pv=request.pv,
        ev=request.ev,
        ac=request.ac,
        sv=metrics["sv"],
        cv=metrics["cv"],
        spi=metrics["spi"],
        cpi=metrics["cpi"],
        eac=metrics["eac"],
        etc=metrics["etc"],
        vac=metrics["vac"],
        tcpi=metrics["tcpi"],
        percent_complete=metrics["percent_complete"],
        data_date=request.snapshot_date,
    )
    db.add(snapshot)
    await db.flush()
    await db.refresh(snapshot)
    return snapshot


@router.get(
    "/evm-snapshots",
    response_model=EVMSnapshotListResponse,
)
async def list_evm_snapshots(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List EVM snapshots for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(EVMSnapshot)
        .where(EVMSnapshot.project_id == project_id)
        .order_by(EVMSnapshot.snapshot_date.desc())
    )
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_snap = await db.get(
            EVMSnapshot,
            cursor_uuid,
        )
        if cursor_snap:
            query = query.where(EVMSnapshot.snapshot_date < cursor_snap.snapshot_date)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    snapshots = list(result.scalars().all())

    has_more = len(snapshots) > limit
    if has_more:
        snapshots = snapshots[:limit]

    next_cursor = str(snapshots[-1].id) if has_more and snapshots else None
    return EVMSnapshotListResponse(
        data=cast(list[EVMSnapshotResponse], snapshots),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )


@router.get(
    "/evm-snapshots/{snapshot_id}",
    response_model=EVMSnapshotResponse,
)
async def get_evm_snapshot(
    snapshot_id: uuid.UUID,
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get an EVM snapshot by ID."""
    snapshot = await db.get(EVMSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="EVM snapshot not found",
        )
    await verify_project_access(snapshot.project_id, current_user, db)
    return snapshot


@router.get(
    "/change-orders/{co_id}",
    response_model=ChangeOrderResponse,
)
async def get_change_order(
    co_id: uuid.UUID,
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a single change order by id."""
    co = await db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change order not found",
        )
    await verify_project_access(co.project_id, current_user, db)
    return co


@router.post(
    "/change-orders/{co_id}/scope-analysis",
)
async def change_order_scope_analysis(
    co_id: uuid.UUID,
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Run AI scope analysis: is this CO genuine additional work, or already
    covered by the contract / specs / answered RFIs?
    """
    co = await db.get(ChangeOrder, co_id)
    if co is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Change order not found",
        )
    await verify_project_access(co.project_id, current_user, db)

    from app.services.controls.scope_analysis import analyze_change_order_scope

    return await analyze_change_order_scope(
        db,
        project_id=co.project_id,
        title=co.title,
        description=co.description,
        change_type=co.change_type,
        spec_section=None,
        drawing_reference=None,
    )


@router.post(
    "/change-orders",
    response_model=ChangeOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_change_order(
    request: ChangeOrderCreate,
    current_user: User = Depends(require_permission("reports", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a change order with AI risk analysis."""
    await verify_project_access(request.project_id, current_user, db)

    ai_analysis = await analyze_change_order(
        title=request.title,
        description=request.description,
        change_type=request.change_type,
        cost_impact=request.cost_impact,
        schedule_impact_days=request.schedule_impact_days,
    )

    co = ChangeOrder(
        project_id=request.project_id,
        co_number=request.co_number,
        title=request.title,
        description=request.description,
        change_type=request.change_type,
        requested_by=current_user.id,
        cost_impact=request.cost_impact,
        schedule_impact_days=request.schedule_impact_days,
        risk_score=ai_analysis.get("risk_score"),
        ai_analysis=ai_analysis,
    )
    db.add(co)
    await db.flush()
    await db.refresh(co)
    return co


@router.get(
    "/change-orders",
    response_model=ChangeOrderListResponse,
)
async def list_change_orders(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List change orders for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(ChangeOrder)
        .where(ChangeOrder.project_id == project_id)
        .order_by(ChangeOrder.created_at.desc())
    )
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_co = await db.get(
            ChangeOrder,
            cursor_uuid,
        )
        if cursor_co:
            query = query.where(ChangeOrder.created_at < cursor_co.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    orders = list(result.scalars().all())

    has_more = len(orders) > limit
    if has_more:
        orders = orders[:limit]

    next_cursor = str(orders[-1].id) if has_more and orders else None
    return ChangeOrderListResponse(
        data=cast(list[ChangeOrderResponse], orders),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )


@router.post(
    "/schedule-risk",
    response_model=MonteCarloScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def run_schedule_risk(
    request: MonteCarloScheduleRequest,
    current_user: User = Depends(require_permission("reports", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Run Monte Carlo schedule risk simulation."""
    await verify_project_access(request.project_id, current_user, db)

    from app.models.scheduling import ScheduleActivity
    from app.services.controls.monte_carlo_schedule import (
        run_schedule_risk_simulation,
    )

    # Fetch real activities from the baseline
    act_query = select(ScheduleActivity).where(ScheduleActivity.baseline_id == request.baseline_id)
    act_result = await db.execute(act_query)
    activities = act_result.scalars().all()

    if not activities:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Baseline has no activities for schedule risk simulation.",
        )

    activity_dicts = [
        {
            "id": str(act.id),
            "name": act.name,
            "duration_days": act.duration_days,
            "predecessors": act.predecessors or [],
        }
        for act in activities
    ]

    sim_result = await run_schedule_risk_simulation(
        activities=activity_dicts,
        num_iterations=request.num_iterations,
    )

    sim = ScheduleRiskSimulation(
        project_id=request.project_id,
        baseline_id=request.baseline_id,
        num_iterations=request.num_iterations,
        p10_duration=sim_result["p10_duration"],
        p50_duration=sim_result["p50_duration"],
        p80_duration=sim_result["p80_duration"],
        p90_duration=sim_result["p90_duration"],
        mean_duration=sim_result["mean_duration"],
        std_dev=sim_result["std_dev"],
        critical_risk_drivers=sim_result["critical_risk_drivers"],
        histogram_data=sim_result["histogram_data"],
    )
    db.add(sim)
    await db.flush()
    await db.refresh(sim)
    return sim


@router.get(
    "/s-curve/{project_id}",
    response_model=SCurveResponse,
)
async def get_scurve(
    project_id: uuid.UUID,
    from_date: str | None = Query(default=None, description="Start date filter (ISO format)"),
    to_date: str | None = Query(default=None, description="End date filter (ISO format)"),
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get S-Curve data for a project."""
    await verify_project_access(project_id, current_user, db)

    # Parse optional date range filters
    parsed_from_date = None
    parsed_to_date = None
    if from_date is not None:
        try:
            parsed_from_date = datetime.fromisoformat(from_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid from_date format: must be ISO 8601",
            )
    if to_date is not None:
        try:
            parsed_to_date = datetime.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid to_date format: must be ISO 8601",
            )

    from app.services.controls.scurve_generator import (
        generate_scurve_data,
    )

    query = (
        select(EVMSnapshot)
        .where(EVMSnapshot.project_id == project_id)
        .order_by(EVMSnapshot.snapshot_date.asc())
    )
    if parsed_from_date is not None:
        query = query.where(EVMSnapshot.snapshot_date >= parsed_from_date)
    if parsed_to_date is not None:
        query = query.where(EVMSnapshot.snapshot_date <= parsed_to_date)
    result = await db.execute(query)
    snapshots = result.scalars().all()

    if not snapshots:
        from decimal import Decimal

        return SCurveResponse(
            project_id=project_id,
            data_points=[],
            bac=Decimal("0"),
            forecast_completion=None,
        )

    snap_dicts = [
        {
            "snapshot_date": s.snapshot_date.isoformat(),
            "pv": str(s.pv),
            "ev": str(s.ev),
            "ac": str(s.ac),
            "spi": str(s.spi),
        }
        for s in snapshots
    ]

    first_date = snapshots[0].snapshot_date
    bac = snapshots[-1].bac

    scurve = await generate_scurve_data(
        snapshots=snap_dicts,
        bac=bac,
        start_date=first_date,
    )

    return SCurveResponse(
        project_id=project_id,
        data_points=scurve.get("data_points", []),
        bac=bac,
        forecast_completion=scurve.get("forecast_completion"),
    )
