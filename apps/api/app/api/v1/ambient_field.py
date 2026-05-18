"""Ambient field intelligence API endpoints.

Routes for ingesting GPS pings, equipment telemetry, badge events,
aggregating daily snapshots, and generating reports from ambient data.
All routes are project-scoped: ``/projects/{project_id}/ambient/...``
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.ambient_field import (
    AggregateRequest,
    AmbientSnapshotResponse,
    BadgeEventBatchRequest,
    FieldPingBatchRequest,
    GenerateReportRequest,
    IngestResponse,
    TelemetryBatchRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/ambient/pings — Batch ingest GPS pings
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/ambient/pings",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_pings(
    project_id: uuid.UUID,
    request: FieldPingBatchRequest,
    current_user: User = Depends(require_permission("field_data", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Batch ingest GPS location pings from worker mobile devices.

    Validates coordinates, deduplicates by (worker_id, timestamp within 5s),
    and inserts valid pings.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.field.ambient_intelligence import ingest_field_pings

    pings_data = [p.model_dump() for p in request.pings]
    count_submitted = len(pings_data)

    try:
        count_inserted = await ingest_field_pings(db, project_id, pings_data)
    except Exception as exc:
        logger.error("Ping ingestion failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest pings",
        )

    return IngestResponse(
        count_inserted=count_inserted,
        count_submitted=count_submitted,
        count_skipped=count_submitted - count_inserted,
    )


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/ambient/telemetry — Batch ingest telemetry
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/ambient/telemetry",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_telemetry(
    project_id: uuid.UUID,
    request: TelemetryBatchRequest,
    current_user: User = Depends(require_permission("field_data", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Batch ingest equipment telemetry from IoT devices.

    Validates status enum (idle/running/off) and inserts valid records.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.field.ambient_intelligence import ingest_equipment_telemetry

    telemetry_data = [t.model_dump() for t in request.telemetry]
    count_submitted = len(telemetry_data)

    try:
        count_inserted = await ingest_equipment_telemetry(db, project_id, telemetry_data)
    except Exception as exc:
        logger.error("Telemetry ingestion failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest telemetry",
        )

    return IngestResponse(
        count_inserted=count_inserted,
        count_submitted=count_submitted,
        count_skipped=count_submitted - count_inserted,
    )


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/ambient/badge-events — Batch ingest badge events
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/ambient/badge-events",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_badge_events(
    project_id: uuid.UUID,
    request: BadgeEventBatchRequest,
    current_user: User = Depends(require_permission("field_data", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Batch ingest badge check-in/check-out events from site gates.

    Validates event_type (check_in/check_out/break_start/break_end).
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.field.ambient_intelligence import ingest_badge_events as _ingest

    events_data = [e.model_dump() for e in request.events]
    count_submitted = len(events_data)

    try:
        count_inserted = await _ingest(db, project_id, events_data)
    except Exception as exc:
        logger.error("Badge event ingestion failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest badge events",
        )

    return IngestResponse(
        count_inserted=count_inserted,
        count_submitted=count_submitted,
        count_skipped=count_submitted - count_inserted,
    )


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/ambient/snapshot — Get daily snapshot
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/ambient/snapshot",
    response_model=AmbientSnapshotResponse,
)
async def get_snapshot(
    project_id: uuid.UUID,
    snapshot_date: date = Query(..., description="Date for the snapshot"),
    current_user: User = Depends(require_permission("field_data", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get the aggregated daily snapshot for a given date."""
    await verify_project_access(project_id, current_user, db)

    from sqlalchemy import select

    from app.models.ambient_field import AmbientDailySnapshot

    result = await db.execute(
        select(AmbientDailySnapshot).where(
            AmbientDailySnapshot.project_id == project_id,
            AmbientDailySnapshot.snapshot_date == snapshot_date,
        )
    )
    snapshot = result.scalars().first()
    if not snapshot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No snapshot found for {snapshot_date}",
        )
    return snapshot


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/ambient/aggregate — Trigger aggregation
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/ambient/aggregate",
    response_model=AmbientSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
async def trigger_aggregation(
    project_id: uuid.UUID,
    request: AggregateRequest,
    current_user: User = Depends(require_permission("field_data", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Trigger aggregation of ambient data into a daily snapshot.

    Queries all pings, telemetry, and badge events for the specified date
    and computes workforce, equipment, and site activity summaries.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.field.ambient_intelligence import aggregate_daily_snapshot

    try:
        snapshot = await aggregate_daily_snapshot(db, project_id, request.snapshot_date)
        return snapshot
    except Exception as exc:
        logger.error("Aggregation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to aggregate daily snapshot",
        )


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/ambient/generate-report — Generate report
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/ambient/generate-report",
    status_code=status.HTTP_201_CREATED,
)
async def generate_report(
    project_id: uuid.UUID,
    request: GenerateReportRequest,
    current_user: User = Depends(require_permission("daily_reports", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a daily report from an ambient snapshot.

    Loads the snapshot for the date, converts to the format expected by
    the daily report generator, and creates the report.
    """
    await verify_project_access(project_id, current_user, db)

    from app.services.field.ambient_intelligence import generate_report_from_snapshot

    try:
        report = await generate_report_from_snapshot(
            db=db,
            project_id=project_id,
            snapshot_date=request.snapshot_date,
            generated_by=current_user.id,
        )
        return {"report_id": str(report.id), "status": report.status}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.error("Report generation from snapshot failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate report from snapshot",
        )
