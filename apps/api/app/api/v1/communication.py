"""Communication and reporting API endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import cast

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.voice import _validate_audio_magic_bytes
from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.communication import (
    RFI,
    DailyReport,
    MeetingMinutes,
    Submittal,
)
from app.models.user import User
from app.schemas.communication import (
    ActionItemUpdate,
    DailyReportCreate,
    DailyReportListResponse,
    DailyReportResponse,
    MeetingMinutesCreate,
    MeetingMinutesListResponse,
    MeetingMinutesResponse,
    MeetingMinutesUpdate,
    OverdueActionItem,
    OverdueActionItemsResponse,
    RFICreate,
    RFIListResponse,
    RFIResponse,
    SubmittalCreate,
    SubmittalListResponse,
    SubmittalResponse,
    TranscribeUploadResponse,
)
from app.schemas.pagination import PaginationMeta

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/daily-reports",
    response_model=DailyReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_daily_report(
    request: DailyReportCreate,
    current_user: User = Depends(require_permission("communication", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a daily construction report."""
    await verify_project_access(request.project_id, current_user, db)

    from app.services.communication.report_generator import (
        generate_daily_report,
    )

    report_data = await generate_daily_report(
        project_id=str(request.project_id),
        report_date=request.report_date,
    )

    report = DailyReport(
        project_id=request.project_id,
        report_date=request.report_date,
        content_markdown=report_data.get(
            "content_markdown",
        ),
        sections=report_data.get("sections", {}),
        status=report_data.get("status", "draft"),
    )
    db.add(report)
    await db.flush()
    await db.refresh(report)
    return report


@router.get(
    "/daily-reports",
    response_model=DailyReportListResponse,
)
async def list_daily_reports(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("communication", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List daily reports for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(DailyReport)
        .where(DailyReport.project_id == project_id)
        .order_by(DailyReport.report_date.desc())
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
            DailyReport,
            cursor_uuid,
        )
        if cursor_obj:
            query = query.where(DailyReport.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return DailyReportListResponse(
        data=cast(list[DailyReportResponse], items),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )


@router.post(
    "/meetings",
    response_model=MeetingMinutesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_meeting_minutes(
    request: MeetingMinutesCreate,
    current_user: User = Depends(require_permission("communication", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create meeting minutes record."""
    await verify_project_access(request.project_id, current_user, db)

    from datetime import time as time_type

    kwargs: dict = {
        "project_id": request.project_id,
        "meeting_type": request.meeting_type,
        "meeting_date": request.meeting_date,
        "title": request.title,
        "attendees": request.attendees,
    }
    if request.meeting_location:
        kwargs["meeting_location"] = request.meeting_location
    if request.notes:
        kwargs["notes"] = request.notes
    if request.agenda_items:
        kwargs["agenda_items"] = request.agenda_items
    if request.start_time:
        try:
            parts = request.start_time.split(":")
            kwargs["start_time"] = time_type(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid start_time format: {request.start_time!r}. Expected HH:MM.",
            )
    if request.end_time:
        try:
            parts = request.end_time.split(":")
            kwargs["end_time"] = time_type(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid end_time format: {request.end_time!r}. Expected HH:MM.",
            )

    minutes = MeetingMinutes(**kwargs)
    db.add(minutes)
    await db.flush()
    await db.refresh(minutes)
    return minutes


@router.get(
    "/meetings",
    response_model=MeetingMinutesListResponse,
)
async def list_meetings(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0, description="Records to skip (offset pagination)"),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("communication", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List meeting minutes for a project.

    Supports both cursor-based and offset-based pagination. When *cursor*
    is provided it takes precedence; otherwise *skip*/*limit* are applied.
    """
    await verify_project_access(project_id, current_user, db)

    query = (
        select(MeetingMinutes)
        .where(
            MeetingMinutes.project_id == project_id,
        )
        .order_by(MeetingMinutes.meeting_date.desc())
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
            MeetingMinutes,
            cursor_uuid,
        )
        if cursor_obj:
            query = query.where(MeetingMinutes.created_at < cursor_obj.created_at)
    elif skip > 0:
        query = query.offset(skip)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return MeetingMinutesListResponse(
        data=cast(list[MeetingMinutesResponse], items),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )


@router.post(
    "/rfis",
    response_model=RFIResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_rfi(
    request: RFICreate,
    current_user: User = Depends(require_permission("communication", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a Request for Information."""
    await verify_project_access(request.project_id, current_user, db)

    from app.services.communication.rfi_helper import (
        suggest_rfi_response,
    )

    suggestion = await suggest_rfi_response(
        subject=request.subject,
        question=request.question,
    )

    rfi = RFI(
        project_id=request.project_id,
        rfi_number=request.rfi_number,
        subject=request.subject,
        question=request.question,
        priority=request.priority,
        submitted_by=current_user.id,
        due_date=request.due_date,
        ai_suggested_response=suggestion.get("suggested_response"),
    )
    db.add(rfi)
    await db.flush()
    await db.refresh(rfi)
    return rfi


@router.get(
    "/rfis",
    response_model=RFIListResponse,
)
async def list_rfis(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("communication", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List RFIs for a project."""
    await verify_project_access(project_id, current_user, db)

    query = select(RFI).where(RFI.project_id == project_id).order_by(RFI.created_at.desc())
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_obj = await db.get(RFI, cursor_uuid)
        if cursor_obj:
            query = query.where(RFI.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return RFIListResponse(
        data=cast(list[RFIResponse], items),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )


@router.post(
    "/submittals",
    response_model=SubmittalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_submittal(
    request: SubmittalCreate,
    current_user: User = Depends(require_permission("communication", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a submittal record."""
    await verify_project_access(request.project_id, current_user, db)

    submittal = Submittal(
        project_id=request.project_id,
        submittal_number=request.submittal_number,
        title=request.title,
        spec_section=request.spec_section,
        submitted_by=current_user.id,
        document_urls=request.document_urls,
        due_date=request.due_date,
    )
    db.add(submittal)
    await db.flush()
    await db.refresh(submittal)
    return submittal


@router.get(
    "/submittals",
    response_model=SubmittalListResponse,
)
async def list_submittals(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("communication", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List submittals for a project."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(Submittal)
        .where(Submittal.project_id == project_id)
        .order_by(Submittal.created_at.desc())
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
            Submittal,
            cursor_uuid,
        )
        if cursor_obj:
            query = query.where(Submittal.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = str(items[-1].id) if has_more and items else None
    return SubmittalListResponse(
        data=cast(list[SubmittalResponse], items),
        meta=PaginationMeta(
            cursor=next_cursor,
            has_more=has_more,
        ),
    )


# ---------------------------------------------------------------------------
# Meeting Minutes CSV Export (AC-18)
# ---------------------------------------------------------------------------


@router.get("/{project_id}/meetings/export")
async def export_meeting_minutes(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("communication", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Export all meeting minutes for a project as a CSV file."""
    await verify_project_access(project_id, current_user, db)

    import csv
    import io

    query = (
        select(MeetingMinutes)
        .where(MeetingMinutes.project_id == project_id)
        .order_by(MeetingMinutes.meeting_date.desc())
    )
    result = await db.execute(query)
    meetings = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "title",
            "meeting_type",
            "meeting_date",
            "meeting_location",
            "status",
            "attendee_count",
            "action_item_count",
            "created_at",
        ]
    )
    for m in meetings:
        writer.writerow(
            [
                str(m.id),
                m.title,
                m.meeting_type,
                m.meeting_date.isoformat() if m.meeting_date else "",
                m.meeting_location or "",
                m.status or "draft",
                len(m.attendees) if m.attendees else 0,
                len(m.action_items) if m.action_items else 0,
                m.created_at.isoformat() if m.created_at else "",
            ]
        )

    csv_bytes = output.getvalue().encode("utf-8")
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=meeting_minutes_export.csv"},
    )


# ---------------------------------------------------------------------------
# Enhanced Meeting Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/meetings/{meeting_id}/transcribe",
    response_model=TranscribeUploadResponse,
)
async def transcribe_meeting(
    meeting_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("communication", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Upload audio file and transcribe a meeting."""
    # Verify meeting exists and user has access to its project
    meeting = await db.get(MeetingMinutes, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    await verify_project_access(meeting.project_id, current_user, db)

    # Validate audio file type
    allowed_audio_types = {
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/x-wav",
        "audio/mp4",
        "audio/m4a",
        "audio/x-m4a",
        "audio/ogg",
        "audio/webm",
        "audio/flac",
    }
    if not file.content_type or file.content_type not in allowed_audio_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid audio type. Accepted: {', '.join(sorted(allowed_audio_types))}",
        )

    # Enforce file size limit (100 MB for meeting recordings)
    max_audio_size = 100 * 1024 * 1024
    content = await file.read()
    if len(content) > max_audio_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Audio file exceeds maximum allowed size of 100 MB.",
        )

    # Validate file content matches audio format via magic bytes
    # (never trust client content_type alone)
    _validate_audio_magic_bytes(content)

    # Reset file position for downstream processing
    await file.seek(0)

    from app.services.communication.meeting_service import transcribe_meeting as _transcribe

    # Look up project_id from the meeting record for the service-layer scope check.
    meeting = await db.get(MeetingMinutes, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    await verify_project_access(meeting.project_id, current_user, db)
    result = await _transcribe(db, meeting_id, file, project_id=meeting.project_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return result


@router.get(
    "/meetings/action-items/overdue",
    response_model=OverdueActionItemsResponse,
)
async def get_overdue_action_items(
    project_id: uuid.UUID = Query(...),
    assigned_to: str | None = Query(default=None),
    current_user: User = Depends(require_permission("communication", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List overdue action items across meetings for a project."""
    await verify_project_access(project_id, current_user, db)

    from app.services.communication.meeting_service import (
        get_overdue_action_items as _get_overdue,
    )

    items = await _get_overdue(db, project_id, assigned_to)
    return OverdueActionItemsResponse(data=cast(list[OverdueActionItem], items), total=len(items))


@router.patch(
    "/meetings/{meeting_id}/action-items/{item_index}",
    response_model=dict,
)
async def update_action_item_status(
    meeting_id: uuid.UUID,
    request: ActionItemUpdate,
    item_index: int = Path(..., ge=0),
    current_user: User = Depends(require_permission("communication", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update the status of an action item."""
    # Verify meeting exists and user has access to its project
    meeting = await db.get(MeetingMinutes, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    await verify_project_access(meeting.project_id, current_user, db)
    from app.services.communication.meeting_service import (
        update_action_item_status as _update_status,
    )

    try:
        result = await _update_status(
            db, meeting_id, item_index, request.status, project_id=meeting.project_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except IndexError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if result is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return result


@router.patch(
    "/meetings/{meeting_id}",
    response_model=MeetingMinutesResponse,
)
async def update_meeting(
    meeting_id: uuid.UUID,
    request: MeetingMinutesUpdate,
    current_user: User = Depends(require_permission("communication", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update meeting details (location, status, notes, agenda, etc.)."""
    # Verify meeting exists and user has access to its project
    existing = await db.get(MeetingMinutes, meeting_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    await verify_project_access(existing.project_id, current_user, db)

    from app.services.communication.meeting_service import update_meeting as _update

    meeting = await _update(
        db, meeting_id, request.model_dump(exclude_unset=True), project_id=existing.project_id
    )
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting
