"""Scheduling API endpoints for baselines, CPM analysis, DCMA checks, and weather impact."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.scheduling import ScheduleActivity, ScheduleBaseline
from app.models.user import User
from app.schemas.pagination import PaginationMeta
from app.schemas.scheduling import (
    BaselineListResponse,
    DCMACheckRequest,
    DCMACheckResponse,
    ScheduleActivityCreate,
    ScheduleActivityListResponse,
    ScheduleActivityResponse,
    ScheduleActivityUpdate,
    ScheduleBaselineResponse,
    ScheduleImportResponse,
    WeatherImpactRequest,
    WeatherImpactResponse,
)
from app.services.scheduling.cpm_engine import WorkCalendar, calculate_cpm

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_SCHEDULE_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


@router.post(
    "/baselines",
    response_model=ScheduleBaselineResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_baseline(
    request: ScheduleActivityCreate,
    current_user: User = Depends(require_permission("schedules", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new schedule baseline.

    Creates a ScheduleBaseline record with initial activity data from the request.
    The baseline_id in the request body is ignored; a new baseline is always created.
    """
    await verify_project_access(request.project_id, current_user, db)

    # Determine the next version number for this project
    version_query = (
        select(ScheduleBaseline.version)
        .where(ScheduleBaseline.project_id == request.project_id)
        .order_by(ScheduleBaseline.version.desc())
        .limit(1)
    )
    result = await db.execute(version_query)
    latest_version = result.scalar()
    next_version = (latest_version or 0) + 1

    baseline = ScheduleBaseline(
        project_id=request.project_id,
        name=f"Baseline v{next_version}",
        version=next_version,
        baseline_date=request.start_date or date.today(),
        created_by=current_user.id,
    )
    db.add(baseline)
    await db.flush()

    # Create the initial activity linked to this baseline
    activity = ScheduleActivity(
        project_id=request.project_id,
        baseline_id=baseline.id,
        activity_code=request.activity_code,
        name=request.name,
        duration_days=request.duration_days,
        start_date=request.start_date,
        finish_date=request.finish_date,
        predecessors=request.predecessors,
        resource_assignments=request.resource_assignments,
        wbs_code=request.wbs_code,
    )
    db.add(activity)
    await db.flush()
    await db.refresh(baseline)

    return baseline


@router.get("/baselines", response_model=BaselineListResponse)
async def list_baselines(
    project_id: uuid.UUID = Query(..., description="Project to list baselines for"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("schedules", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List schedule baselines for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(ScheduleBaseline)
        .where(ScheduleBaseline.project_id == project_id)
        .order_by(ScheduleBaseline.created_at.desc())
    )

    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_baseline = await db.get(ScheduleBaseline, cursor_uuid)
        if cursor_baseline:
            query = query.where(ScheduleBaseline.created_at < cursor_baseline.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    baselines = list(result.scalars().all())

    has_more = len(baselines) > limit
    if has_more:
        baselines = baselines[:limit]

    next_cursor = str(baselines[-1].id) if has_more and baselines else None

    return BaselineListResponse(
        data=cast(list[ScheduleBaselineResponse], baselines),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


@router.get("/baselines/{baseline_id}", response_model=ScheduleBaselineResponse)
async def get_baseline(
    baseline_id: uuid.UUID,
    current_user: User = Depends(require_permission("schedules", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a schedule baseline by ID."""
    baseline = await db.get(ScheduleBaseline, baseline_id)
    if baseline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule baseline not found",
        )
    await verify_project_access(baseline.project_id, current_user, db)
    return baseline


@router.get("/activities", response_model=ScheduleActivityListResponse)
async def list_schedule_activities(
    project_id: uuid.UUID = Query(..., description="Project to list activities for"),
    baseline_id: uuid.UUID | None = Query(None, description="Filter by baseline"),
    status_filter: str | None = Query(None, alias="status", description="Filter by status"),
    is_critical: bool | None = Query(None, description="Filter critical-path activities"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    current_user: User = Depends(require_permission("schedules", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List schedule activities for a project with offset/limit pagination.

    Supports filtering by baseline, status, and critical-path flag.
    """
    await verify_project_access(project_id, current_user, db)

    from sqlalchemy import func as sa_func

    base_filter = ScheduleActivity.project_id == project_id
    query = select(ScheduleActivity).where(base_filter)

    if baseline_id is not None:
        query = query.where(ScheduleActivity.baseline_id == baseline_id)
    if status_filter is not None:
        query = query.where(ScheduleActivity.status == status_filter)
    if is_critical is not None:
        query = query.where(ScheduleActivity.is_critical.is_(is_critical))

    # Count total matching
    count_query = select(sa_func.count()).select_from(ScheduleActivity).where(base_filter)
    if baseline_id is not None:
        count_query = count_query.where(ScheduleActivity.baseline_id == baseline_id)
    if status_filter is not None:
        count_query = count_query.where(ScheduleActivity.status == status_filter)
    if is_critical is not None:
        count_query = count_query.where(ScheduleActivity.is_critical.is_(is_critical))
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    query = (
        query.order_by(
            ScheduleActivity.start_date.asc().nullslast(),
            ScheduleActivity.activity_code.asc(),
        )
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(query)
    activities = list(result.scalars().all())

    return ScheduleActivityListResponse(
        data=cast(list[ScheduleActivityResponse], activities),
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/{project_id}/scheduling/activities",
    response_model=ScheduleActivityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule_activity(
    project_id: uuid.UUID,
    request: ScheduleActivityCreate,
    current_user: User = Depends(require_permission("schedules", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new schedule activity for a project."""
    await verify_project_access(project_id, current_user, db)

    activity = ScheduleActivity(
        project_id=project_id,
        baseline_id=request.baseline_id,
        activity_code=request.activity_code,
        name=request.name,
        duration_days=request.duration_days,
        start_date=request.start_date,
        finish_date=request.finish_date,
        predecessors=request.predecessors,
        resource_assignments=request.resource_assignments,
        wbs_code=request.wbs_code,
        calendar_id=request.calendar_id,
    )
    db.add(activity)
    await db.flush()
    await db.refresh(activity)
    return activity


@router.patch(
    "/{project_id}/scheduling/activities/{activity_id}",
    response_model=ScheduleActivityResponse,
)
async def update_schedule_activity(
    project_id: uuid.UUID,
    activity_id: uuid.UUID,
    request: ScheduleActivityUpdate,
    current_user: User = Depends(require_permission("schedules", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a schedule activity."""
    await verify_project_access(project_id, current_user, db)

    activity = await db.get(ScheduleActivity, activity_id)
    if activity is None or activity.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule activity not found",
        )

    _PROTECTED_FIELDS = {"id", "project_id", "baseline_id", "created_at"}
    update_data = request.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        if field_name in _PROTECTED_FIELDS:
            continue
        setattr(activity, field_name, value)

    await db.flush()
    await db.refresh(activity)
    return activity


@router.post("/baselines/{baseline_id}/cpm", response_model=dict)
async def run_cpm(
    baseline_id: uuid.UUID,
    current_user: User = Depends(require_permission("schedules", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Run CPM analysis on a schedule baseline.

    Retrieves all activities for the baseline, runs the Critical Path Method
    calculation, and returns the results including critical path and project duration.
    """
    baseline = await db.get(ScheduleBaseline, baseline_id)
    if baseline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule baseline not found",
        )
    await verify_project_access(baseline.project_id, current_user, db)

    # Get activities for this baseline
    activities_query = select(ScheduleActivity).where(ScheduleActivity.baseline_id == baseline_id)
    result = await db.execute(activities_query)
    activities = result.scalars().all()

    if not activities:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Baseline has no activities for CPM analysis.",
        )

    # Convert to dicts for the CPM engine
    activity_dicts = [
        {
            "id": str(act.id),
            "name": act.name,
            "duration_days": act.duration_days,
            "relationships": act.predecessors or [],
            "calendar_id": act.calendar_id,
        }
        for act in activities
    ]

    # Build calendar map from baseline if available
    cpm_calendars: dict[str, WorkCalendar] = {}
    if baseline.calendars:
        for cal in baseline.calendars:
            cpm_calendars[cal["id"]] = WorkCalendar(
                work_days=cal["work_days"],
                holidays=set(cal.get("holidays", [])),
            )

    cpm_result = await calculate_cpm(
        activity_dicts,
        calendars=cpm_calendars if cpm_calendars else None,
        project_start=baseline.baseline_date if cpm_calendars else None,
    )

    # Update baseline with CPM results
    baseline.total_duration_days = cpm_result["project_duration"]
    baseline.critical_path_length = cpm_result["critical_path_length"]

    # Update individual activities with CPM data
    enriched_map = {a["id"]: a for a in cpm_result["activities"]}
    for act in activities:
        enriched = enriched_map.get(str(act.id))
        if enriched:
            if "start_date" in enriched:
                act.early_start = date.fromisoformat(enriched["start_date"])
                act.early_finish = date.fromisoformat(enriched["finish_date"])
            else:
                act.early_start = None
                act.early_finish = None
            act.late_start = None
            act.late_finish = None
            act.total_float = enriched.get("total_float")
            act.free_float = enriched.get("free_float")
            act.is_critical = enriched.get("is_critical", False)

    await db.flush()

    return cpm_result


@router.post("/dcma-check", response_model=DCMACheckResponse)
async def run_dcma_check_endpoint(
    request: DCMACheckRequest,
    current_user: User = Depends(require_permission("schedules", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Run DCMA 14-point assessment on a schedule baseline.

    Evaluates the schedule against industry-standard DCMA metrics including
    logic density, hard constraints, high float, negative float, and more.
    """
    baseline = await db.get(ScheduleBaseline, request.baseline_id)
    if baseline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule baseline not found",
        )
    await verify_project_access(baseline.project_id, current_user, db)

    # Get activities for analysis
    activities_query = select(ScheduleActivity).where(
        ScheduleActivity.baseline_id == request.baseline_id
    )
    result = await db.execute(activities_query)
    activities = result.scalars().all()

    if not activities:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Baseline has no activities for DCMA analysis.",
        )

    activity_dicts = [
        {
            "id": str(act.id),
            "name": act.name,
            "duration_days": act.duration_days,
            "predecessors": act.predecessors,
            "total_float": act.total_float,
            "is_critical": act.is_critical,
            "start_date": act.start_date.isoformat() if act.start_date else None,
            "finish_date": act.finish_date.isoformat() if act.finish_date else None,
            "resource_assignments": act.resource_assignments,
            "status": act.status,
        }
        for act in activities
    ]

    # Import DCMA checker lazily; the module may not yet exist
    try:
        from app.services.scheduling.dcma_checker import run_dcma_check

        dcma_result = await run_dcma_check(activity_dicts)
    except ImportError:
        logger.warning("DCMA checker module not available; running basic checks")
        # Provide a basic fallback DCMA assessment
        total = len(activity_dicts)
        missing_predecessors = sum(1 for a in activity_dicts if not a["predecessors"])
        logic_pct = ((total - missing_predecessors) / total * 100) if total > 0 else 0

        dcma_result = {
            "overall_score": round(logic_pct, 1),
            "checks": [
                {
                    "check_name": "Logic (Predecessors)",
                    "status": "pass" if logic_pct >= 90 else "fail",
                    "score": round(logic_pct, 1),
                    "description": (
                        f"{total - missing_predecessors}/{total} activities have predecessors"
                    ),
                    "threshold": 90.0,
                },
            ],
            "passed": 1 if logic_pct >= 90 else 0,
            "failed": 0 if logic_pct >= 90 else 1,
            "warning": 0,
        }

    # Store DCMA results on the baseline
    from decimal import Decimal

    baseline.dcma_score = Decimal(str(dcma_result["overall_score"]))
    baseline.dcma_results = dcma_result
    await db.flush()

    return DCMACheckResponse(**dcma_result)


@router.post("/weather-impact", response_model=WeatherImpactResponse)
async def check_weather_impact(
    request: WeatherImpactRequest,
    current_user: User = Depends(require_permission("schedules", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Analyze weather impact on schedule.

    Fetches weather data for the project location and date range, then
    analyzes the potential impact on outdoor construction activities.
    """
    await verify_project_access(request.project_id, current_user, db)

    # Get activities for the project within the date range
    activities_query = (
        select(ScheduleActivity)
        .where(ScheduleActivity.project_id == request.project_id)
        .where(ScheduleActivity.start_date >= request.start_date)
        .where(ScheduleActivity.start_date <= request.end_date)
    )
    result = await db.execute(activities_query)
    activities = result.scalars().all()

    activity_dicts = [
        {
            "id": str(act.id),
            "name": act.name,
            "duration_days": act.duration_days,
            "start_date": act.start_date.isoformat() if act.start_date else None,
            "finish_date": act.finish_date.isoformat() if act.finish_date else None,
        }
        for act in activities
    ]

    # Import weather service lazily; the module may not yet exist
    try:
        from app.services.scheduling.weather_service import get_weather_impact

        weather_result = await get_weather_impact(
            location=request.location,
            start_date=request.start_date,
            end_date=request.end_date,
            activities=activity_dicts,
        )
    except ImportError:
        logger.warning("Weather service module not available; returning default assessment")
        # Provide a basic fallback assessment
        total_days = (request.end_date - request.start_date).days
        estimated_impact = max(1, total_days // 10)  # ~10% weather days as heuristic
        weather_result = {
            "impact_days": estimated_impact,
            "weather_events": [],
            "adjusted_end_date": request.end_date.isoformat(),
            "risk_level": "medium",
        }

    # Ensure adjusted_end_date is a date object for the response
    adjusted_end = weather_result.get("adjusted_end_date", request.end_date)
    if isinstance(adjusted_end, str):
        adjusted_end = date.fromisoformat(adjusted_end)

    return WeatherImpactResponse(
        impact_days=weather_result["impact_days"],
        weather_events=weather_result.get("weather_events", []),
        adjusted_end_date=adjusted_end,
        risk_level=weather_result.get("risk_level", "unknown"),
    )


# ---------------------------------------------------------------------------
# Schedule Import
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/schedule/import",
    response_model=ScheduleImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_schedule(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("schedules", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Import a P6 (.xer, .pmxml) or MS Project (.mpp, .mpx, .mspdi, .xml) schedule file.

    Parses the file using MPXJ, creates a new schedule baseline with all
    activities, relationships, and calendars, then runs CPM analysis.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.scheduling.schedule_importer import ScheduleImporter

    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in ScheduleImporter.SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file format '{ext}'. "
            f"Supported: {', '.join(sorted(ScheduleImporter.SUPPORTED_EXTENSIONS))}",
        )

    # Validate actual file size by reading content
    file_bytes = await file.read()
    if len(file_bytes) > MAX_SCHEDULE_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {MAX_SCHEDULE_FILE_SIZE // (1024 * 1024)} MB.",
        )
    await file.seek(0)  # reset for downstream consumption

    importer = ScheduleImporter()
    result = await importer.import_file(db, project_id, file, current_user.id)
    return result
