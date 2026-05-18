"""Instant Pay service: automated pay app generation and payment orchestration.

Bridges vision-based progress tracking, schedule of values, pay application
generation, payment submission, webhook processing, and lien waiver management
into a single payment pipeline.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
HUNDRED = Decimal("100")
TWO_PLACES = Decimal("0.01")


def _round2(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Pure functions (no DB)
# ---------------------------------------------------------------------------


def compute_retainage(
    amount: Decimal,
    retainage_pct: Decimal,
    is_substantial_completion: bool = False,
) -> dict[str, Decimal]:
    """Compute retainage and net payment amounts.

    At substantial completion, retainage is typically released (reduced to 0),
    so retainage_amount becomes 0 and net_amount equals the full amount.

    Args:
        amount: Gross billing amount.
        retainage_pct: Retainage percentage (0-100).
        is_substantial_completion: If True, retainage is released.

    Returns:
        Dict with retainage_amount and net_amount.
    """
    if amount < ZERO:
        raise ValueError("amount must be non-negative")
    if retainage_pct < ZERO or retainage_pct > HUNDRED:
        raise ValueError("retainage_pct must be between 0 and 100")

    if is_substantial_completion:
        return {
            "retainage_amount": ZERO,
            "net_amount": _round2(amount),
        }

    retainage_rate = retainage_pct / HUNDRED
    retainage_amount = _round2(amount * retainage_rate)
    net_amount = _round2(amount - retainage_amount)

    return {
        "retainage_amount": retainage_amount,
        "net_amount": net_amount,
    }


def _map_progress_to_sov(
    activities_progress: dict[str, Any],
    sov_items: list[dict],
    schedule_activities: list[dict] | None = None,
) -> list[dict]:
    """Map progress snapshot data to SOV line items for billing.

    Pure function — no database access.

    Matching strategy (in priority order):
    1. activity.wbs_code prefix matches sov.item_number
    2. CSI code matching (activity CSI prefix matches sov.csi_code prefix)
    3. Keyword matching on description

    Args:
        activities_progress: Dict of {activity_id: percent_complete} from snapshot.
        sov_items: List of SOV item dicts with keys: id, item_number, description,
            scheduled_value, csi_code.
        schedule_activities: Optional list of activity dicts with keys: id, name,
            wbs_code, activity_code, pct_complete.

    Returns:
        List of dicts suitable for pay application line item input, each with:
        sov_id, item_number, description_of_work, scheduled_value,
        work_completed_this_period, materials_presently_stored.
    """
    if not activities_progress or not sov_items:
        return []

    # Build activity lookup
    act_lookup: dict[str, dict] = {}
    if schedule_activities:
        for act in schedule_activities:
            act_id = str(act.get("id", ""))
            act_lookup[act_id] = act

    # Build sets for matching
    result: list[dict] = []
    matched_sov_ids: set[str] = set()

    for sov in sov_items:
        sov_id = str(sov.get("id", ""))
        item_number = sov.get("item_number", "")
        sov_desc = (sov.get("description", "") or "").lower()
        sov_csi = (sov.get("csi_code", "") or "").strip()
        scheduled_value = Decimal(str(sov.get("scheduled_value", 0)))

        if scheduled_value <= ZERO:
            continue

        best_match_pct: Decimal | None = None
        best_match_id: str | None = None

        for act_id, progress_val in activities_progress.items():
            progress_pct = Decimal(str(progress_val))
            act_data = act_lookup.get(act_id, {})
            act_wbs = (act_data.get("wbs_code") or "").strip()
            act_code = (act_data.get("activity_code") or "").strip()
            act_name = (act_data.get("name") or "").lower()

            matched = False

            # Strategy 1: WBS code prefix match
            if act_wbs and item_number:
                # WBS "03.01" should match SOV item_number "03.01" or "03.01.01"
                if item_number.startswith(act_wbs) or act_wbs.startswith(item_number):
                    matched = True

            # Strategy 2: CSI code match
            if not matched and sov_csi and act_code:
                sov_csi_prefix = sov_csi.replace(" ", "")[:4]
                act_csi_prefix = act_code.replace(" ", "")[:4]
                if sov_csi_prefix and act_csi_prefix and sov_csi_prefix == act_csi_prefix:
                    matched = True

            # Strategy 3: Keyword match on description
            if not matched and act_name and sov_desc:
                # Split activity name into words, check if 2+ match SOV description
                act_words = set(act_name.split())
                # Filter out very short/common words
                act_words = {w for w in act_words if len(w) > 3}
                if act_words:
                    matched_words = sum(1 for w in act_words if w in sov_desc)
                    if matched_words >= 2 or (matched_words == 1 and len(act_words) <= 2):
                        matched = True

            if matched and (best_match_pct is None or progress_pct > best_match_pct):
                best_match_pct = progress_pct
                best_match_id = act_id

        if best_match_pct is not None:
            # Calculate work_completed_this_period as the incremental amount
            # based on percent complete applied to scheduled value
            completed_amount = _round2(scheduled_value * best_match_pct / HUNDRED)
            # The actual billing for this period is the completed amount
            # (previous period billing will be subtracted by create_pay_application)
            result.append(
                {
                    "sov_id": sov_id,
                    "item_number": item_number,
                    "description_of_work": sov.get("description", ""),
                    "scheduled_value": scheduled_value,
                    "work_completed_this_period": completed_amount,
                    "materials_presently_stored": ZERO,
                    "_matched_activity_id": best_match_id,
                    "_progress_pct": best_match_pct,
                }
            )
            matched_sov_ids.add(sov_id)

    # Include unmatched SOV items with zero billing
    for sov in sov_items:
        sov_id = str(sov.get("id", ""))
        if sov_id not in matched_sov_ids:
            scheduled_value = Decimal(str(sov.get("scheduled_value", 0)))
            if scheduled_value > ZERO:
                result.append(
                    {
                        "sov_id": sov_id,
                        "item_number": sov.get("item_number", ""),
                        "description_of_work": sov.get("description", ""),
                        "scheduled_value": scheduled_value,
                        "work_completed_this_period": ZERO,
                        "materials_presently_stored": ZERO,
                    }
                )

    return result


# ---------------------------------------------------------------------------
# Auto-generate pay app from progress snapshot
# ---------------------------------------------------------------------------


async def auto_generate_pay_app_from_progress(
    db: AsyncSession,
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    period_to,
    submitted_by: uuid.UUID | None = None,
) -> Any:
    """Generate a pay application from the latest progress snapshot.

    This is the key automation function. It:
    1. Loads the specified ProgressSnapshot
    2. Loads SOV line items for the project
    3. Loads schedule activities for matching
    4. Maps activity progress to SOV items
    5. Creates a pay application via pay_application_service

    Args:
        db: Async database session.
        project_id: Project ID.
        snapshot_id: Progress snapshot to base billing on.
        period_to: Billing period end date.
        submitted_by: Optional user who triggered the generation.

    Returns:
        The created PayApplication ORM instance.

    Raises:
        ValueError: If snapshot not found, no SOV items, or mapping fails.
    """
    from app.models.pay_application import ScheduleOfValues
    from app.models.progress_tracking import ProgressSnapshot
    from app.models.scheduling import ScheduleActivity
    from app.services.controls.pay_application_service import create_pay_application

    # Load snapshot
    snapshot = await db.get(ProgressSnapshot, snapshot_id)
    if snapshot is None:
        raise ValueError("Progress snapshot not found")
    if str(snapshot.project_id) != str(project_id):
        raise ValueError("Snapshot does not belong to this project")

    activities_progress = snapshot.activities_progress or {}
    if not activities_progress:
        raise ValueError("Progress snapshot has no activity data")

    # Load SOV items
    sov_result = await db.execute(
        select(ScheduleOfValues)
        .where(ScheduleOfValues.project_id == project_id)
        .order_by(ScheduleOfValues.sort_order, ScheduleOfValues.item_number)
    )
    sov_records = list(sov_result.scalars().all())
    if not sov_records:
        raise ValueError("No Schedule of Values found for project")

    sov_items = [
        {
            "id": str(s.id),
            "item_number": s.item_number,
            "description": s.description,
            "scheduled_value": s.scheduled_value,
            "csi_code": s.csi_code,
        }
        for s in sov_records
    ]

    # Load schedule activities for matching
    act_result = await db.execute(
        select(ScheduleActivity).where(ScheduleActivity.project_id == project_id)
    )
    activities = list(act_result.scalars().all())
    schedule_activities = [
        {
            "id": str(a.id),
            "name": a.name,
            "wbs_code": a.wbs_code,
            "activity_code": a.activity_code,
            "pct_complete": float(a.pct_complete or 0),
        }
        for a in activities
    ]

    # Map progress to SOV
    mapped_items = _map_progress_to_sov(activities_progress, sov_items, schedule_activities)
    if not mapped_items:
        raise ValueError("Could not map any progress data to SOV items")

    # Get retainage config
    config = await _get_payment_config(db, project_id)
    retainage_pct = config.retainage_pct if config else Decimal("10.00")

    # Create pay application
    pay_app = await create_pay_application(
        db,
        project_id=project_id,
        period_to=period_to,
        line_items_input=mapped_items,
        retainage_pct=retainage_pct,
        submitted_by=submitted_by,
    )

    logger.info(
        "Auto-generated pay application #%d for project %s from snapshot %s",
        pay_app.application_number,
        project_id,
        snapshot_id,
    )
    return pay_app


# ---------------------------------------------------------------------------
# Payment submission
# ---------------------------------------------------------------------------


async def submit_payment(
    db: AsyncSession,
    pay_application_id: uuid.UUID,
    payment_method: str | None = None,
) -> Any:
    """Create a payment transaction from an approved pay application.

    The pay application must be in 'certified' status. Creates a
    PaymentTransaction with computed retainage and net amount.

    Args:
        db: Async database session.
        pay_application_id: ID of the certified pay application.
        payment_method: Optional method (ach, wire, check).

    Returns:
        The created PaymentTransaction ORM instance.

    Raises:
        ValueError: If pay app not found or not certified.
    """
    from app.models.instant_pay import PaymentTransaction
    from app.models.pay_application import PayApplication

    pay_app = await db.get(PayApplication, pay_application_id)
    if pay_app is None:
        raise ValueError("Pay application not found")
    if pay_app.status != "certified":
        raise ValueError(
            f"Pay application must be certified to submit payment, current status: {pay_app.status}"
        )

    # Check for existing active transaction
    existing = await db.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.pay_application_id == pay_application_id,
            PaymentTransaction.status.notin_(["failed", "cancelled"]),
        )
    )
    if existing.scalars().first():
        raise ValueError("An active payment transaction already exists for this pay application")

    # Get config for retainage
    config = await _get_payment_config(db, pay_app.project_id)
    retainage_pct = config.retainage_pct if config else pay_app.retainage_pct

    # Compute amounts
    gross_amount = pay_app.current_payment_due
    retainage_data = compute_retainage(gross_amount, retainage_pct)

    transaction = PaymentTransaction(
        project_id=pay_app.project_id,
        pay_application_id=pay_application_id,
        transaction_type="owner_to_gc",
        amount=gross_amount,
        status="submitted",
        submitted_at=datetime.now(UTC),
        retainage_pct=retainage_pct,
        retainage_amount=retainage_data["retainage_amount"],
        net_amount=retainage_data["net_amount"],
        payment_method=payment_method,
        payer_info=pay_app.architect_info,
        payee_info=pay_app.contractor_info,
        processor_name=config.processor_name if config else None,
        metadata_={
            "application_number": pay_app.application_number,
            "period_to": str(pay_app.period_to),
        },
    )
    db.add(transaction)
    await db.flush()
    await db.refresh(transaction)

    logger.info(
        "Payment submitted for pay app #%d: $%s (net $%s)",
        pay_app.application_number,
        gross_amount,
        retainage_data["net_amount"],
    )
    return transaction


# ---------------------------------------------------------------------------
# Webhook handling
# ---------------------------------------------------------------------------


async def handle_payment_webhook(
    db: AsyncSession,
    project_id: uuid.UUID,
    payload: dict,
    signature: str,
    webhook_secret: str,
) -> Any:
    """Handle incoming payment processor webhook.

    Validates HMAC-SHA256 signature, updates transaction status, and
    auto-generates lien waivers on successful payment if configured.

    Args:
        db: Async database session.
        project_id: Project the webhook belongs to.
        payload: Raw webhook payload dict.
        signature: HMAC-SHA256 signature from the webhook header.
        webhook_secret: Secret key to validate the signature.

    Returns:
        The updated PaymentTransaction ORM instance.

    Raises:
        ValueError: On invalid signature or missing transaction.
    """
    from app.models.instant_pay import PaymentTransaction

    # SECURITY: Validate HMAC-SHA256 signature
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    expected_sig = hmac.new(
        webhook_secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, signature):
        logger.warning("SECURITY: Invalid webhook signature for project %s", project_id)
        raise ValueError("Invalid webhook signature")

    # Extract transaction ID and new status from payload
    processor_txn_id = payload.get("transaction_id")
    event_type = payload.get("event_type", "")
    new_status = payload.get("status", "")

    if not processor_txn_id:
        raise ValueError("Webhook payload missing transaction_id")

    # Find the transaction
    result = await db.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.project_id == project_id,
            PaymentTransaction.processor_transaction_id == str(processor_txn_id),
        )
    )
    transaction = result.scalars().first()

    if transaction is None:
        # Try matching by pay_application_id from metadata
        pay_app_id = payload.get("pay_application_id")
        if pay_app_id:
            result = await db.execute(
                select(PaymentTransaction).where(
                    PaymentTransaction.project_id == project_id,
                    PaymentTransaction.pay_application_id == uuid.UUID(str(pay_app_id)),
                    PaymentTransaction.status.notin_(["failed", "cancelled"]),
                )
            )
            transaction = result.scalars().first()

    if transaction is None:
        raise ValueError(f"Payment transaction not found for processor ID {processor_txn_id}")

    # Update status based on event
    now = datetime.now(UTC)
    status_map = {
        "payment.approved": "approved",
        "payment.processing": "processing",
        "payment.completed": "paid",
        "payment.paid": "paid",
        "payment.failed": "failed",
        "payment.cancelled": "cancelled",
    }

    mapped_status = status_map.get(event_type, new_status)
    if not mapped_status:
        logger.warning("Unknown webhook event type: %s", event_type)
        return transaction

    # Apply status-specific timestamps
    if mapped_status == "approved" and not transaction.approved_at:
        transaction.approved_at = now
    elif mapped_status == "paid" and not transaction.paid_at:
        transaction.paid_at = now
    elif mapped_status == "failed" and not transaction.failed_at:
        transaction.failed_at = now
        transaction.failure_reason = payload.get("failure_reason", "Payment failed")

    transaction.status = mapped_status
    transaction.processor_transaction_id = str(processor_txn_id)

    await db.flush()
    await db.refresh(transaction)

    # Send payment notification
    if mapped_status == "paid":
        try:
            from app.services.notifications.event_notifier import notify

            # Notify relevant users about the payment
            notify_user_ids = []
            if transaction.payee_info and isinstance(transaction.payee_info, dict):
                payee_user_id = transaction.payee_info.get("user_id")
                if payee_user_id:
                    notify_user_ids.append(uuid.UUID(str(payee_user_id)))
            if notify_user_ids:
                await notify(
                    db,
                    event_type="payment.received",
                    project_id=project_id,
                    user_ids=notify_user_ids,
                    context_data={
                        "amount": f"${transaction.net_amount:,.2f}",
                        "project": str(project_id),
                        "transaction_id": str(transaction.id),
                    },
                )
        except Exception:
            logger.warning(
                "Failed to send payment notification for transaction %s",
                transaction.id,
                exc_info=True,
            )

    # Auto-generate lien waivers on successful payment
    if mapped_status == "paid":
        config = await _get_payment_config(db, project_id)
        if config and config.auto_generate_lien_waivers:
            try:
                await generate_lien_waiver_package(
                    db,
                    transaction.pay_application_id,
                    package_type="unconditional",
                )
                logger.info(
                    "Auto-generated lien waiver package for paid transaction %s",
                    transaction.id,
                )
            except Exception as exc:
                logger.error(
                    "Failed to auto-generate lien waivers for transaction %s: %s",
                    transaction.id,
                    exc,
                )

    logger.info(
        "Webhook processed: transaction %s -> %s",
        transaction.id,
        mapped_status,
    )
    return transaction


# ---------------------------------------------------------------------------
# Lien waiver package
# ---------------------------------------------------------------------------


async def generate_lien_waiver_package(
    db: AsyncSession,
    pay_application_id: uuid.UUID,
    package_type: str = "conditional",
) -> Any:
    """Create a lien waiver package for a pay application.

    Generates waiver items for each vendor/sub involved based on the
    pay application line items. Uses the existing LienWaiver model from
    cash_flow for individual waiver records and bundles them into a
    LienWaiverPackage.

    Args:
        db: Async database session.
        pay_application_id: Pay application to generate waivers for.
        package_type: 'conditional' or 'unconditional'.

    Returns:
        The created LienWaiverPackage ORM instance.

    Raises:
        ValueError: If pay application not found.
    """
    from app.models.cash_flow import LienWaiver
    from app.models.instant_pay import LienWaiverPackage
    from app.models.pay_application import PayApplication

    if package_type not in ("conditional", "unconditional"):
        raise ValueError("package_type must be 'conditional' or 'unconditional'")

    pay_app = await db.get(PayApplication, pay_application_id)
    if pay_app is None:
        raise ValueError("Pay application not found")

    # Build waiver items from pay app line items
    waiver_items: list[dict] = []
    total_amount = ZERO

    for li in pay_app.line_items or []:
        if li.work_completed_this_period and li.work_completed_this_period > ZERO:
            waiver_items.append(
                {
                    "item_number": li.item_number,
                    "description": li.description_of_work,
                    "amount": str(li.work_completed_this_period),
                    "status": "pending",
                }
            )
            total_amount += li.work_completed_this_period

    if not waiver_items:
        # Use total current payment due if no per-line breakdown
        total_amount = pay_app.current_payment_due
        waiver_items = [
            {
                "item_number": "TOTAL",
                "description": f"Pay Application #{pay_app.application_number}",
                "amount": str(total_amount),
                "status": "pending",
            }
        ]

    # Also create individual LienWaiver records for tracking
    contractor_name = (pay_app.contractor_info or {}).get("name", "General Contractor")
    lien_waiver = LienWaiver(
        project_id=pay_app.project_id,
        pay_application_id=pay_application_id,
        waiver_type=package_type,
        vendor_name=contractor_name,
        amount=_round2(total_amount),
        through_date=pay_app.period_to,
        status="pending",
    )
    db.add(lien_waiver)

    # Create the package
    package = LienWaiverPackage(
        project_id=pay_app.project_id,
        pay_application_id=pay_application_id,
        package_type=package_type,
        waiver_items=waiver_items,
        total_amount=_round2(total_amount),
        status="draft",
        generated_at=datetime.now(UTC),
    )
    db.add(package)
    await db.flush()
    await db.refresh(package)

    logger.info(
        "Generated %s lien waiver package for pay app #%d: $%s",
        package_type,
        pay_app.application_number,
        total_amount,
    )
    return package


# ---------------------------------------------------------------------------
# Payment status
# ---------------------------------------------------------------------------


async def get_payment_status(
    db: AsyncSession,
    project_id: uuid.UUID,
    pay_application_id: uuid.UUID | None = None,
) -> list[dict]:
    """Get payment transaction status with waterfall timing.

    Returns a list of transaction dicts with status, amounts, and timestamps.

    Args:
        db: Async database session.
        project_id: Project ID to scope the query.
        pay_application_id: Optional filter to a specific pay application.

    Returns:
        List of payment status dicts.
    """
    from app.models.instant_pay import PaymentTransaction

    query = (
        select(PaymentTransaction)
        .where(PaymentTransaction.project_id == project_id)
        .order_by(PaymentTransaction.created_at.desc())
    )
    if pay_application_id:
        query = query.where(PaymentTransaction.pay_application_id == pay_application_id)

    result = await db.execute(query)
    transactions = list(result.scalars().all())

    return [
        {
            "id": str(t.id),
            "pay_application_id": str(t.pay_application_id),
            "transaction_type": t.transaction_type,
            "amount": float(t.amount),
            "net_amount": float(t.net_amount),
            "retainage_amount": float(t.retainage_amount) if t.retainage_amount else None,
            "currency": t.currency,
            "status": t.status,
            "payment_method": t.payment_method,
            "processor_name": t.processor_name,
            "processor_transaction_id": t.processor_transaction_id,
            "submitted_at": t.submitted_at.isoformat() if t.submitted_at else None,
            "approved_at": t.approved_at.isoformat() if t.approved_at else None,
            "paid_at": t.paid_at.isoformat() if t.paid_at else None,
            "failed_at": t.failed_at.isoformat() if t.failed_at else None,
            "failure_reason": t.failure_reason,
            "waterfall": _compute_waterfall_timing(t),
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in transactions
    ]


def _compute_waterfall_timing(transaction) -> dict:
    """Compute payment waterfall timing from transaction timestamps."""
    timing: dict[str, Any] = {}

    if transaction.submitted_at:
        timing["submitted"] = transaction.submitted_at.isoformat()

    if transaction.approved_at and transaction.submitted_at:
        delta = (transaction.approved_at - transaction.submitted_at).total_seconds()
        timing["approval_duration_hours"] = round(delta / 3600, 1)

    if transaction.paid_at and transaction.submitted_at:
        delta = (transaction.paid_at - transaction.submitted_at).total_seconds()
        timing["total_duration_hours"] = round(delta / 3600, 1)

    if transaction.paid_at and transaction.approved_at:
        delta = (transaction.paid_at - transaction.approved_at).total_seconds()
        timing["processing_duration_hours"] = round(delta / 3600, 1)

    return timing


# ---------------------------------------------------------------------------
# Payment integration config
# ---------------------------------------------------------------------------


async def configure_payment_integration(
    db: AsyncSession,
    project_id: uuid.UUID,
    config_data: dict,
) -> Any:
    """Create or update payment integration configuration for a project.

    Args:
        db: Async database session.
        project_id: Project to configure.
        config_data: Configuration data including processor_name, webhook_secret, etc.

    Returns:
        The PaymentIntegrationConfig ORM instance.
    """
    from app.models.instant_pay import PaymentIntegrationConfig

    # Check for existing config
    result = await db.execute(
        select(PaymentIntegrationConfig).where(PaymentIntegrationConfig.project_id == project_id)
    )
    existing = result.scalars().first()

    if existing:
        # Update existing
        for field in (
            "processor_name",
            "webhook_secret",
            "config",
            "retainage_pct",
            "payment_terms_days",
            "auto_generate_pay_apps",
            "auto_generate_lien_waivers",
            "is_active",
        ):
            if field in config_data:
                setattr(existing, field, config_data[field])
        await db.flush()
        await db.refresh(existing)
        return existing

    # Create new
    processor_name = config_data.get("processor_name")
    if not processor_name:
        raise ValueError("processor_name is required")

    config = PaymentIntegrationConfig(
        project_id=project_id,
        processor_name=processor_name,
        webhook_secret=config_data.get("webhook_secret"),
        config=config_data.get("config", {}),
        retainage_pct=Decimal(str(config_data.get("retainage_pct", 10))),
        payment_terms_days=config_data.get("payment_terms_days", 30),
        auto_generate_pay_apps=config_data.get("auto_generate_pay_apps", False),
        auto_generate_lien_waivers=config_data.get("auto_generate_lien_waivers", True),
        is_active=config_data.get("is_active", True),
    )
    db.add(config)
    await db.flush()
    await db.refresh(config)
    return config


async def _get_payment_config(db: AsyncSession, project_id: uuid.UUID) -> Any | None:
    """Get payment integration config for a project."""
    from app.models.instant_pay import PaymentIntegrationConfig

    result = await db.execute(
        select(PaymentIntegrationConfig).where(
            PaymentIntegrationConfig.project_id == project_id,
            PaymentIntegrationConfig.is_active == True,  # noqa: E712
        )
    )
    return result.scalars().first()
