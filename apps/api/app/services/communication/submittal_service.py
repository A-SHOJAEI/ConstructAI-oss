"""Submittal workflow service — business logic, validation, and event publishing."""

from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.communication import Submittal, SubmittalAttachment, SubmittalReview

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[str, set[str]] = {
    "not_submitted": {"pending_review"},
    "pending_review": {"approved", "approved_as_noted", "revise_and_resubmit", "rejected"},
    "approved": {"closed"},
    "approved_as_noted": {"closed"},
    "revise_and_resubmit": {"pending_review"},
    "rejected": {"pending_review", "closed"},
    "closed": set(),
}

LEGACY_STATUS_MAP: dict[str, str] = {"pending": "pending_review"}

VALID_STATUSES = set(VALID_TRANSITIONS.keys())

VALID_PRIORITIES = {"urgent", "high", "normal", "low"}

VALID_TYPES = {
    "shop_drawing",
    "product_data",
    "sample",
    "mock_up",
    "test_report",
    "certificate",
    "other",
}

REVIEW_ACTION_TO_STATUS: dict[str, str] = {
    "approved": "approved",
    "approved_as_noted": "approved_as_noted",
    "revise_and_resubmit": "revise_and_resubmit",
    "rejected": "rejected",
    "no_exception_taken": "approved",
}

VALID_REVIEW_ACTIONS = set(REVIEW_ACTION_TO_STATUS.keys())

# Fields that Procore owns — cannot be updated on procore-sourced submittals
_PROCORE_OWNED_FIELDS = {"title", "submittal_number", "spec_section"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_status(status: str) -> str:
    """Map legacy status values to current ones."""
    return LEGACY_STATUS_MAP.get(status, status)


def check_overdue(submittal: Submittal) -> bool:
    """Return True if *submittal* is overdue based on date_required."""
    effective_status = _normalize_status(submittal.status)
    if effective_status not in ("pending_review",):
        return False
    if submittal.date_required is None:
        return False
    return date.today() > submittal.date_required


def _compute_days_open(submittal: Submittal) -> int | None:
    """Number of calendar days since the submittal was created."""
    if submittal.created_at is None:
        return None
    end = submittal.date_returned.date() if submittal.date_returned else date.today()
    return (end - submittal.created_at.date()).days


def _find_next_reviewer_in_chain(
    chain: list[dict], current_reviewer_id: uuid.UUID
) -> uuid.UUID | None:
    """Walk the review chain to find the next reviewer after *current_reviewer_id*."""
    found_current = False
    for step in chain:
        user_id_str = step.get("user_id")
        if not user_id_str:
            continue
        if str(current_reviewer_id) == str(user_id_str):
            found_current = True
            continue
        if found_current:
            return uuid.UUID(user_id_str)
    return None


async def _publish_submittal_event(
    event_type: str,
    submittal: Submittal,
    extra_data: dict | None = None,
) -> None:
    """Best-effort Kafka event for submittal lifecycle changes."""
    try:
        from app.services.messaging.kafka_producer import KafkaEventProducer

        producer = KafkaEventProducer()
        data: dict[str, Any] = {
            "submittal_id": str(submittal.id),
            "project_id": str(submittal.project_id),
            "submittal_number": submittal.submittal_number,
            "status": submittal.status,
            "title": submittal.title,
        }
        if extra_data:
            data.update(extra_data)
        await producer.publish(
            event_type=f"constructai.communication.submittal.{event_type}",
            data=data,
            source="/submittal-service",
        )
    except Exception:
        logger.warning("Failed to publish submittal event %s", event_type, exc_info=True)


# ---------------------------------------------------------------------------
# Submittal number generation
# ---------------------------------------------------------------------------


async def generate_submittal_number(db: AsyncSession, project_id: uuid.UUID) -> str:
    """Auto-generate the next ``SUB-NNN`` number for *project_id*.

    Uses a PostgreSQL advisory lock scoped to the project to prevent
    duplicate numbers under concurrent requests.
    """
    from sqlalchemy import text

    # Advisory lock scoped to the project to prevent duplicate numbers
    lock_key = hash(str(project_id)) & 0x7FFFFFFF  # PostgreSQL advisory lock needs int
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

    # Now safe to read max without FOR UPDATE (which is invalid with aggregates)
    sql = text("""
        SELECT MAX(
            CAST(SUBSTRING(submittal_number FROM 'SUB-(\\d+)') AS INTEGER)
        )
        FROM submittals
        WHERE project_id = :pid
          AND submittal_number ~ '^SUB-\\d+$'
    """)
    result = await db.execute(sql, {"pid": project_id})
    max_num = result.scalar()

    if max_num is None:
        count_q = (
            select(func.count()).select_from(Submittal).where(Submittal.project_id == project_id)
        )
        count_result = await db.execute(count_q)
        max_num = count_result.scalar() or 0

    next_num = (max_num or 0) + 1
    return f"SUB-{next_num:03d}"


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


async def create_submittal(
    db: AsyncSession,
    project_id: uuid.UUID,
    data: dict[str, Any],
    submitted_by: uuid.UUID,
) -> Submittal:
    """Create a new submittal with auto-generated number."""
    submittal_number = await generate_submittal_number(db, project_id)

    submittal_type = data.get("submittal_type", "shop_drawing")
    if submittal_type not in VALID_TYPES:
        raise ValueError(f"Invalid submittal type '{submittal_type}'")

    priority = data.get("priority", "normal")
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"Invalid priority '{priority}'")

    review_chain = data.get("review_chain", [])

    submittal = Submittal(
        project_id=project_id,
        submittal_number=submittal_number,
        title=data["title"],
        description=data.get("description"),
        spec_section=data.get("spec_section"),
        spec_section_name=data.get("spec_section_name"),
        submittal_type=submittal_type,
        status="not_submitted",
        priority=priority,
        revision_number=0,
        submitted_by=submitted_by,
        ball_in_court=submitted_by,
        due_date=data.get("due_date"),
        date_required=data.get("date_required"),
        lead_time_days=data.get("lead_time_days"),
        review_chain=review_chain,
        distribution_list=data.get("distribution_list", []),
        linked_rfi_ids=data.get("linked_rfi_ids", []),
    )

    db.add(submittal)
    await db.flush()
    await db.refresh(submittal)

    await _publish_submittal_event("created", submittal)
    return submittal


async def update_submittal(
    db: AsyncSession,
    submittal_id: uuid.UUID,
    project_id: uuid.UUID,
    data: dict[str, Any],
    current_user_id: uuid.UUID,
) -> Submittal:
    """Update a submittal with status transition validation."""
    submittal = await _get_submittal_or_raise(db, submittal_id, project_id)

    # Status transition validation
    new_status = data.get("status")
    if new_status and new_status != _normalize_status(submittal.status):
        current = _normalize_status(submittal.status)
        allowed = VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise ValueError(
                f"Cannot transition from '{current}' to '{new_status}'. "
                f"Allowed: {allowed or 'none (terminal state)'}"
            )
        submittal.status = new_status

        if new_status == "pending_review" and submittal.date_submitted is None:
            submittal.date_submitted = datetime.now(UTC)
            submittal.submitted_at = datetime.now(UTC)
            # Set first reviewer from chain
            if submittal.review_chain:
                first_step = submittal.review_chain[0]
                first_user = first_step.get("user_id")
                if first_user:
                    submittal.current_reviewer = uuid.UUID(first_user)
                    submittal.ball_in_court = uuid.UUID(first_user)

    # Protect Procore-owned fields
    if submittal.data_source == "procore":
        for field in _PROCORE_OWNED_FIELDS:
            if field in data and data[field] is not None:
                data.pop(field)

    # Apply field updates
    updatable = {
        "title",
        "description",
        "spec_section",
        "spec_section_name",
        "submittal_type",
        "priority",
        "ball_in_court",
        "due_date",
        "date_required",
        "lead_time_days",
        "review_chain",
        "distribution_list",
        "linked_rfi_ids",
    }
    for field in updatable:
        if field in data and data[field] is not None:
            setattr(submittal, field, data[field])

    submittal.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(submittal)

    await _publish_submittal_event("updated", submittal)
    return submittal


async def review_submittal(
    db: AsyncSession,
    submittal_id: uuid.UUID,
    project_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    action: str,
    comments: str | None = None,
) -> SubmittalReview:
    """Submit a review action and advance the approval chain."""
    submittal = await _get_submittal_or_raise(db, submittal_id, project_id)

    effective_status = _normalize_status(submittal.status)
    if effective_status != "pending_review":
        raise ValueError(f"Cannot review a submittal in '{effective_status}' status")

    if action not in VALID_REVIEW_ACTIONS:
        raise ValueError(f"Invalid review action '{action}'. Valid actions: {VALID_REVIEW_ACTIONS}")

    # Create the review record
    review = SubmittalReview(
        submittal_id=submittal_id,
        reviewer_id=reviewer_id,
        review_action=action,
        comments=comments,
        revision_number=submittal.revision_number,
    )
    db.add(review)

    target_status = REVIEW_ACTION_TO_STATUS[action]

    # On rejection/revise_and_resubmit, bypass chain and set status immediately
    if target_status in ("rejected", "revise_and_resubmit"):
        submittal.status = target_status
        submittal.date_returned = datetime.now(UTC)
        submittal.reviewed_at = datetime.now(UTC)
        submittal.current_reviewer = None
        if submittal.submitted_by:
            submittal.ball_in_court = submittal.submitted_by
    else:
        # Advance through chain
        chain: list[dict[str, Any]] = (
            submittal.review_chain if isinstance(submittal.review_chain, list) else []
        )
        next_reviewer = _find_next_reviewer_in_chain(chain, reviewer_id) if chain else None
        if next_reviewer:
            # More reviewers in chain — stay pending_review
            submittal.current_reviewer = next_reviewer
            submittal.ball_in_court = next_reviewer
        else:
            # Chain exhausted — apply final status
            submittal.status = target_status
            submittal.date_returned = datetime.now(UTC)
            submittal.reviewed_at = datetime.now(UTC)
            submittal.current_reviewer = None
            if submittal.submitted_by:
                submittal.ball_in_court = submittal.submitted_by

    submittal.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(review)

    # IG-10: When a submittal is approved, note the cleared constraint on
    # any schedule activities that reference this submittal's spec_section.
    # This is informational only — it does NOT modify activity dates.
    if target_status in ("approved", "approved_as_noted") and submittal.spec_section:
        try:
            from app.models.scheduling import ScheduleActivity

            _spec = submittal.spec_section
            act_result = await db.execute(
                select(ScheduleActivity).where(
                    ScheduleActivity.project_id == submittal.project_id,
                    ScheduleActivity.status.in_(["not_started", "in_progress"]),
                )
            )
            _activities = list(act_result.scalars().all())
            _cleared_count = 0
            for _act in _activities:
                # Check activity metadata, wbs_code, or name for spec section reference
                _meta = dict(_act.metadata_ or {})
                _act_spec = _meta.get("spec_section", "")
                _name_lower = (_act.name or "").lower()
                _spec_lower = _spec.lower().replace(" ", "")

                if (
                    _act_spec == _spec
                    or _spec_lower in _name_lower.replace(" ", "")
                    or _spec in (_act.wbs_code or "")
                ):
                    _meta["submittal_constraint_cleared"] = True
                    _meta["submittal_cleared_spec_section"] = _spec
                    _meta["submittal_cleared_at"] = datetime.now(UTC).isoformat()
                    _meta["submittal_cleared_id"] = str(submittal.id)
                    _act.metadata_ = _meta
                    _cleared_count += 1

            if _cleared_count > 0:
                await db.flush()
                logger.info(
                    "Submittal %s (spec %s) approved — cleared constraint on %d schedule activities",
                    submittal.submittal_number,
                    _spec,
                    _cleared_count,
                )
            else:
                logger.debug(
                    "Submittal %s (spec %s) approved — no matching schedule activities found",
                    submittal.submittal_number,
                    _spec,
                )
        except Exception:
            logger.warning(
                "Failed to update schedule activities after submittal %s approval",
                submittal.submittal_number,
                exc_info=True,
            )

    await _publish_submittal_event(
        "reviewed",
        submittal,
        {
            "review_id": str(review.id),
            "reviewer_id": str(reviewer_id),
            "action": action,
        },
    )
    return review


async def resubmit_submittal(
    db: AsyncSession,
    submittal_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    notes: str | None = None,
) -> Submittal:
    """Resubmit a submittal with a new revision."""
    submittal = await _get_submittal_or_raise(db, submittal_id, project_id)

    effective_status = _normalize_status(submittal.status)
    allowed = VALID_TRANSITIONS.get(effective_status, set())
    if "pending_review" not in allowed:
        raise ValueError(
            f"Cannot resubmit from '{effective_status}' status. "
            f"Allowed transitions: {allowed or 'none'}"
        )

    submittal.revision_number += 1
    submittal.status = "pending_review"
    submittal.date_submitted = datetime.now(UTC)
    submittal.date_returned = None
    submittal.reviewed_at = None

    # Reset to first reviewer in chain
    chain = submittal.review_chain or []
    if chain:
        first_step = chain[0]
        first_user = first_step.get("user_id")
        if first_user:
            submittal.current_reviewer = uuid.UUID(first_user)
            submittal.ball_in_court = uuid.UUID(first_user)
    else:
        submittal.current_reviewer = None
        if submittal.submitted_by:
            submittal.ball_in_court = submittal.submitted_by

    submittal.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(submittal)

    await _publish_submittal_event(
        "resubmitted",
        submittal,
        {
            "revision_number": submittal.revision_number,
            "notes": notes,
        },
    )
    return submittal


async def close_submittal(
    db: AsyncSession,
    submittal_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Submittal:
    """Close an approved/rejected submittal."""
    submittal = await _get_submittal_or_raise(db, submittal_id, project_id)

    effective_status = _normalize_status(submittal.status)
    allowed = VALID_TRANSITIONS.get(effective_status, set())
    if "closed" not in allowed:
        raise ValueError(
            f"Cannot close submittal in '{effective_status}' status. "
            f"Allowed transitions: {allowed or 'none'}"
        )

    submittal.status = "closed"
    submittal.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(submittal)

    await _publish_submittal_event("closed", submittal)
    return submittal


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


async def get_submittal_detail(
    db: AsyncSession,
    submittal_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict:
    """Fetch a submittal with reviews, attachments, and computed fields."""
    submittal = await _get_submittal_or_raise(db, submittal_id, project_id)

    # Reviews
    rev_q = (
        select(SubmittalReview)
        .where(SubmittalReview.submittal_id == submittal_id)
        .order_by(SubmittalReview.created_at.asc())
    )
    rev_result = await db.execute(rev_q)
    reviews = list(rev_result.scalars().all())

    # Attachments
    att_q = (
        select(SubmittalAttachment)
        .where(SubmittalAttachment.submittal_id == submittal_id)
        .order_by(SubmittalAttachment.uploaded_at.asc())
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
                "submittal_id": att.submittal_id,
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
        **_submittal_to_dict(submittal),
        "is_overdue": check_overdue(submittal),
        "days_open": _compute_days_open(submittal),
        "reviews": [
            {
                "id": r.id,
                "submittal_id": r.submittal_id,
                "reviewer_id": r.reviewer_id,
                "review_action": r.review_action,
                "comments": r.comments,
                "revision_number": r.revision_number,
                "reviewed_at": r.reviewed_at,
                "created_at": r.created_at,
            }
            for r in reviews
        ],
        "attachments": attachment_items,
    }


async def list_submittals(
    db: AsyncSession,
    project_id: uuid.UUID,
    status_filter: str | None = None,
    priority_filter: str | None = None,
    type_filter: str | None = None,
    spec_section_filter: str | None = None,
    ball_in_court_filter: uuid.UUID | None = None,
    overdue_only: bool = False,
    search: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    """List submittals for a project with filtering and cursor pagination."""
    query = select(Submittal).where(Submittal.project_id == project_id)

    if status_filter:
        query = query.where(Submittal.status == status_filter)
    if priority_filter:
        query = query.where(Submittal.priority == priority_filter)
    if type_filter:
        query = query.where(Submittal.submittal_type == type_filter)
    if spec_section_filter:
        query = query.where(Submittal.spec_section == spec_section_filter)
    if ball_in_court_filter:
        query = query.where(Submittal.ball_in_court == ball_in_court_filter)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.where(
            Submittal.title.ilike(pattern)
            | Submittal.submittal_number.ilike(pattern)
            | Submittal.spec_section.ilike(pattern)
        )

    # Apply overdue filter in SQL to ensure correct pagination
    if overdue_only:
        query = query.where(
            Submittal.status == "pending_review",
            Submittal.date_required.isnot(None),
            Submittal.date_required < date.today(),
        )

    query = query.order_by(Submittal.created_at.desc())

    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise ValueError("Invalid cursor format: must be a valid UUID")
        cursor_obj = await db.get(Submittal, cursor_uuid)
        if cursor_obj:
            query = query.where(Submittal.created_at < cursor_obj.created_at)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    items = list(result.scalars().all())

    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    data = []
    for s in items:
        d = _submittal_to_dict(s)
        d["is_overdue"] = check_overdue(s)
        d["days_open"] = _compute_days_open(s)
        d["reviews"] = []
        d["attachments"] = []
        data.append(d)

    next_cursor = str(items[-1].id) if has_more and items else None
    return {
        "data": data,
        "meta": {"cursor": next_cursor, "has_more": has_more},
    }


# ---------------------------------------------------------------------------
# Statistics, register, and export
# ---------------------------------------------------------------------------


async def get_submittal_stats(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Compute aggregate submittal statistics for a project."""
    q = (
        select(Submittal.status, func.count())
        .where(Submittal.project_id == project_id)
        .group_by(Submittal.status)
    )
    result = await db.execute(q)
    status_counts: dict[str, int] = {}
    total = 0
    for row in result.all():
        normalized = _normalize_status(row[0])
        status_counts[normalized] = status_counts.get(normalized, 0) + row[1]
        total += row[1]

    # Average days to return
    avg_q = select(
        func.avg(func.extract("epoch", Submittal.date_returned - Submittal.date_submitted) / 86400)
    ).where(
        Submittal.project_id == project_id,
        Submittal.date_returned.isnot(None),
        Submittal.date_submitted.isnot(None),
    )
    avg_result = await db.execute(avg_q)
    avg_days = avg_result.scalar()

    # Overdue count
    open_q = select(Submittal).where(
        Submittal.project_id == project_id,
        Submittal.status.in_(["pending_review", "pending"]),
    )
    open_result = await db.execute(open_q)
    open_submittals = list(open_result.scalars().all())
    overdue_count = sum(1 for s in open_submittals if check_overdue(s))

    return {
        "total": total,
        "not_submitted": status_counts.get("not_submitted", 0),
        "pending_review": status_counts.get("pending_review", 0),
        "approved": status_counts.get("approved", 0),
        "approved_as_noted": status_counts.get("approved_as_noted", 0),
        "revise_and_resubmit": status_counts.get("revise_and_resubmit", 0),
        "rejected": status_counts.get("rejected", 0),
        "closed": status_counts.get("closed", 0),
        "overdue": overdue_count,
        "avg_review_days": round(avg_days, 1) if avg_days else None,
    }


async def get_submittal_register(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> list[dict]:
    """Generate the submittal register — spec_section × status matrix."""
    q = (
        select(
            Submittal.spec_section,
            Submittal.status,
            func.count().label("cnt"),
        )
        .where(
            Submittal.project_id == project_id,
            Submittal.spec_section.isnot(None),
        )
        .group_by(Submittal.spec_section, Submittal.status)
    )
    result = await db.execute(q)

    # Also fetch spec_section_name for each section
    name_q = (
        select(Submittal.spec_section, Submittal.spec_section_name)
        .where(
            Submittal.project_id == project_id,
            Submittal.spec_section.isnot(None),
        )
        .distinct(Submittal.spec_section)
    )
    name_result = await db.execute(name_q)
    section_names: dict[str, str | None] = {}
    for name_row in name_result.all():
        section_names[name_row[0]] = name_row[1]

    register: dict[str, dict[str, int]] = {}
    for row in result.all():
        section = row[0]
        status = _normalize_status(row[1])
        count = row[2]
        if section not in register:
            register[section] = {}
        register[section][status] = register[section].get(status, 0) + count

    entries = []
    for section in sorted(register.keys()):
        counts = register[section]
        total = sum(counts.values())
        entries.append(
            {
                "spec_section": section,
                "spec_section_name": section_names.get(section),
                "total": total,
                "not_submitted": counts.get("not_submitted", 0),
                "pending_review": counts.get("pending_review", 0),
                "approved": counts.get("approved", 0),
                "approved_as_noted": counts.get("approved_as_noted", 0),
                "revise_and_resubmit": counts.get("revise_and_resubmit", 0),
                "rejected": counts.get("rejected", 0),
                "closed": counts.get("closed", 0),
            }
        )

    return entries


async def export_submittals_csv(
    db: AsyncSession,
    project_id: uuid.UUID,
    limit: int = 5000,
) -> bytes:
    """Generate a CSV export of all submittals for a project."""
    q = (
        select(Submittal)
        .where(Submittal.project_id == project_id)
        .order_by(Submittal.submittal_number)
        .limit(limit)
    )
    result = await db.execute(q)
    submittals = list(result.scalars().all())

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Submittal Number",
            "Title",
            "Type",
            "Status",
            "Priority",
            "Spec Section",
            "Spec Section Name",
            "Revision",
            "Date Required",
            "Due Date",
            "Lead Time Days",
            "Ball In Court",
            "Created",
            "Date Submitted",
            "Date Returned",
            "Days Open",
        ]
    )

    for s in submittals:
        days = _compute_days_open(s)
        writer.writerow(
            [
                s.submittal_number,
                s.title,
                s.submittal_type,
                s.status,
                s.priority,
                s.spec_section or "",
                s.spec_section_name or "",
                s.revision_number,
                s.date_required.isoformat() if s.date_required else "",
                s.due_date.isoformat() if s.due_date else "",
                str(s.lead_time_days) if s.lead_time_days else "",
                str(s.ball_in_court) if s.ball_in_court else "",
                s.created_at.isoformat() if s.created_at else "",
                s.date_submitted.isoformat() if s.date_submitted else "",
                s.date_returned.isoformat() if s.date_returned else "",
                str(days) if days is not None else "",
            ]
        )

    return output.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_submittal_or_raise(
    db: AsyncSession,
    submittal_id: uuid.UUID,
    project_id: uuid.UUID,
) -> Submittal:
    """Fetch a submittal and verify project ownership."""
    from fastapi import HTTPException, status

    submittal = await db.get(Submittal, submittal_id)
    if submittal is None or submittal.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submittal not found")
    return submittal


def _submittal_to_dict(submittal: Submittal) -> dict:
    """Convert a Submittal model to a serializable dict."""
    return {
        "id": submittal.id,
        "project_id": submittal.project_id,
        "submittal_number": submittal.submittal_number,
        "title": submittal.title,
        "description": submittal.description,
        "spec_section": submittal.spec_section,
        "spec_section_name": submittal.spec_section_name,
        "submittal_type": submittal.submittal_type,
        "status": submittal.status,
        "priority": submittal.priority,
        "revision_number": submittal.revision_number,
        "submitted_by": submittal.submitted_by,
        "reviewer_id": submittal.reviewer_id,
        "current_reviewer": submittal.current_reviewer,
        "ball_in_court": submittal.ball_in_court,
        "document_urls": submittal.document_urls,
        "review_comments": submittal.review_comments,
        "due_date": submittal.due_date,
        "date_required": submittal.date_required,
        "date_submitted": submittal.date_submitted,
        "date_returned": submittal.date_returned,
        "submitted_at": submittal.submitted_at,
        "reviewed_at": submittal.reviewed_at,
        "lead_time_days": submittal.lead_time_days,
        "distribution_list": submittal.distribution_list,
        "linked_rfi_ids": submittal.linked_rfi_ids,
        "review_chain": submittal.review_chain,
        "data_source": submittal.data_source,
        "created_at": submittal.created_at,
        "updated_at": submittal.updated_at,
    }
