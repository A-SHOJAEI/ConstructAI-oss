from __future__ import annotations

import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.camera import Camera, SafetyZone
from app.models.user import User
from app.schemas.zone import (
    ZoneCreate,
    ZoneListResponse,
    ZoneResponse,
    ZoneUpdate,
)

router = APIRouter()


@router.post("/", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED)
async def create_zone(
    request: ZoneCreate,
    current_user: User = Depends(require_permission("zones", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new safety zone for a camera."""
    await verify_project_access(request.project_id, current_user, db)

    zone = SafetyZone(**request.model_dump())
    db.add(zone)
    await db.flush()
    await db.refresh(zone)
    return zone


@router.get("/", response_model=ZoneListResponse)
async def list_zones(
    camera_id: uuid.UUID = Query(...),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(require_permission("zones", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List zones for a camera (limited, typically < 50 per camera)."""
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    await verify_project_access(camera.project_id, current_user, db)

    result = await db.execute(
        select(SafetyZone)
        .where(SafetyZone.camera_id == camera_id)
        .order_by(SafetyZone.id)
        .limit(limit)
    )
    zones = list(result.scalars().all())
    return ZoneListResponse(data=cast(list[ZoneResponse], zones))


@router.patch("/{zone_id}", response_model=ZoneResponse)
async def update_zone(
    zone_id: uuid.UUID,
    request: ZoneUpdate,
    current_user: User = Depends(require_permission("zones", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a safety zone."""
    zone = await db.get(SafetyZone, zone_id)
    if not zone:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found")
    await verify_project_access(zone.project_id, current_user, db)
    _PROTECTED = {"id", "project_id", "camera_id", "created_at", "updated_at"}
    for field, value in request.model_dump(exclude_unset=True).items():
        if field not in _PROTECTED:
            setattr(zone, field, value)
    await db.flush()
    await db.refresh(zone)
    return zone


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone(
    zone_id: uuid.UUID,
    current_user: User = Depends(require_permission("zones", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a safety zone."""
    zone = await db.get(SafetyZone, zone_id)
    if not zone:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found")
    await verify_project_access(zone.project_id, current_user, db)
    await db.delete(zone)
    await db.flush()
