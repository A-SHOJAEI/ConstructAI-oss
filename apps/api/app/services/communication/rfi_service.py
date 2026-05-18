"""RFI workflow service — business logic, validation, and event publishing."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.communication import RFI, RfiAttachment, RfiResponse

logger = logging.getLogger(__name__)

# Strong-ref registry for fire-and-forget asyncio tasks (prevents RUF006 GC).
_BACKGROUND_TASKS: set = set()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"open", "void"},
    "open": {"pending_review", "answered", "closed", "void"},
    "pending_review": {"open", "answered", "closed", "void"},
    "answered": {"closed", "open", "void"},
    "closed": {"open"},  # reopen
    "void": set(),  # terminal
}

VALID_STATUSES = {"draft", "open", "pending_review", "answered", "closed", "void"}
VALID_PRIORITIES = {"urgent", "high", "normal", "low"}

OVERDUE_DAYS: dict[str, int] = {
    "urgent": 3,
    "high": 5,
    "normal": 7,
    "low": 14,
}

# Fields that Procore owns — cannot be updated on procore-sourced RFIs
_PROCORE_OWNED_FIELDS = {"subject", "question", "rfi_number"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def check_overdue(rfi: RFI) -> bool:
    """Return True if *rfi* is overdue based on priority thresholds."""
    if rfi.status not in ("open", "pending_review"):
        return False
    threshold_days = OVERDUE_DAYS.get(rfi.priority, 7)
    deadline = rfi.due_date or (
        rfi.created_at.date() + timedelta(days=threshold_days) if rfi.created_at else None
    )
    if deadline is None:
        return False
    return datetime.now(UTC).date() > deadline


def _compute_days_open(rfi: RFI) -> int | None:
    """Number of calendar days since the RFI was created."""
    if rfi.created_at is None:
        return None
    end = rfi.date_closed.date() if rfi.date_closed else datetime.now(UTC).date()
    return (end - rfi.created_at.date()).days


async def _publish_rfi_event(
    event_type: str,
    rfi: RFI,
    extra_data: dict | None = None,
) -> None:
    """Best-effort Kafka event for RFI lifecycle changes."""
    try:
        from app.services.messaging.kafka_producer import KafkaEventProducer

        producer = KafkaEventProducer()
        data: dict[str, Any] = {
            "rfi_id": str(rfi.id),
            "project_id": str(rfi.project_id),
            "rfi_number": rfi.rfi_number,
            "status": rfi.status,
            "priority": rfi.priority,
            "subject": rfi.subject,
        }
        if extra_data:
            data.update(extra_data)
        await producer.publish(
            event_type=f"constructai.communication.rfi.{event_type}",
            data=data,
            source="/rfi-service",
        )
    except Exception:
        logger.warning("Failed to publish RFI event %s", event_type, exc_info=True)


async def _trigger_resolution_agent(
    rfi_id: uuid.UUID,
    project_id: uuid.UUID,
    subject: str,
    question: str,
    rfi_number: str,
) -> None:
    """Run the RFI Resolution Agent Stage 1 for a locally-created RFI.

    This mirrors ``_handle_rfi_resolution_requested`` in
    ``procore_webhook_processor.py`` so that locally-created RFIs receive
    the same unnecessary-check that Procore-sourced RFIs do.

    Runs as a fire-and-forget ``asyncio.Task``; exceptions are caught
    so they never propagate to the caller.

    Creates its own DB session to avoid sharing the request-scoped session
    which will be closed when the request handler returns.
    """
    try:
        from app.database import async_session
        from app.models.communication import RfiResolutionLog
        from app.services.agents.rfi_resolution_agent import run_rfi_unnecessary_check

        check_result = await run_rfi_unnecessary_check(
            rfi_id=rfi_id,
            project_id=project_id,
            subject=subject,
            question=question,
        )

        # Log the check result (matches webhook processor logic)
        async with async_session() as db:
            log = RfiResolutionLog(
                rfi_id=rfi_id,
                project_id=project_id,
                stage_reached=1,
                was_unnecessary=check_result.get("is_unnecessary", False),
                unnecessary_source=check_result.get("unnecessary_source"),
                unnecessary_reason=check_result.get("unnecessary_reason"),
                similar_rfi_count=len(check_result.get("similar_rfis", [])),
            )
            db.add(log)
            await db.commit()

        logger.info(
            "RFI resolution check for %s: unnecessary=%s",
            rfi_number,
            check_result.get("is_unnecessary", False),
        )
    except Exception:
        logger.warning(
            "RFI resolution agent failed for RFI %s",
            rfi_id,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# RFI number generation
# ---------------------------------------------------------------------------


async def generate_rfi_number(db: AsyncSession, project_id: uuid.UUID) -> str:
    """Auto-generate the next ``RFI-NNN`` number for *project_id*.

    Uses a PostgreSQL advisory lock scoped to the project to prevent
    duplicate numbers under concurrent requests.
    """
    from sqlalchemy import text

    # Advisory lock scoped to the project to prevent duplicate numbers
    lock_key = hash(str(project_id)) & 0x7FFFFFFF  # PostgreSQL advisory lock needs int
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

    # Extract numeric suffix from "RFI-NNN" patterns and find the max
    sql = text("""
        SELECT MAX(
            CAST(SUBSTRING(rfi_number FROM 'RFI-(\\d+)') AS INTEGER)
        )
        FROM rfis
        WHERE project_id = :pid
          AND rfi_number ~ '^RFI-\\d+$'
    """)
    result = await db.execute(sql, {"pid": project_id})
    max_num = result.scalar()

    if max_num is None:
        # Fallback: count all RFIs for project
        count_q = select(func.count()).select_from(RFI).where(RFI.project_id == project_id)
        count_result = await db.execute(count_q)
        max_num = count_result.scalar() or 0

    next_num = (max_num or 0) + 1
    return f"RFI-{next_num:03d}"


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


async def create_rfi(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: dict[str, Any],
    submitted_by: uuid.UUID,
) -> RFI:
    """Create a new RFI with auto-generated number and AI suggestion."""
    from app.services.communication.rfi_helper import suggest_rfi_response

    rfi_number = await generate_rfi_number(db, project_id)

    status = data.get("status", "open")
    if status not in ("draft", "open"):
        raise ValueError(f"Initial RFI status must be 'draft' or 'open', got '{status}'")

    priority = data.get("priority", "normal")
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"Invalid priority '{priority}'")

    # Default due_date based on priority
    due_date = data.get("due_date")
    if due_date is None:
        threshold = OVERDUE_DAYS.get(priority, 7)
        due_date = datetime.now(UTC).date() + timedelta(days=threshold)

    # AI suggestion — wrapped so failures never block RFI creation
    _MIN_SUGGESTION_LENGTH = 10
    ai_suggested_response = None
    try:
        suggestion = await suggest_rfi_response(
            subject=data["subject"],
            question=data["question"],
            db=db,
            project_id=project_id,
        )
        raw = suggestion.get("suggested_response")
        if raw and isinstance(raw, str) and len(raw.strip()) >= _MIN_SUGGESTION_LENGTH:
            ai_suggested_response = raw
        else:
            logger.info("AI suggestion for RFI %s discarded (empty or too short)", rfi_number)
    except Exception:
        logger.warning(
            "AI suggestion failed for RFI %s; continuing without suggestion",
            rfi_number,
            exc_info=True,
        )

    assigned_to = data.get("assigned_to")

    rfi = RFI(
        project_id=project_id,
        rfi_number=rfi_number,
        subject=data["subject"],
        question=data["question"],
        status=status,
        priority=priority,
        submitted_by=submitted_by,
        assigned_to=assigned_to,
        ball_in_court=assigned_to,
        due_date=due_date,
        spec_section=data.get("spec_section"),
        drawing_reference=data.get("drawing_reference"),
        cost_impact=data.get("cost_impact"),
        schedule_impact=data.get("schedule_impact"),
        cost_impact_amount=data.get("cost_impact_amount"),
        schedule_impact_days=data.get("schedule_impact_days"),
        distribution_list=data.get("distribution_list", []),
        ai_suggested_response=ai_suggested_response,
    )

    db.add(rfi)
    await db.flush()
    await db.refresh(rfi)

    # Index for similarity search (use savepoint so failures don't abort the RFI insert)
    try:
        from app.services.rag.retrieval import index_rfi_for_search

        async with db.begin_nested():
            await index_rfi_for_search(
                db=db,
                rfi_id=rfi.id,
                project_id=rfi.project_id,
                subject=rfi.subject,
                question=rfi.question,
                answer=rfi.answer or "",
                rfi_number=rfi.rfi_number,
            )
    except Exception:
        logger.warning("Failed to index RFI %s for search", rfi.id, exc_info=True)

    await _publish_rfi_event("created", rfi)

    # Trigger RFI Resolution Agent (Stage 1: unnecessary check)
    # Fire-and-forget: don't block RFI creation on agent processing.
    # This mirrors the same trigger that the Procore webhook fires via
    # _handle_rfi_resolution_requested in procore_webhook_processor.py.
    if status == "open":
        try:
            _resolution_task = asyncio.create_task(
                _trigger_resolution_agent(
                    rfi_id=rfi.id,
                    project_id=rfi.project_id,
                    subject=rfi.subject,
                    question=rfi.question,
                    rfi_number=rfi.rfi_number,
                ),
            )
            _BACKGROUND_TASKS.add(_resolution_task)
            _resolution_task.add_done_callback(_BACKGROUND_TASKS.discard)
        except Exception:
            logger.warning("Failed to schedule RFI resolution agent for RFI %s", rfi.id)

    return rfi


async def update_rfi(
    db: AsyncSession,
    rfi_id: uuid.UUID,
    project_id: uuid.UUID,
    data: dict[str, Any],
    current_user_id: uuid.UUID,
) -> RFI:
    """Update an RFI with status transition validation."""
    rfi = await _get_rfi_or_raise(db, rfi_id, project_id)

    # Status transition validation
    new_status = data.get("status")
    if new_status and new_status != rfi.status:
        allowed = VALID_TRANSITIONS.get(rfi.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Cannot transition from '{rfi.status}' to '{new_status}'. "
                f"Allowed: {allowed or 'none (terminal state)'}"
            )
        rfi.status = new_status

        if new_status == "answered" and rfi.date_answered is None:
            rfi.date_answered = datetime.now(UTC)
        elif new_status == "closed":
            rfi.date_closed = datetime.now(UTC)
            if rfi.date_answered is None:
                rfi.date_answered = datetime.now(UTC)
        elif new_status == "open" and rfi.date_closed:
            # Reopen — clear closed date
            rfi.date_closed = None

    # Protect Procore-owned fields
    if rfi.data_source == "procore":
        for field in _PROCORE_OWNED_FIELDS:
            if field in data and data[field] is not None:
                data.pop(field)

    # Apply field updates
    updatable = {
        "subject",
        "question",
        "answer",
        "priority",
        "assigned_to",
        "ball_in_court",
        "due_date",
        "spec_section",
        "drawing_reference",
        "cost_impact",
        "schedule_impact",
        "cost_impact_amount",
        "schedule_impact_days",
        "distribution_list",
    }
    for field in updatable:
        if field in data and data[field] is not None:
            setattr(rfi, field, data[field])

    # Ball-in-court follows assigned_to if changed
    if "assigned_to" in data and data["assigned_to"] is not None:
        rfi.ball_in_court = data["assigned_to"]

    rfi.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(rfi)

    await _publish_rfi_event("updated", rfi)
    return rfi


async def respond_to_rfi(
    db: AsyncSession,
    rfi_id: uuid.UUID,
    project_id: uuid.UUID,
    responder_id: uuid.UUID,
    response_text: str,
) -> RfiResponse:
    """Add a response to an RFI and update status/ball-in-court."""
    rfi = await _get_rfi_or_raise(db, rfi_id, project_id)

    if rfi.status == "void":
        raise ValueError("Cannot respond to a voided RFI")

    response = RfiResponse(
        rfi_id=rfi_id,
        responder_id=responder_id,
        response_text=response_text,
        status="pending",
    )
    db.add(response)

    # Transition to pending_review if currently open
    if rfi.status == "open":
        rfi.status = "pending_review"

    # Ball returns to the person who submitted the RFI
    if rfi.submitted_by:
        rfi.ball_in_court = rfi.submitted_by

    if rfi.responded_at is None:
        rfi.responded_at = datetime.now(UTC)

    rfi.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(response)

    # Index updated RFI for similarity search (use savepoint so failures don't abort the response)
    try:
        from app.services.rag.retrieval import index_rfi_for_search

        async with db.begin_nested():
            await index_rfi_for_search(
                db=db,
                rfi_id=rfi.id,
                project_id=rfi.project_id,
                subject=rfi.subject,
                question=rfi.question,
                answer=rfi.answer or "",
                rfi_number=rfi.rfi_number,
            )
    except Exception:
        logger.warning("Failed to index RFI %s for search", rfi.id, exc_info=True)

    await _publish_rfi_event(
        "response_added",
        rfi,
        {
            "response_id": str(response.id),
            "responder_id": str(responder_id),
        },
    )

    # Notify relevant users about the new response
    try:
        from app.services.notifications.event_notifier import notify

        notify_user_ids = []
        if rfi.submitted_by:
            notify_user_ids.append(rfi.submitted_by)
        if rfi.assigned_to and rfi.assigned_to != responder_id:
            if rfi.assigned_to not in notify_user_ids:
                notify_user_ids.append(rfi.assigned_to)
        # Remove the responder themselves from the notification list
        notify_user_ids = [uid for uid in notify_user_ids if uid != responder_id]

        if notify_user_ids:
            await notify(
                db,
                event_type="rfi.response_added",
                project_id=project_id,
                user_ids=notify_user_ids,
                context_data={
                    "rfi_number": rfi.rfi_number,
                    "subject": rfi.subject,
                    "responder_id": str(responder_id),
                },
            )
    except Exception:
        logger.warning(
            "Failed to send RFI response notification for RFI %s",
            rfi.id,
            exc_info=True,
        )

    return response


async def close_rfi(
    db: AsyncSession,
    rfi_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    answer: str | None = None,
) -> RFI:
    """Close an RFI with optional final answer."""
    rfi = await _get_rfi_or_raise(db, rfi_id, project_id)

    allowed = VALID_TRANSITIONS.get(rfi.status, set())
    if "closed" not in allowed:
        raise ValueError(
            f"Cannot close RFI in '{rfi.status}' status. Allowed transitions: {allowed or 'none'}"
        )

    rfi.status = "closed"
    rfi.date_closed = datetime.now(UTC)
    if answer:
        rfi.answer = answer
    if rfi.date_answered is None:
        rfi.date_answered = datetime.now(UTC)

    # Approve all pending responses
    stmt = (
        update(RfiResponse)
        .where(RfiResponse.rfi_id == rfi_id, RfiResponse.status == "pending")
        .values(status="approved")
    )
    await db.execute(stmt)

    rfi.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(rfi)

    # Index closed RFI with final answer for similarity search (savepoint protects the close)
    try:
        from app.services.rag.retrieval import index_rfi_for_search

        async with db.begin_nested():
            await index_rfi_for_search(
                db=db,
                rfi_id=rfi.id,
                project_id=rfi.project_id,
                subject=rfi.subject,
                question=rfi.question,
                answer=rfi.answer or "",
                rfi_number=rfi.rfi_number,
            )
    except Exception:
        logger.warning("Failed to index RFI %s for search", rfi.id, exc_info=True)

    await _publish_rfi_event("closed", rfi)
    return rfi


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


async def get_rfi_detail(
    db: AsyncSession,
    rfi_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict:
    """Fetch an RFI with its responses, attachments, and computed fields."""
    rfi = await _get_rfi_or_raise(db, rfi_id, project_id)

    # Responses
    resp_q = (
        select(RfiResponse)
        .where(RfiResponse.rfi_id == rfi_id)
        .order_by(RfiResponse.created_at.asc())
    )
    resp_result = await db.execute(resp_q)
    responses = list(resp_result.scalars().all())

    # Attachments
    att_q = (
        select(RfiAttachment)
        .where(RfiAttachment.rfi_id == rfi_id)
        .order_by(RfiAttachment.uploaded_at.asc())
    )
    att_result = await db.execute(att_q)
    attachments = list(att_result.scalars().all())

    # Generate presigned URLs
    attachment_items = []
    for att in attachments:
        download_url = None
        try:
            from app.utils.s3 import generate_presigned_url

            download_url = generate_presigned_url(att.file_path)
        except Exception:
            logger.warning("Failed to generate presigned URL for %s", att.file_path)

        attachment_items.append(
            {
                "id": att.id,
                "rfi_id": att.rfi_id,
                "file_path": att.file_path,
                "file_name": att.file_name,
                "file_type": att.file_type,
                "file_size_bytes": att.file_size_bytes,
                "uploaded_by": att.uploaded_by,
                "uploaded_at": att.uploaded_at,
                "download_url": download_url,
            }
        )

    return {
        **_rfi_to_dict(rfi),
        "is_overdue": check_overdue(rfi),
        "days_open": _compute_days_open(rfi),
        "responses": [
            {
                "id": r.id,
                "rfi_id": r.rfi_id,
                "responder_id": r.responder_id,
                "response_text": r.response_text,
                "status": r.status,
                "responded_at": r.responded_at,
                "created_at": r.created_at,
            }
            for r in responses
        ],
        "attachments": attachment_items,
    }


async def list_rfis(
    db: AsyncSession,
    project_id: uuid.UUID,
    status_filter: str | None = None,
    priority_filter: str | None = None,
    assigned_to_filter: uuid.UUID | None = None,
    ball_in_court_filter: uuid.UUID | None = None,
    overdue_only: bool = False,
    search: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    """List RFIs for a project with filtering and cursor pagination."""
    query = select(RFI).where(RFI.project_id == project_id)

    if status_filter:
        query = query.where(RFI.status == status_filter)
    if priority_filter:
        query = query.where(RFI.priority == priority_filter)
    if assigned_to_filter:
        query = query.where(RFI.assigned_to == assigned_to_filter)
    if ball_in_court_filter:
        query = query.where(RFI.ball_in_court == ball_in_court_filter)
    if search:
        # SECURITY [M-15]: Escape LIKE wildcards to prevent pattern injection
        escaped = search.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        query = query.where(
            RFI.subject.ilike(pattern) | RFI.question.ilike(pattern) | RFI.rfi_number.ilike(pattern)
        )

    # Apply overdue filter in SQL to ensure correct pagination
    if overdue_only:
        query = query.where(
            RFI.status.in_(["open", "pending_review"]),
            RFI.due_date < datetime.now(UTC).date(),
        )

    query = query.order_by(RFI.created_at.desc())

    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
            cursor_obj = await db.get(RFI, cursor_uuid)
            if cursor_obj:
                query = query.where(RFI.created_at < cursor_obj.created_at)
        except ValueError:
            pass

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    data = []
    for rfi in items:
        d = _rfi_to_dict(rfi)
        d["is_overdue"] = check_overdue(rfi)
        d["days_open"] = _compute_days_open(rfi)
        d["responses"] = []
        d["attachments"] = []
        data.append(d)

    next_cursor = str(items[-1].id) if has_more and items else None
    return {
        "data": data,
        "meta": {"cursor": next_cursor, "has_more": has_more},
    }


# ---------------------------------------------------------------------------
# Statistics and export
# ---------------------------------------------------------------------------


async def get_rfi_stats(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Compute aggregate RFI statistics for a project."""
    # Counts by status
    q = select(RFI.status, func.count()).where(RFI.project_id == project_id).group_by(RFI.status)
    result = await db.execute(q)
    status_counts: dict[str, int] = {}
    total = 0
    for row in result.all():
        status_counts[row[0]] = row[1]
        total += row[1]

    # Average days to close
    avg_q = select(func.avg(func.extract("epoch", RFI.date_closed - RFI.created_at) / 86400)).where(
        RFI.project_id == project_id,
        RFI.date_closed.isnot(None),
    )
    avg_result = await db.execute(avg_q)
    avg_days = avg_result.scalar()

    # Overdue count (need to load open/pending_review RFIs and check)
    open_q = select(RFI).where(
        RFI.project_id == project_id,
        RFI.status.in_(["open", "pending_review"]),
    )
    open_result = await db.execute(open_q)
    open_rfis = list(open_result.scalars().all())
    overdue_count = sum(1 for r in open_rfis if check_overdue(r))

    return {
        "total": total,
        "draft": status_counts.get("draft", 0),
        "open": status_counts.get("open", 0),
        "pending_review": status_counts.get("pending_review", 0),
        "answered": status_counts.get("answered", 0),
        "closed": status_counts.get("closed", 0),
        "void": status_counts.get("void", 0),
        "overdue": overdue_count,
        "avg_response_days": round(avg_days, 1) if avg_days else None,
    }


async def export_rfis_csv(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    limit: int = 10_000,
) -> bytes:
    """Generate a CSV export of RFIs for a project (bounded by *limit*)."""
    q = select(RFI).where(RFI.project_id == project_id).order_by(RFI.rfi_number).limit(limit)
    result = await db.execute(q)
    rfis = list(result.scalars().all())

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "RFI Number",
            "Subject",
            "Status",
            "Priority",
            "Question",
            "Answer",
            "Due Date",
            "Spec Section",
            "Drawing Reference",
            "Cost Impact",
            "Schedule Impact",
            "Cost Impact Amount",
            "Schedule Impact Days",
            "Created",
            "Date Answered",
            "Date Closed",
            "Days Open",
        ]
    )

    for rfi in rfis:
        days = _compute_days_open(rfi)
        writer.writerow(
            [
                rfi.rfi_number,
                rfi.subject,
                rfi.status,
                rfi.priority,
                rfi.question,
                rfi.answer or "",
                rfi.due_date.isoformat() if rfi.due_date else "",
                rfi.spec_section or "",
                rfi.drawing_reference or "",
                "Yes" if rfi.cost_impact else "No",
                "Yes" if rfi.schedule_impact else "No",
                str(rfi.cost_impact_amount) if rfi.cost_impact_amount else "",
                str(rfi.schedule_impact_days) if rfi.schedule_impact_days else "",
                rfi.created_at.isoformat() if rfi.created_at else "",
                rfi.date_answered.isoformat() if rfi.date_answered else "",
                rfi.date_closed.isoformat() if rfi.date_closed else "",
                str(days) if days is not None else "",
            ]
        )

    return output.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_rfi_or_raise(
    db: AsyncSession,
    rfi_id: uuid.UUID,
    project_id: uuid.UUID,
) -> RFI:
    """Fetch an RFI and verify project ownership."""
    from fastapi import HTTPException, status

    rfi = await db.get(RFI, rfi_id)
    if rfi is None or rfi.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RFI not found")
    return rfi


def _rfi_to_dict(rfi: RFI) -> dict:
    """Convert an RFI model to a serializable dict."""
    return {
        "id": rfi.id,
        "project_id": rfi.project_id,
        "rfi_number": rfi.rfi_number,
        "subject": rfi.subject,
        "question": rfi.question,
        "answer": rfi.answer,
        "status": rfi.status,
        "priority": rfi.priority,
        "submitted_by": rfi.submitted_by,
        "assigned_to": rfi.assigned_to,
        "ball_in_court": rfi.ball_in_court,
        "response": rfi.response,
        "ai_suggested_response": rfi.ai_suggested_response,
        "due_date": rfi.due_date,
        "spec_section": rfi.spec_section,
        "drawing_reference": rfi.drawing_reference,
        "cost_impact": rfi.cost_impact,
        "schedule_impact": rfi.schedule_impact,
        "cost_impact_amount": float(rfi.cost_impact_amount) if rfi.cost_impact_amount else None,
        "schedule_impact_days": rfi.schedule_impact_days,
        "distribution_list": rfi.distribution_list,
        "date_sent": rfi.date_sent,
        "date_answered": rfi.date_answered,
        "date_closed": rfi.date_closed,
        "responded_at": rfi.responded_at,
        "data_source": rfi.data_source,
        "created_at": rfi.created_at,
        "updated_at": rfi.updated_at,
    }
