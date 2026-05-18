from __future__ import annotations

import ipaddress
import socket
import uuid
from typing import cast
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.camera import Camera
from app.models.user import User
from app.schemas.camera import (
    CameraCreate,
    CameraListResponse,
    CameraResponse,
    CameraUpdate,
)
from app.schemas.pagination import PaginationMeta

router = APIRouter()


def _validate_stream_url(url: str) -> None:
    """Reject stream URLs that could cause SSRF."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https", "rtsp", "rtmp"):
        raise HTTPException(status_code=422, detail="Invalid stream URL scheme")
    # Block metadata service and loopback
    hostname = parsed.hostname or ""
    if hostname in ("169.254.169.254", "metadata.google.internal"):
        raise HTTPException(status_code=422, detail="Stream URL not allowed")
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise HTTPException(
                status_code=422, detail="Stream URL must not target private addresses"
            )
    except ValueError:
        pass  # Not an IP literal — resolve hostname and validate resolved IPs

    # SEC-01: DNS resolution validation — block hostnames that resolve to private IPs
    try:
        hostname = parsed.hostname or ""
        if hostname:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for _, _, _, _, sockaddr in resolved:
                resolved_ip = ipaddress.ip_address(sockaddr[0])
                if resolved_ip.is_private or resolved_ip.is_loopback or resolved_ip.is_link_local:
                    raise ValueError(f"Hostname {hostname} resolves to private IP {resolved_ip}")
                # Check cloud metadata
                if str(resolved_ip) in ("169.254.169.254",):
                    raise ValueError(f"Hostname {hostname} resolves to metadata service IP")
    except socket.gaierror:
        raise ValueError(f"Cannot resolve hostname: {parsed.hostname}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
async def register_camera(
    request: CameraCreate,
    current_user: User = Depends(require_permission("cameras", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Register a new camera for a project."""
    await verify_project_access(request.project_id, current_user, db)
    _validate_stream_url(request.stream_url)

    camera = Camera(**request.model_dump())
    db.add(camera)
    await db.flush()
    await db.refresh(camera)
    return camera


@router.get("/", response_model=CameraListResponse)
async def list_cameras(
    project_id: uuid.UUID = Query(...),
    limit: int = Query(50, ge=1, le=100),
    cursor: uuid.UUID | None = Query(None),
    current_user: User = Depends(require_permission("cameras", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List cameras for a project with pagination."""
    await verify_project_access(project_id, current_user, db)

    query = select(Camera).where(Camera.project_id == project_id).order_by(Camera.id)
    if cursor:
        query = query.where(Camera.id > cursor)
    query = query.limit(limit + 1)

    result = await db.execute(query)
    items = list(result.scalars().all())
    has_more = len(items) > limit
    if has_more:
        items = items[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None
    return CameraListResponse(
        data=cast(list[CameraResponse], items),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


@router.get("/{camera_id}", response_model=CameraResponse)
async def get_camera(
    camera_id: uuid.UUID,
    current_user: User = Depends(require_permission("cameras", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a single camera by ID."""
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    await verify_project_access(camera.project_id, current_user, db)
    return camera


@router.patch("/{camera_id}", response_model=CameraResponse)
async def update_camera(
    camera_id: uuid.UUID,
    request: CameraUpdate,
    current_user: User = Depends(require_permission("cameras", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update camera settings."""
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    await verify_project_access(camera.project_id, current_user, db)
    update_data = request.model_dump(exclude_unset=True)
    if "stream_url" in update_data:
        _validate_stream_url(update_data["stream_url"])
    protected = {"id", "project_id", "created_at", "updated_at", "org_id"}
    for field, value in update_data.items():
        if field not in protected:
            setattr(camera, field, value)
    await db.flush()
    await db.refresh(camera)
    return camera


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(
    camera_id: uuid.UUID,
    current_user: User = Depends(require_permission("cameras", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a camera."""
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    await verify_project_access(camera.project_id, current_user, db)
    await db.delete(camera)
    await db.flush()
