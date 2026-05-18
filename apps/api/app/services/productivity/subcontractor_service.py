"""Subcontractor portal service: profile management, submissions, filtered SOV,
payment status tracking, and translated safety briefings.

Subcontractors see only the SOV line items assigned to them and can submit
manpower logs, delivery receipts, and pay applications scoped to their trade.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pay_application import PayApplication, ScheduleOfValues
from app.models.subcontractor import SubcontractorProfile, SubcontractorSubmission

logger = logging.getLogger(__name__)

# Valid submission types
SUBMISSION_TYPES = {"manpower", "delivery_receipt", "pay_application"}

# Valid submission statuses
SUBMISSION_STATUSES = {"pending", "reviewed", "approved", "rejected"}


@dataclass
class PaymentStatusEntry:
    """Payment status for a single period."""

    period_to: date
    submission_id: uuid.UUID
    submitted_amount: Decimal
    approved_amount: Decimal
    paid_amount: Decimal
    retainage_held: Decimal
    status: str


# ---------------------------------------------------------------------------
# Profile management
# ---------------------------------------------------------------------------


async def get_subcontractor_profile(
    db: AsyncSession,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
) -> SubcontractorProfile | None:
    """Fetch the subcontractor profile for a user on a project."""
    result = await db.execute(
        select(SubcontractorProfile).where(
            SubcontractorProfile.user_id == user_id,
            SubcontractorProfile.project_id == project_id,
        )
    )
    return result.scalar_one_or_none()


async def create_subcontractor_profile(
    db: AsyncSession,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    company_name: str,
    trade: str,
    sov_item_ids: list[str],
    contact_info: dict | None = None,
) -> SubcontractorProfile:
    """Create a subcontractor profile linking a user to a project with scope.

    Validates that sov_item_ids reference real SOV line items in the project.
    """
    # Validate SOV item IDs exist for this project
    if sov_item_ids:
        sov_uuids = [uuid.UUID(sid) for sid in sov_item_ids]
        result = await db.execute(
            select(func.count(ScheduleOfValues.id)).where(
                ScheduleOfValues.project_id == project_id,
                ScheduleOfValues.id.in_(sov_uuids),
            )
        )
        found_count = result.scalar_one()
        if found_count != len(sov_uuids):
            raise ValueError(
                f"Some SOV item IDs are invalid for this project: "
                f"expected {len(sov_uuids)}, found {found_count}"
            )

    profile = SubcontractorProfile(
        user_id=user_id,
        project_id=project_id,
        company_name=company_name,
        trade=trade,
        sov_item_ids=[str(sid) for sid in sov_item_ids],
        contact_info=contact_info or {},
    )
    db.add(profile)
    await db.flush()
    await db.refresh(profile)
    return profile


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------


async def submit_manpower(
    db: AsyncSession,
    profile_id: uuid.UUID,
    date_: date,
    workers_by_trade: dict[str, int],
    total_hours: float,
    notes: str | None = None,
) -> SubcontractorSubmission:
    """Submit a daily manpower report for a subcontractor.

    Validates that the profile exists and is active.
    """
    profile = await _validate_active_profile(db, profile_id)

    submission = SubcontractorSubmission(
        profile_id=profile.id,
        submission_type="manpower",
        submission_date=date_,
        data={
            "workers_by_trade": workers_by_trade,
            "total_hours": total_hours,
            "notes": notes,
            "total_workers": sum(workers_by_trade.values()),
        },
    )
    db.add(submission)
    await db.flush()
    await db.refresh(submission)
    logger.info(
        "Manpower submission created: profile=%s date=%s workers=%d",
        profile_id,
        date_,
        sum(workers_by_trade.values()),
    )
    return submission


async def upload_delivery_receipt(
    db: AsyncSession,
    profile_id: uuid.UUID,
    material_description: str,
    quantity: float,
    unit: str,
    supplier: str,
    delivery_date: date,
    document_url: str | None = None,
) -> SubcontractorSubmission:
    """Record a material delivery receipt for a subcontractor."""
    profile = await _validate_active_profile(db, profile_id)

    submission = SubcontractorSubmission(
        profile_id=profile.id,
        submission_type="delivery_receipt",
        submission_date=delivery_date,
        data={
            "material_description": material_description,
            "quantity": quantity,
            "unit": unit,
            "supplier": supplier,
        },
        document_url=document_url,
    )
    db.add(submission)
    await db.flush()
    await db.refresh(submission)
    logger.info(
        "Delivery receipt created: profile=%s material=%s qty=%s %s",
        profile_id,
        material_description,
        quantity,
        unit,
    )
    return submission


# ---------------------------------------------------------------------------
# Filtered SOV — scope isolation
# ---------------------------------------------------------------------------


async def get_filtered_sov(
    db: AsyncSession,
    project_id: uuid.UUID,
    profile_id: uuid.UUID,
) -> list[dict]:
    """Return only the SOV line items that this subcontractor is responsible for.

    This is the critical scope-isolation function: subcontractors must only
    see their own work items.
    """
    profile = await _validate_active_profile(db, profile_id)

    if not profile.sov_item_ids:
        return []

    sov_uuids = [uuid.UUID(sid) for sid in profile.sov_item_ids]
    result = await db.execute(
        select(ScheduleOfValues)
        .where(
            ScheduleOfValues.project_id == project_id,
            ScheduleOfValues.id.in_(sov_uuids),
        )
        .order_by(ScheduleOfValues.sort_order, ScheduleOfValues.item_number)
    )
    sov_items = list(result.scalars().all())

    return [
        {
            "id": str(item.id),
            "item_number": item.item_number,
            "description": item.description,
            "scheduled_value": str(item.scheduled_value),
            "csi_code": item.csi_code,
            "sort_order": item.sort_order,
        }
        for item in sov_items
    ]


# ---------------------------------------------------------------------------
# Sub pay application
# ---------------------------------------------------------------------------


async def submit_sub_pay_application(
    db: AsyncSession,
    profile_id: uuid.UUID,
    line_items: list[dict],
    period_to: date,
    notes: str | None = None,
) -> SubcontractorSubmission:
    """Submit a subcontractor pay application scoped to their SOV items.

    Validates that every line_item.item_id is within the subcontractor's
    assigned sov_item_ids. This prevents a sub from billing for work
    outside their scope.

    Each line_item dict must contain:
        - item_id: str (SOV line item UUID)
        - work_completed_this_period: Decimal
        - materials_presently_stored: Decimal (optional, default 0)
    """
    profile = await _validate_active_profile(db, profile_id)

    allowed_ids = set(profile.sov_item_ids or [])
    if not allowed_ids:
        raise ValueError("Subcontractor has no SOV items assigned")

    total_billed = Decimal("0")
    validated_items = []

    for li in line_items:
        item_id = str(li.get("item_id", ""))
        if item_id not in allowed_ids:
            raise ValueError(
                f"SOV item {item_id} is not in subcontractor's scope. "
                f"Allowed items: {sorted(allowed_ids)}"
            )
        work_this_period = Decimal(str(li.get("work_completed_this_period", "0")))
        materials_stored = Decimal(str(li.get("materials_presently_stored", "0")))

        if work_this_period < 0:
            raise ValueError(f"work_completed_this_period cannot be negative for item {item_id}")
        if materials_stored < 0:
            raise ValueError(f"materials_presently_stored cannot be negative for item {item_id}")

        total_billed += work_this_period + materials_stored
        validated_items.append(
            {
                "item_id": item_id,
                "work_completed_this_period": str(work_this_period),
                "materials_presently_stored": str(materials_stored),
            }
        )

    submission = SubcontractorSubmission(
        profile_id=profile.id,
        submission_type="pay_application",
        submission_date=period_to,
        data={
            "line_items": validated_items,
            "total_billed": str(total_billed),
            "notes": notes,
        },
    )
    db.add(submission)
    await db.flush()
    await db.refresh(submission)
    logger.info(
        "Sub pay application created: profile=%s period=%s total=$%s",
        profile_id,
        period_to,
        total_billed,
    )
    return submission


# ---------------------------------------------------------------------------
# Payment status
# ---------------------------------------------------------------------------


async def get_payment_status(
    db: AsyncSession,
    profile_id: uuid.UUID,
) -> list[PaymentStatusEntry]:
    """Query pay application submissions and derive payment status per period.

    Cross-references with parent PayApplication records to determine
    approved and paid amounts.
    """
    profile = await _validate_active_profile(db, profile_id)

    result = await db.execute(
        select(SubcontractorSubmission)
        .where(
            SubcontractorSubmission.profile_id == profile.id,
            SubcontractorSubmission.submission_type == "pay_application",
        )
        .order_by(SubcontractorSubmission.submission_date.desc())
    )
    submissions = list(result.scalars().all())

    if not submissions:
        return []

    entries: list[PaymentStatusEntry] = []

    for sub in submissions:
        submitted_amount = Decimal(sub.data.get("total_billed", "0"))
        approved_amount = Decimal("0")
        paid_amount = Decimal("0")
        retainage_held = Decimal("0")

        if sub.status == "approved":
            approved_amount = submitted_amount
            # Check if parent pay app for this period is paid
            parent_app = await _find_parent_pay_app(db, profile.project_id, sub.submission_date)
            if parent_app and parent_app.status == "paid":
                retainage_pct = parent_app.retainage_pct or Decimal("10")
                retainage_held = approved_amount * retainage_pct / Decimal("100")
                paid_amount = approved_amount - retainage_held
            elif parent_app and parent_app.status in ("certified", "paid"):
                retainage_pct = parent_app.retainage_pct or Decimal("10")
                retainage_held = approved_amount * retainage_pct / Decimal("100")

        entries.append(
            PaymentStatusEntry(
                period_to=sub.submission_date,
                submission_id=sub.id,
                submitted_amount=submitted_amount,
                approved_amount=approved_amount,
                paid_amount=paid_amount,
                retainage_held=retainage_held,
                status=sub.status,
            )
        )

    return entries


# ---------------------------------------------------------------------------
# List & review submissions
# ---------------------------------------------------------------------------


async def list_submissions(
    db: AsyncSession,
    profile_id: uuid.UUID,
    submission_type: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[SubcontractorSubmission], int]:
    """List submissions for a subcontractor profile with optional type filter.

    Returns (submissions, total_count).
    """
    base_query = select(SubcontractorSubmission).where(
        SubcontractorSubmission.profile_id == profile_id,
    )
    count_query = select(func.count(SubcontractorSubmission.id)).where(
        SubcontractorSubmission.profile_id == profile_id,
    )

    if submission_type:
        if submission_type not in SUBMISSION_TYPES:
            raise ValueError(
                f"Invalid submission_type '{submission_type}'. "
                f"Must be one of: {sorted(SUBMISSION_TYPES)}"
            )
        base_query = base_query.where(SubcontractorSubmission.submission_type == submission_type)
        count_query = count_query.where(SubcontractorSubmission.submission_type == submission_type)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    result = await db.execute(
        base_query.order_by(SubcontractorSubmission.created_at.desc()).offset(skip).limit(limit)
    )
    submissions = list(result.scalars().all())

    return submissions, total


async def review_submission(
    db: AsyncSession,
    submission_id: uuid.UUID,
    reviewed_by: uuid.UUID,
    status: str,
    notes: str | None = None,
) -> SubcontractorSubmission:
    """Review a submission (approve, reject, or mark reviewed).

    Only pending or reviewed submissions can be transitioned.
    """
    if status not in SUBMISSION_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Must be one of: {sorted(SUBMISSION_STATUSES)}"
        )

    submission = await db.get(SubcontractorSubmission, submission_id)
    if submission is None:
        raise ValueError("Submission not found")

    if submission.status not in ("pending", "reviewed"):
        raise ValueError(
            f"Cannot review a submission with status '{submission.status}'. "
            f"Only 'pending' or 'reviewed' submissions can be reviewed."
        )

    submission.status = status
    submission.reviewed_by = reviewed_by
    submission.review_notes = notes
    await db.flush()
    await db.refresh(submission)
    logger.info(
        "Submission %s reviewed: status=%s by=%s",
        submission_id,
        status,
        reviewed_by,
    )
    return submission


# ---------------------------------------------------------------------------
# Translated safety briefings
# ---------------------------------------------------------------------------


async def get_translated_safety_briefing(
    briefing_text: str,
    target_language: str,
) -> str:
    """Translate a safety briefing to the target language.

    Uses the translation service with safety_alert context for urgency
    and clarity in safety-critical communication.
    """
    from app.services.communication.translation_service import get_translation_service

    service = get_translation_service()
    result = await service.translate(
        text=briefing_text,
        target_lang=target_language,
        context="safety_alert",
    )
    return result.translated_text


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _validate_active_profile(
    db: AsyncSession,
    profile_id: uuid.UUID,
) -> SubcontractorProfile:
    """Load profile and verify it exists and is active."""
    profile = await db.get(SubcontractorProfile, profile_id)
    if profile is None:
        raise ValueError("Subcontractor profile not found")
    if profile.status != "active":
        raise ValueError(
            f"Subcontractor profile is '{profile.status}', not 'active'. "
            f"Only active profiles can submit data."
        )
    return profile


async def _find_parent_pay_app(
    db: AsyncSession,
    project_id: uuid.UUID,
    period_to: date,
) -> PayApplication | None:
    """Find the parent pay application closest to the given period."""
    result = await db.execute(
        select(PayApplication)
        .where(
            PayApplication.project_id == project_id,
            PayApplication.period_to == period_to,
        )
        .order_by(PayApplication.application_number.desc())
        .limit(1)
    )
    return result.scalars().first()
