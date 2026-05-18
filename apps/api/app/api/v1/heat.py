"""HeatShield API — heat illness prevention and OSHA compliance endpoints."""

from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.heat_compliance import HeatMonitoringConfig, HeatPlan
from app.models.user import User
from app.schemas.heat_compliance import (
    BreakLogCreate,
    BreakLogResponse,
    BreakScheduleItem,
    HeatConfigResponse,
    HeatConfigUpdate,
    HeatDashboardResponse,
    HeatPlanResponse,
    HeatReadingResponse,
    IncidentCreate,
    IncidentResponse,
    ManualReadingCreate,
    WorkerAcclimatizationResponse,
    WorkerCreate,
    WorkerUpdate,
)
from app.services.products.heatshield.service import (
    add_worker,
    configure_monitoring,
    create_incident,
    generate_break_schedule,
    generate_hiipp,
    get_current_conditions,
    get_dashboard,
    list_breaks,
    list_workers,
    log_break,
    record_manual_reading,
    update_worker,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@router.patch(
    "/{project_id}/heat/config",
    response_model=HeatConfigResponse,
)
async def update_heat_config(
    project_id: uuid.UUID,
    body: HeatConfigUpdate,
    current_user: User = Depends(require_permission("safety", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Create or update heat monitoring configuration for a project."""
    await verify_project_access(project_id, current_user, db)
    config = await configure_monitoring(
        db,
        project_id,
        current_user.org_id,
        body.model_dump(exclude_unset=True),
    )
    return config


# ---------------------------------------------------------------------------
# Conditions & Readings
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/heat/conditions",
    response_model=dict | None,
)
async def get_conditions(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the latest heat conditions for a project."""
    await verify_project_access(project_id, current_user, db)
    return await get_current_conditions(db, project_id)


@router.post(
    "/{project_id}/heat/readings",
    response_model=HeatReadingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_manual_reading(
    project_id: uuid.UUID,
    body: ManualReadingCreate,
    current_user: User = Depends(require_permission("safety", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Record a manual heat/weather reading from field personnel."""
    await verify_project_access(project_id, current_user, db)
    reading = await record_manual_reading(db, project_id, current_user.org_id, body.model_dump())
    return reading


# ---------------------------------------------------------------------------
# Worker Acclimatization
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/heat/workers",
    response_model=WorkerAcclimatizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_worker(
    project_id: uuid.UUID,
    body: WorkerCreate,
    current_user: User = Depends(require_permission("safety", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Register a worker for acclimatization tracking."""
    await verify_project_access(project_id, current_user, db)
    worker = await add_worker(db, project_id, current_user.org_id, body.model_dump())
    return worker


@router.patch(
    "/{project_id}/heat/workers/{worker_id}",
    response_model=WorkerAcclimatizationResponse,
)
async def patch_worker(
    project_id: uuid.UUID,
    worker_id: str,
    body: WorkerUpdate,
    current_user: User = Depends(require_permission("safety", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a worker's acclimatization record."""
    await verify_project_access(project_id, current_user, db)
    worker = await update_worker(db, project_id, worker_id, body.model_dump(exclude_unset=True))
    if worker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Worker '{worker_id}' not found in project",
        )
    return worker


@router.get(
    "/{project_id}/heat/workers",
    response_model=list[WorkerAcclimatizationResponse],
)
async def get_workers(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List all workers with their acclimatization status."""
    await verify_project_access(project_id, current_user, db)
    return await list_workers(db, project_id)


# ---------------------------------------------------------------------------
# Rest Breaks
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/heat/breaks",
    response_model=BreakLogResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_break_log(
    project_id: uuid.UUID,
    body: BreakLogCreate,
    current_user: User = Depends(require_permission("safety", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Log a rest/water break."""
    await verify_project_access(project_id, current_user, db)
    break_log = await log_break(
        db, project_id, current_user.org_id, body.model_dump(), current_user.id
    )
    return break_log


@router.get(
    "/{project_id}/heat/breaks",
    response_model=list[BreakLogResponse],
)
async def get_breaks(
    project_id: uuid.UUID,
    break_date: date | None = Query(default=None),
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List logged breaks, optionally filtered by date."""
    await verify_project_access(project_id, current_user, db)
    return await list_breaks(db, project_id, break_date)


@router.get(
    "/{project_id}/heat/break-schedule",
    response_model=list[BreakScheduleItem],
)
async def get_break_schedule(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the recommended break schedule based on current conditions."""
    await verify_project_access(project_id, current_user, db)

    # Fetch config for crew start time
    result = await db.execute(
        select(HeatMonitoringConfig).where(HeatMonitoringConfig.project_id == project_id)
    )
    config = result.scalar_one_or_none()
    crew_start = config.crew_start_time if config else "07:00"

    # Determine current threshold level
    conditions = await get_current_conditions(db, project_id)
    level = conditions["threshold_level"] if conditions else "normal"

    return generate_break_schedule(crew_start, level)


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/heat/incidents",
    response_model=IncidentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def report_incident(
    project_id: uuid.UUID,
    body: IncidentCreate,
    current_user: User = Depends(require_permission("safety", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Report a heat-related incident."""
    await verify_project_access(project_id, current_user, db)
    incident = await create_incident(db, project_id, current_user.org_id, body.model_dump())
    return incident


# ---------------------------------------------------------------------------
# HIIPP
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/heat/hiipp",
    response_model=HeatPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_hiipp(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("safety", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a Heat Illness Injury Prevention Plan."""
    await verify_project_access(project_id, current_user, db)
    plan = await generate_hiipp(db, project_id, current_user.org_id)
    return plan


@router.get(
    "/{project_id}/heat/plans",
    response_model=list[HeatPlanResponse],
)
async def get_plans(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List all generated heat plans for a project."""
    await verify_project_access(project_id, current_user, db)
    result = await db.execute(
        select(HeatPlan)
        .where(HeatPlan.project_id == project_id)
        .order_by(HeatPlan.generated_at.desc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/heat/dashboard",
    response_model=HeatDashboardResponse,
)
async def heat_dashboard(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("safety", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the aggregated heat compliance dashboard for a project."""
    await verify_project_access(project_id, current_user, db)
    data = await get_dashboard(db, project_id)
    return HeatDashboardResponse(**data)
