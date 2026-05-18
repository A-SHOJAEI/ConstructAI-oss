"""Drone/UAV data integration API endpoints.

Routes for managing drone flight logs, uploading captures, calculating
earthwork volumes, and comparing survey data across captures.
All routes are project-scoped: ``/projects/{project_id}/drones/...``
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.drone import (
    CaptureListResponse,
    CaptureResponse,
    EarthworkCalculateRequest,
    EarthworkCompareRequest,
    EarthworkVolumeListResponse,
    EarthworkVolumeResponse,
    FlightLogCreateRequest,
    FlightLogListResponse,
    FlightLogResponse,
    FlightSummaryResponse,
    VolumeComparisonResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Flight log management
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/drones/flights",
    response_model=FlightLogResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_flight_log(
    project_id: uuid.UUID,
    request: FlightLogCreateRequest,
    current_user: User = Depends(require_permission("drones", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a drone flight log record."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.drone_service import (
        create_flight_log as svc_create_flight,
    )

    flight = await svc_create_flight(
        db,
        project_id=project_id,
        flight_date=request.flight_date,
        drone_id=request.drone_id,
        duration_minutes=request.duration_minutes,
        area_covered_sf=request.area_covered_sf,
        altitude_ft=request.altitude_ft,
        flight_path=request.flight_path,
        weather_conditions=request.weather_conditions,
        operator_id=current_user.id,
        notes=request.notes,
    )

    return flight


@router.get(
    "/{project_id}/drones/flights",
    response_model=FlightLogListResponse,
)
async def list_flights(
    project_id: uuid.UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("drones", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List drone flight logs for a project."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.drone_service import list_flights as svc_list_flights

    flights, total = await svc_list_flights(db, project_id, skip, limit)

    return FlightLogListResponse(data=flights, total=total, skip=skip, limit=limit)


# ---------------------------------------------------------------------------
# Capture management
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/drones/flights/{flight_id}/captures",
    response_model=CaptureResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_capture(
    project_id: uuid.UUID,
    flight_id: uuid.UUID,
    file: UploadFile = File(...),
    capture_type: str = Form(
        ..., description="One of: orthomosaic, point_cloud, video, thermal, photo"
    ),
    resolution: str | None = Form(default=None, description="e.g. '2cm/px'"),
    current_user: User = Depends(require_permission("drones", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a drone capture (orthomosaic, point cloud, video, thermal, photo)."""
    await verify_project_access(project_id, current_user, db)

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file",
        )

    filename = file.filename or f"capture.{capture_type}"

    from app.services.intelligence.drone_service import (
        upload_capture as svc_upload_capture,
    )

    try:
        capture = await svc_upload_capture(
            db,
            flight_id,
            file_bytes,
            capture_type,
            filename,
            resolution,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return capture


@router.get(
    "/{project_id}/drones/flights/{flight_id}/captures",
    response_model=CaptureListResponse,
)
async def list_captures(
    project_id: uuid.UUID,
    flight_id: uuid.UUID,
    current_user: User = Depends(require_permission("drones", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List all captures for a drone flight."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.drone_service import (
        list_captures as svc_list_captures,
    )

    captures = await svc_list_captures(db, flight_id)
    return CaptureListResponse(data=captures, count=len(captures))


# ---------------------------------------------------------------------------
# Earthwork volume calculation
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/drones/earthwork/calculate",
    response_model=EarthworkVolumeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def calculate_earthwork_volume(
    project_id: uuid.UUID,
    request: EarthworkCalculateRequest,
    current_user: User = Depends(require_permission("drones", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Calculate cut/fill earthwork volume from point data.

    Accepts an Nx3 array of [x, y, z] coordinates in feet and computes
    volume above/below the reference elevation using the specified method.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.drone_service import (
        calculate_earthwork_volume as svc_calc_volume,
    )

    try:
        volume = await svc_calc_volume(
            db,
            project_id=project_id,
            zone_name=request.zone_name,
            points=request.points,
            grid_spacing_ft=request.grid_spacing_ft,
            reference_elevation_ft=request.reference_elevation_ft,
            method=request.method,
            capture_id=request.capture_id,
            created_by=current_user.id,
            notes=request.notes,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return volume


@router.post(
    "/{project_id}/drones/earthwork/compare",
    response_model=VolumeComparisonResponse,
)
async def compare_captures(
    project_id: uuid.UUID,
    request: EarthworkCompareRequest,
    current_user: User = Depends(require_permission("drones", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Compare earthwork volumes between two captures for a zone."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.drone_service import (
        compare_captures as svc_compare,
    )

    try:
        comparison = await svc_compare(
            db,
            request.capture_id_before,
            request.capture_id_after,
            request.zone_name,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    return VolumeComparisonResponse(
        zone_name=comparison.zone_name,
        before_date=comparison.before_date,
        after_date=comparison.after_date,
        before_cut_cy=comparison.before_cut_cy,
        before_fill_cy=comparison.before_fill_cy,
        after_cut_cy=comparison.after_cut_cy,
        after_fill_cy=comparison.after_fill_cy,
        delta_cut_cy=comparison.delta_cut_cy,
        delta_fill_cy=comparison.delta_fill_cy,
        delta_net_cy=comparison.delta_net_cy,
        progress_pct=comparison.progress_pct,
    )


@router.get(
    "/{project_id}/drones/earthwork",
    response_model=EarthworkVolumeListResponse,
)
async def list_earthwork_volumes(
    project_id: uuid.UUID,
    zone_name: str | None = Query(default=None),
    current_user: User = Depends(require_permission("drones", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List earthwork volume records for a project, optionally filtered by zone."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.drone_service import (
        list_earthwork_volumes as svc_list_volumes,
    )

    volumes = await svc_list_volumes(db, project_id, zone_name)
    return EarthworkVolumeListResponse(data=volumes, count=len(volumes))


# ---------------------------------------------------------------------------
# Flight summary
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/drones/summary",
    response_model=FlightSummaryResponse,
)
async def get_flight_summary(
    project_id: uuid.UUID,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    current_user: User = Depends(require_permission("drones", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregate summary of drone flights and captures for a project."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.drone_service import (
        get_flight_summary as svc_summary,
    )

    summary = await svc_summary(db, project_id, date_from, date_to)

    return FlightSummaryResponse(
        project_id=summary.project_id,
        total_flights=summary.total_flights,
        total_area_covered_sf=summary.total_area_covered_sf,
        total_flight_minutes=summary.total_flight_minutes,
        captures_by_type=summary.captures_by_type,
        date_range=summary.date_range,
    )
