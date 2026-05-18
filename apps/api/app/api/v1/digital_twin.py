"""Digital twin API endpoints.

Routes for creating digital twins from IFC/point cloud files, managing IoT
sensor links, ingesting sensor data, and capturing point-in-time snapshots.
All routes are project-scoped: ``/projects/{project_id}/twins/...``
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.digital_twin import (
    DigitalTwinListResponse,
    DigitalTwinResponse,
    SensorAnomalyResponse,
    SensorBatchRequest,
    SensorRegisterRequest,
    SensorResponse,
    SnapshotCreateRequest,
    TwinSnapshotListResponse,
    TwinSnapshotResponse,
    TwinStateResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Create twin from IFC file
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/twins",
    response_model=DigitalTwinResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_twin_from_ifc(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("twins", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a digital twin from an uploaded IFC file.

    Parses the IFC to extract elements, metadata, and coordinate info.
    Uploads the file to S3 and creates a DigitalTwinModel record.
    """
    await verify_project_access(project_id, current_user, db)

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file",
        )

    filename = file.filename or "model.ifc"

    from app.services.intelligence.digital_twin_service import create_twin_from_ifc

    try:
        twin = await create_twin_from_ifc(
            db, project_id, file_bytes, filename, created_by=current_user.id
        )
    except Exception as exc:
        logger.error("Failed to create twin from IFC: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse IFC file: {exc}",
        )

    return twin


# ---------------------------------------------------------------------------
# Create twin from point cloud
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/twins/point-cloud",
    response_model=DigitalTwinResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_twin_from_point_cloud(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    file_format: str = Form(..., description="Point cloud format: las, laz, ply, e57"),
    current_user: User = Depends(require_permission("twins", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a digital twin from a point cloud file (LAS/LAZ/PLY/E57)."""
    await verify_project_access(project_id, current_user, db)

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file",
        )

    filename = file.filename or f"pointcloud.{file_format}"

    from app.services.intelligence.digital_twin_service import (
        create_twin_from_point_cloud,
    )

    try:
        twin = await create_twin_from_point_cloud(
            db,
            project_id,
            file_bytes,
            filename,
            file_format,
            created_by=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return twin


# ---------------------------------------------------------------------------
# List twins
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/twins",
    response_model=DigitalTwinListResponse,
)
async def list_twins(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("twins", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List all digital twin models for a project."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.digital_twin_service import (
        list_twins as svc_list_twins,
    )

    twins = await svc_list_twins(db, project_id)
    return DigitalTwinListResponse(data=twins, count=len(twins))


# ---------------------------------------------------------------------------
# Get twin current state
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/twins/{twin_id}",
    response_model=TwinStateResponse,
)
async def get_twin_state(
    project_id: uuid.UUID,
    twin_id: uuid.UUID,
    current_user: User = Depends(require_permission("twins", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the current state of a digital twin (sensors + schedule + anomalies)."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.digital_twin_service import (
        get_twin_state as svc_get_twin_state,
    )

    try:
        state = await svc_get_twin_state(db, twin_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return TwinStateResponse(
        twin_id=state.twin_id,
        project_id=state.project_id,
        name=state.name,
        source_type=state.source_type,
        status=state.status,
        element_count=state.element_count,
        bounds=state.bounds,
        coordinate_system=state.coordinate_system,
        metadata=state.metadata,
        sensors=state.sensors,
        anomalies=[
            SensorAnomalyResponse(
                sensor_id=a.sensor_id,
                sensor_type=a.sensor_type,
                value=a.value,
                unit=a.unit,
                level=a.level,
                threshold=a.threshold,
                message=a.message,
                element_id=a.element_id,
            )
            for a in state.anomalies
        ],
        latest_snapshot=state.latest_snapshot,
    )


# ---------------------------------------------------------------------------
# Register sensor
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/twins/{twin_id}/sensors",
    response_model=SensorResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_sensor(
    project_id: uuid.UUID,
    twin_id: uuid.UUID,
    request: SensorRegisterRequest,
    current_user: User = Depends(require_permission("twins", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Register an IoT sensor at a position within the digital twin."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.digital_twin_service import (
        register_sensor as svc_register,
    )

    try:
        sensor = await svc_register(
            db,
            twin_id,
            request.sensor_id,
            request.sensor_type,
            request.location_xyz,
            request.element_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return sensor


# ---------------------------------------------------------------------------
# Ingest sensor readings
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/twins/{twin_id}/sensors/readings",
    status_code=status.HTTP_200_OK,
)
async def ingest_sensor_readings(
    project_id: uuid.UUID,
    twin_id: uuid.UUID,
    request: SensorBatchRequest,
    current_user: User = Depends(require_permission("twins", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Ingest one or more sensor readings for a digital twin."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.digital_twin_service import ingest_sensor_batch

    readings = [
        {
            "sensor_id": r.sensor_id,
            "value": r.value,
            "unit": r.unit,
            "timestamp": r.timestamp,
        }
        for r in request.readings
    ]

    try:
        count = await ingest_sensor_batch(db, twin_id, readings)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return {"updated": count, "submitted": len(request.readings)}


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/twins/{twin_id}/snapshots",
    response_model=TwinSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_snapshot(
    project_id: uuid.UUID,
    twin_id: uuid.UUID,
    request: SnapshotCreateRequest,
    current_user: User = Depends(require_permission("twins", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a point-in-time snapshot of the digital twin state."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.digital_twin_service import (
        create_snapshot as svc_create_snapshot,
    )

    try:
        snapshot = await svc_create_snapshot(
            db,
            twin_id,
            request.schedule_overlay,
            request.photo_urls,
            request.notes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return snapshot


@router.get(
    "/{project_id}/twins/{twin_id}/snapshots",
    response_model=TwinSnapshotListResponse,
)
async def list_snapshots(
    project_id: uuid.UUID,
    twin_id: uuid.UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("twins", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List snapshots for a digital twin, most recent first."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.digital_twin_service import (
        list_snapshots as svc_list_snapshots,
    )

    snapshots = await svc_list_snapshots(db, twin_id, skip, limit)
    return TwinSnapshotListResponse(data=snapshots, count=len(snapshots))


# ---------------------------------------------------------------------------
# Element sensors
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/twins/{twin_id}/elements/{element_id}/sensors",
    response_model=list[SensorResponse],
)
async def get_element_sensors(
    project_id: uuid.UUID,
    twin_id: uuid.UUID,
    element_id: str,
    current_user: User = Depends(require_permission("twins", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get all sensors attached to a specific IFC element."""
    await verify_project_access(project_id, current_user, db)

    from app.services.intelligence.digital_twin_service import (
        get_element_sensors as svc_get_element_sensors,
    )

    sensors = await svc_get_element_sensors(db, twin_id, element_id)
    return sensors
