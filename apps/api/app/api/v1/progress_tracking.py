"""AI progress tracking API endpoints.

Routes for uploading site photos, getting AI-estimated progress,
managing snapshots, and comparing against schedule.
All routes are project-scoped: ``/projects/{project_id}/progress/...``
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.progress_tracking import ProgressPhoto, ProgressSnapshot
from app.models.user import User
from app.schemas.pagination import PaginationMeta
from app.schemas.progress_tracking import (
    ActivityMatchResult,
    ApplyProgressRequest,
    ApplyProgressResponse,
    ProgressAnalysisResponse,
    ProgressSnapshotListResponse,
    ProgressSnapshotResponse,
    ProgressVarianceListResponse,
    ProgressVarianceResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Upload and analyze a progress photo
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/progress/photos",
    response_model=ProgressAnalysisResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_progress_photo(
    project_id: uuid.UUID,
    photo: UploadFile = File(...),
    photo_url: str = Form(""),
    current_user: User = Depends(require_permission("progress", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Upload a site photo for AI progress analysis.

    Runs YOLO detection, maps detections to schedule activities,
    and estimates percent complete per matched activity.
    """
    await verify_project_access(project_id, current_user, db)

    photo_bytes = await photo.read()
    if not photo_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty photo file",
        )

    # Use filename as URL fallback
    url = photo_url or photo.filename or "uploaded_photo.jpg"

    from app.services.vision.progress_tracker import analyze_progress_photo

    result = await analyze_progress_photo(
        db=db,
        project_id=project_id,
        photo_bytes=photo_bytes,
        photo_url=url,
        uploaded_by=current_user.id,
    )

    return ProgressAnalysisResponse(
        photo_id=result.photo_id,
        project_id=result.project_id,
        worker_count=result.worker_count,
        equipment_detected=result.equipment_detected,
        activity_matches=[
            ActivityMatchResult(
                activity_id=m.activity_id,
                activity_name=m.activity_name,
                detection_class=m.detection_class,
                csi_division=m.csi_division,
                match_score=m.match_score,
                detection_confidence=m.detection_confidence,
            )
            for m in result.activity_matches
        ],
        estimated_progress={k: float(v) for k, v in result.estimated_progress.items()},
        overall_confidence=float(result.overall_confidence),
    )


# ---------------------------------------------------------------------------
# Get progress report (combined view)
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/progress/report",
)
async def get_progress_report(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("progress", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a combined progress report showing latest snapshot and variances."""
    await verify_project_access(project_id, current_user, db)

    # Get latest snapshot
    snap_result = await db.execute(
        select(ProgressSnapshot)
        .where(ProgressSnapshot.project_id == project_id)
        .order_by(ProgressSnapshot.snapshot_date.desc())
        .limit(1)
    )
    latest_snapshot = snap_result.scalars().first()

    # Get recent photos
    photo_result = await db.execute(
        select(ProgressPhoto)
        .where(ProgressPhoto.project_id == project_id)
        .order_by(ProgressPhoto.created_at.desc())
        .limit(5)
    )
    recent_photos = list(photo_result.scalars().all())

    # Compute variances if we have a snapshot
    variances: list[dict] = []
    if latest_snapshot and latest_snapshot.activities_progress:
        from app.services.vision.progress_tracker import compare_against_schedule

        progress = {k: Decimal(str(v)) for k, v in latest_snapshot.activities_progress.items()}
        variance_list = await compare_against_schedule(db, project_id, progress)
        variances = [
            {
                "activity_id": v.activity_id,
                "activity_name": v.activity_name,
                "scheduled_pct": float(v.scheduled_pct),
                "estimated_pct": float(v.estimated_pct),
                "variance_pct": float(v.variance_pct),
                "status": v.status,
            }
            for v in variance_list
        ]

    return {
        "project_id": str(project_id),
        "report_date": date.today().isoformat(),
        "overall_progress": (
            float(latest_snapshot.overall_progress)
            if latest_snapshot and latest_snapshot.overall_progress
            else None
        ),
        "variances": variances,
        "recent_photos": [
            {
                "id": str(p.id),
                "photo_url": p.photo_url,
                "overall_confidence": float(p.overall_confidence) if p.overall_confidence else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in recent_photos
        ],
        "latest_snapshot": (
            {
                "id": str(latest_snapshot.id),
                "snapshot_date": latest_snapshot.snapshot_date.isoformat(),
                "overall_progress": (
                    float(latest_snapshot.overall_progress)
                    if latest_snapshot.overall_progress
                    else None
                ),
                "activities_progress": latest_snapshot.activities_progress,
            }
            if latest_snapshot
            else None
        ),
    }


# ---------------------------------------------------------------------------
# List progress snapshots
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/progress/snapshots",
    response_model=ProgressSnapshotListResponse,
)
async def list_snapshots(
    project_id: uuid.UUID,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("progress", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List progress snapshots for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(ProgressSnapshot)
        .where(ProgressSnapshot.project_id == project_id)
        .order_by(ProgressSnapshot.snapshot_date.desc())
    )
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
            cursor_obj = await db.get(ProgressSnapshot, cursor_uuid)
            if cursor_obj:
                query = query.where(ProgressSnapshot.snapshot_date < cursor_obj.snapshot_date)
        except ValueError:
            pass

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return ProgressSnapshotListResponse(
        data=cast(list[ProgressSnapshotResponse], items),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


# ---------------------------------------------------------------------------
# Apply progress updates to schedule
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/progress/apply",
    response_model=ApplyProgressResponse,
)
async def apply_progress(
    project_id: uuid.UUID,
    request: ApplyProgressRequest,
    current_user: User = Depends(require_permission("progress", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Apply AI-estimated progress from a snapshot to schedule activities."""
    await verify_project_access(project_id, current_user, db)

    # Load the snapshot
    snap = await db.get(ProgressSnapshot, request.snapshot_id)
    if not snap or snap.project_id != project_id:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    progress = {k: Decimal(str(v)) for k, v in snap.activities_progress.items()}

    from app.services.vision.progress_tracker import auto_update_schedule_progress

    count = await auto_update_schedule_progress(db, project_id, progress, request.snapshot_id)

    return ApplyProgressResponse(
        activities_updated=count,
        snapshot_id=str(request.snapshot_id),
    )


# ---------------------------------------------------------------------------
# Get variances
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/progress/variances",
    response_model=ProgressVarianceListResponse,
)
async def get_variances(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("progress", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get variance analysis between AI-estimated and scheduled progress."""
    await verify_project_access(project_id, current_user, db)

    # Use latest snapshot
    snap_result = await db.execute(
        select(ProgressSnapshot)
        .where(ProgressSnapshot.project_id == project_id)
        .order_by(ProgressSnapshot.snapshot_date.desc())
        .limit(1)
    )
    snapshot = snap_result.scalars().first()

    if not snapshot or not snapshot.activities_progress:
        return ProgressVarianceListResponse(
            project_id=str(project_id),
            variances=[],
            summary={"ahead": 0, "behind": 0, "on_track": 0},
        )

    from app.services.vision.progress_tracker import compare_against_schedule

    progress = {k: Decimal(str(v)) for k, v in snapshot.activities_progress.items()}
    variance_list = await compare_against_schedule(db, project_id, progress)

    variances = [
        ProgressVarianceResponse(
            activity_id=v.activity_id,
            activity_name=v.activity_name,
            scheduled_pct=float(v.scheduled_pct),
            estimated_pct=float(v.estimated_pct),
            variance_pct=float(v.variance_pct),
            status=v.status,
        )
        for v in variance_list
    ]

    summary = {
        "ahead": sum(1 for v in variance_list if v.status == "ahead"),
        "behind": sum(1 for v in variance_list if v.status == "behind"),
        "on_track": sum(1 for v in variance_list if v.status == "on_track"),
    }

    return ProgressVarianceListResponse(
        project_id=str(project_id),
        variances=variances,
        summary=summary,
    )
