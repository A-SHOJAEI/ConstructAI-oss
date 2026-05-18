"""Productivity tracking API endpoints."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import cast

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.productivity import (
    CrewProductivity,
    DailyLog,
    EquipmentTelemetry,
)
from app.models.user import User
from app.schemas.pagination import PaginationMeta
from app.schemas.productivity import (
    CrewProductivityCreate,
    CrewProductivityListResponse,
    CrewProductivityResponse,
    DailyLogCreate,
    DailyLogListResponse,
    DailyLogResponse,
    EquipmentTelemetryCreate,
    EquipmentTelemetryListResponse,
    EquipmentTelemetryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/daily-logs",
    response_model=DailyLogResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_daily_log(
    request: DailyLogCreate,
    current_user: User = Depends(require_permission("productivity", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a daily field log entry."""
    await verify_project_access(request.project_id, current_user, db)

    log = DailyLog(
        project_id=request.project_id,
        log_date=request.log_date,
        weather=request.weather,
        crew_count=request.crew_count,
        work_hours=request.work_hours,
        activities_completed=request.activities_completed,
        delays=request.delays,
        notes=request.notes,
        created_by=current_user.id,
    )
    db.add(log)
    await db.flush()
    await db.refresh(log)
    return log


@router.get(
    "/daily-logs",
    response_model=DailyLogListResponse,
)
async def list_daily_logs(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("productivity", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List daily logs for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(DailyLog).where(DailyLog.project_id == project_id).order_by(DailyLog.log_date.desc())
    )
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(
            DailyLog,
            cursor_uuid,
        )
        if cursor_obj:
            query = query.where(DailyLog.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return DailyLogListResponse(
        data=cast(list[DailyLogResponse], items),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )


@router.post(
    "/crew-productivity",
    response_model=CrewProductivityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_crew_productivity(
    request: CrewProductivityCreate,
    current_user: User = Depends(require_permission("productivity", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Record crew productivity data."""
    await verify_project_access(request.project_id, current_user, db)

    # Calculate productivity rate and PF ratio
    productivity_rate = None
    pf_ratio = None
    if request.crew_size > 0 and request.actual_units > 0:
        productivity_rate = request.actual_units / Decimal(str(request.crew_size))
    if request.planned_units > 0:
        pf_ratio = request.actual_units / request.planned_units

    entry = CrewProductivity(
        project_id=request.project_id,
        trade=request.trade,
        crew_size=request.crew_size,
        work_date=request.work_date,
        planned_units=request.planned_units,
        actual_units=request.actual_units,
        unit_of_measure=request.unit_of_measure,
        productivity_rate=productivity_rate,
        pf_ratio=pf_ratio,
        conditions=request.conditions,
    )
    db.add(entry)
    await db.flush()
    await db.refresh(entry)
    return entry


@router.get(
    "/crew-productivity",
    response_model=CrewProductivityListResponse,
)
async def list_crew_productivity(
    project_id: uuid.UUID = Query(...),
    trade: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("productivity", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List crew productivity records."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(CrewProductivity)
        .where(
            CrewProductivity.project_id == project_id,
        )
        .order_by(CrewProductivity.work_date.desc())
    )
    if trade:
        query = query.where(
            CrewProductivity.trade == trade,
        )

    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(
            CrewProductivity,
            cursor_uuid,
        )
        if cursor_obj:
            query = query.where(CrewProductivity.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return CrewProductivityListResponse(
        data=cast(list[CrewProductivityResponse], items),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )


@router.post(
    "/equipment-telemetry",
    response_model=EquipmentTelemetryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_equipment_telemetry(
    request: EquipmentTelemetryCreate,
    current_user: User = Depends(require_permission("productivity", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Ingest equipment telemetry data."""
    await verify_project_access(request.project_id, current_user, db)

    entry = EquipmentTelemetry(
        project_id=request.project_id,
        equipment_id=request.equipment_id,
        equipment_type=request.equipment_type,
        timestamp=request.timestamp,
        engine_hours=request.engine_hours,
        fuel_consumption=request.fuel_consumption,
        idle_time_hours=request.idle_time_hours,
        utilization_pct=request.utilization_pct,
        location_data=request.location_data,
        raw_payload=request.raw_payload,
    )
    db.add(entry)
    await db.flush()
    await db.refresh(entry)
    return entry


@router.get(
    "/equipment-telemetry",
    response_model=EquipmentTelemetryListResponse,
)
async def list_equipment_telemetry(
    project_id: uuid.UUID = Query(...),
    equipment_id: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("productivity", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List equipment telemetry records."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(EquipmentTelemetry)
        .where(
            EquipmentTelemetry.project_id == project_id,
        )
        .order_by(EquipmentTelemetry.timestamp.desc())
    )
    if equipment_id:
        query = query.where(EquipmentTelemetry.equipment_id == equipment_id)

    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(
            EquipmentTelemetry,
            cursor_uuid,
        )
        if cursor_obj:
            query = query.where(EquipmentTelemetry.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return EquipmentTelemetryListResponse(
        data=cast(list[EquipmentTelemetryResponse], items),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )
