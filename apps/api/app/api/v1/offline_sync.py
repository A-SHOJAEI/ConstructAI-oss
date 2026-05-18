"""Offline-first mobile sync API endpoints.

Routes for push/pull sync, conflict resolution, sync status,
and deferred photo uploads.
All routes are project-scoped: ``/projects/{project_id}/sync/...``
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.offline_sync import (
    ConflictLogResponse,
    ConflictResolveRequest,
    PendingPhotoResponse,
    PhotoUploadResponse,
    SyncPullRequest,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
    SyncStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/sync/push — Push changes from device
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sync/push",
    response_model=SyncPushResponse,
)
async def sync_push(
    project_id: uuid.UUID,
    request: SyncPushRequest,
    current_user: User = Depends(require_permission("field_data", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Push offline changes from a mobile device to the server.

    Processes each item with LWW conflict resolution. Creates new entities
    if they don't exist, updates existing ones if the client timestamp is
    newer, or logs a conflict if the server is newer.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.sync.offline_sync_engine import sync_push as _sync_push

    items_data = [item.model_dump() for item in request.items]

    try:
        result = await _sync_push(
            db=db,
            project_id=project_id,
            device_id=request.device_id,
            user_id=current_user.id,
            items=items_data,
        )
        return SyncPushResponse(
            processed=result.processed,
            created=result.created,
            updated=result.updated,
            conflicts=result.conflicts,
            errors=result.errors,
            conflict_details=result.conflict_details,
            error_details=result.error_details,
            server_timestamp=result.server_timestamp,
        )
    except Exception as exc:
        logger.error("Sync push failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Sync push failed",
        )


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/sync/pull — Pull updates from server
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sync/pull",
    response_model=SyncPullResponse,
)
async def sync_pull(
    project_id: uuid.UUID,
    request: SyncPullRequest,
    current_user: User = Depends(require_permission("field_data", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Pull updated records from the server since the last sync.

    Returns all records updated after the `since` timestamp across
    the requested entity types.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.sync.offline_sync_engine import sync_pull as _sync_pull

    try:
        result = await _sync_pull(
            db=db,
            project_id=project_id,
            device_id=request.device_id,
            since=request.since,
            entity_types=request.entity_types,
            limit=request.limit,
        )
        return SyncPullResponse(
            items=result.items,
            server_timestamp=result.server_timestamp,
            has_more=result.has_more,
            total_available=result.total_available,
        )
    except Exception as exc:
        logger.error("Sync pull failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Sync pull failed",
        )


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/sync/status/{device_id} — Get sync status
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/sync/status/{device_id}",
    response_model=SyncStatusResponse,
)
async def get_sync_status(
    project_id: uuid.UUID,
    device_id: str,
    current_user: User = Depends(require_permission("field_data", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the current sync status for a specific device and project."""
    await verify_project_access(project_id, current_user, db)

    from app.services.sync.offline_sync_engine import get_sync_status as _get_status

    state = await _get_status(db, project_id, device_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No sync state found for this device",
        )
    return state


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/sync/conflicts — List conflicts
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/sync/conflicts",
    response_model=list[ConflictLogResponse],
)
async def get_conflicts(
    project_id: uuid.UUID,
    device_id: str | None = Query(default=None),
    resolved: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_permission("field_data", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List conflict logs for a project, optionally filtered by device."""
    await verify_project_access(project_id, current_user, db)

    from app.services.sync.offline_sync_engine import list_conflicts as _list_conflicts

    conflicts = await _list_conflicts(
        db, project_id, device_id=device_id, resolved=resolved, limit=limit
    )
    return conflicts


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/sync/conflicts/{conflict_id}/resolve
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sync/conflicts/{conflict_id}/resolve",
    response_model=ConflictLogResponse,
)
async def resolve_conflict(
    project_id: uuid.UUID,
    conflict_id: uuid.UUID,
    request: ConflictResolveRequest,
    current_user: User = Depends(require_permission("field_data", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Manually resolve a sync conflict."""
    await verify_project_access(project_id, current_user, db)

    from datetime import UTC, datetime

    from app.models.offline_sync import ConflictLog

    conflict = await db.get(ConflictLog, conflict_id)
    if conflict is None or conflict.project_id != project_id:
        raise HTTPException(status_code=404, detail="Conflict not found")

    if request.resolution == "manual_merge" and request.merged_data:
        # Apply merged data to the entity
        from app.services.sync.offline_sync_engine import _apply_entity_upsert

        try:
            await _apply_entity_upsert(
                db,
                conflict.entity_type,
                conflict.entity_id,
                request.merged_data,
                project_id,
                current_user.id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to apply merged data: {exc}",
            )
    elif request.resolution == "client_wins":
        # Re-apply client data
        from app.services.sync.offline_sync_engine import _apply_entity_upsert

        try:
            await _apply_entity_upsert(
                db,
                conflict.entity_type,
                conflict.entity_id,
                conflict.client_data,
                project_id,
                current_user.id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to apply client data: {exc}",
            )
    # server_wins: no data change needed (server already has its version)

    conflict.resolution = request.resolution
    conflict.is_resolved = True
    conflict.resolved_by = current_user.id
    conflict.resolved_at = datetime.now(UTC)

    await db.flush()
    await db.refresh(conflict)
    return conflict


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/sync/photos/{photo_id} — Upload queued photo
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/sync/photos/{photo_id}",
    response_model=PhotoUploadResponse,
)
async def upload_photo(
    project_id: uuid.UUID,
    photo_id: uuid.UUID,
    file: UploadFile,
    current_user: User = Depends(require_permission("field_data", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a previously queued photo from an offline device."""
    await verify_project_access(project_id, current_user, db)

    from app.services.sync.offline_sync_engine import process_photo_upload

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    try:
        result = await process_photo_upload(db, project_id, photo_id, file_bytes)
        return PhotoUploadResponse(
            id=photo_id,
            s3_key=result.get("s3_key"),
            status=result.get("status", "completed"),
            file_size_bytes=result.get("file_size"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Photo upload failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Photo upload failed",
        )


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/sync/photos/pending — List pending photos
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/sync/photos/pending",
    response_model=list[PendingPhotoResponse],
)
async def list_pending_photos(
    project_id: uuid.UUID,
    device_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(require_permission("field_data", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List photos pending upload for a project."""
    await verify_project_access(project_id, current_user, db)

    from sqlalchemy import select

    from app.models.offline_sync import PhotoUploadQueue

    query = (
        select(PhotoUploadQueue)
        .where(
            PhotoUploadQueue.project_id == project_id,
            PhotoUploadQueue.status == "pending",
        )
        .order_by(PhotoUploadQueue.created_at.desc())
        .limit(limit)
    )

    if device_id:
        query = query.where(PhotoUploadQueue.device_id == device_id)

    result = await db.execute(query)
    return list(result.scalars().all())
