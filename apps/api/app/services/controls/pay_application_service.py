"""Pay application service: CRUD, auto-population, CO integration."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evm import ChangeOrder
from app.models.pay_application import (
    PayApplication,
    PayApplicationLineItem,
    ScheduleOfValues,
)
from app.models.project import Project
from app.services.controls.pay_application_math import (
    compute_g702_totals,
    compute_g703_line,
    validate_no_overbilling,
)

logger = logging.getLogger(__name__)

# SECURITY: Configurable overbilling threshold. If total_completed_and_stored
# exceeds this percentage of the scheduled_value, the pay application is
# blocked (when block_overbilling=True) or warned (default).
# Default: 105% — allows minor overages due to rounding but catches true overbilling.
OVERBILLING_THRESHOLD_PCT = Decimal("105.00")


# ---------------------------------------------------------------------------
# Schedule of Values
# ---------------------------------------------------------------------------


async def create_sov_bulk(
    db: AsyncSession,
    project_id: uuid.UUID,
    line_items: list[dict],
) -> list[ScheduleOfValues]:
    """Create SOV line items in bulk."""
    created = []
    for i, li in enumerate(line_items):
        sov = ScheduleOfValues(
            project_id=project_id,
            item_number=li["item_number"],
            description=li["description"],
            scheduled_value=li["scheduled_value"],
            csi_code=li.get("csi_code"),
            sort_order=li.get("sort_order", i),
        )
        db.add(sov)
        created.append(sov)
    await db.flush()
    for sov in created:
        await db.refresh(sov)
    return created


async def create_sov_from_estimate(
    db: AsyncSession,
    project_id: uuid.UUID,
    estimate_id: uuid.UUID,
) -> list[ScheduleOfValues]:
    """Auto-populate SOV from a CostEstimate's line items."""
    from app.models.estimating import EstimateLineItem

    result = await db.execute(
        select(EstimateLineItem)
        .where(EstimateLineItem.estimate_id == estimate_id)
        .order_by(EstimateLineItem.id)
    )
    estimate_lines = list(result.scalars().all())

    if not estimate_lines:
        raise ValueError("No line items found in estimate")

    items = []
    for i, eli in enumerate(estimate_lines):
        items.append(
            {
                "item_number": eli.csi_code or str(i + 1),
                "description": eli.description,
                "scheduled_value": eli.total_cost or Decimal("0"),
                "csi_code": eli.csi_code,
                "sort_order": i,
            }
        )
    return await create_sov_bulk(db, project_id, items)


async def add_co_to_sov(
    db: AsyncSession,
    project_id: uuid.UUID,
    change_order_id: uuid.UUID,
) -> ScheduleOfValues:
    """Add a new SOV line item when a CO is approved.

    Creates a line with is_change_order_line=True, linked to the CO.
    """
    co = await db.get(ChangeOrder, change_order_id)
    if co is None:
        raise ValueError("Change order not found")

    # Determine sort order (after existing items)
    max_sort_result = await db.execute(
        select(func.coalesce(func.max(ScheduleOfValues.sort_order), 0)).where(
            ScheduleOfValues.project_id == project_id
        )
    )
    next_sort = max_sort_result.scalar_one() + 1

    sov = ScheduleOfValues(
        project_id=project_id,
        item_number=f"CO-{co.co_number}",
        description=f"Change Order #{co.co_number}: {co.title}",
        scheduled_value=co.cost_impact,
        change_order_id=change_order_id,
        is_change_order_line=True,
        sort_order=next_sort,
    )
    db.add(sov)
    await db.flush()
    await db.refresh(sov)
    return sov


async def list_sov(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> list[ScheduleOfValues]:
    """List all SOV line items for a project, ordered by sort_order."""
    result = await db.execute(
        select(ScheduleOfValues)
        .where(ScheduleOfValues.project_id == project_id)
        .order_by(ScheduleOfValues.sort_order, ScheduleOfValues.item_number)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Contract sums
# ---------------------------------------------------------------------------


async def get_contract_sums_for_g702(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict[str, Decimal]:
    """Compute original_contract_sum and net_change_by_cos.

    original_contract_sum = project.contract_value
    net_change_by_cos = SUM(change_orders.cost_impact WHERE status='approved')
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found")

    original = project.contract_value or Decimal("0")

    result = await db.execute(
        select(func.coalesce(func.sum(ChangeOrder.cost_impact), Decimal("0"))).where(
            ChangeOrder.project_id == project_id,
            ChangeOrder.status == "approved",
        )
    )
    net_change = result.scalar_one()

    return {
        "original_contract_sum": original,
        "net_change_by_cos": net_change,
    }


# ---------------------------------------------------------------------------
# Previous pay app data
# ---------------------------------------------------------------------------


async def get_previous_pay_app_totals(
    db: AsyncSession,
    project_id: uuid.UUID,
    before_app_number: int,
) -> dict:
    """Get data from the most recent certified/paid pay app.

    Returns:
        less_previous_certificates: total_earned_less_retainage from prior app
        line_totals: {sov_id: total_completed_and_stored} for auto-populating Column D
    """
    result = await db.execute(
        select(PayApplication)
        .where(
            PayApplication.project_id == project_id,
            PayApplication.application_number < before_app_number,
            PayApplication.status.in_(["certified", "paid"]),
        )
        .order_by(PayApplication.application_number.desc())
        .limit(1)
    )
    prev_app = result.scalars().first()

    if prev_app is None:
        return {
            "less_previous_certificates": Decimal("0"),
            "line_totals": {},
        }

    # Build per-SOV line totals from previous pay app's line items
    line_totals = {}
    for li in prev_app.line_items:
        if li.sov_id:
            line_totals[li.sov_id] = li.total_completed_and_stored

    return {
        "less_previous_certificates": prev_app.total_earned_less_retainage,
        "line_totals": line_totals,
    }


# ---------------------------------------------------------------------------
# Pay Application CRUD
# ---------------------------------------------------------------------------


async def get_next_app_number(db: AsyncSession, project_id: uuid.UUID) -> int:
    """Get the next sequential application number for a project."""
    result = await db.execute(
        select(func.coalesce(func.max(PayApplication.application_number), 0)).where(
            PayApplication.project_id == project_id
        )
    )
    return result.scalar_one() + 1


async def create_pay_application(
    db: AsyncSession,
    project_id: uuid.UUID,
    period_to,
    line_items_input: list[dict],
    *,
    contractor_info: dict | None = None,
    architect_info: dict | None = None,
    retainage_pct: Decimal = Decimal("10.00"),
    submitted_by: uuid.UUID | None = None,
    block_overbilling: bool = False,
    overbilling_threshold_pct: Decimal | None = None,
) -> PayApplication:
    """Create a new pay application with computed fields.

    Steps:
    1. Get next application_number
    2. Fetch contract sums (original + net_change from COs)
    3. Auto-populate work_completed_previous from prior pay app
    4. Compute all G703 line items (columns G, H, I)
    5. Validate overbilling (warnings returned in response)
    6. Compute G702 totals
    7. Persist PayApplication + PayApplicationLineItem records
    """
    app_number = await get_next_app_number(db, project_id)
    contract_sums = await get_contract_sums_for_g702(db, project_id)
    prev_data = await get_previous_pay_app_totals(db, project_id, app_number)

    original_contract_sum = contract_sums["original_contract_sum"]
    net_change_by_cos = contract_sums["net_change_by_cos"]
    less_previous_certificates = prev_data["less_previous_certificates"]
    prev_line_totals = prev_data["line_totals"]

    # Build line items with auto-populated Column D
    enriched_lines = []
    for i, li_input in enumerate(line_items_input):
        sov_id = li_input.get("sov_id")
        work_completed_previous = Decimal("0")
        if sov_id and sov_id in prev_line_totals:
            work_completed_previous = prev_line_totals[sov_id]

        enriched_lines.append(
            {
                "sov_id": sov_id,
                "item_number": li_input["item_number"],
                "description_of_work": li_input["description_of_work"],
                "scheduled_value": li_input["scheduled_value"],
                "work_completed_previous": work_completed_previous,
                "work_completed_this_period": li_input.get(
                    "work_completed_this_period", Decimal("0")
                ),
                "materials_presently_stored": li_input.get(
                    "materials_presently_stored", Decimal("0")
                ),
                "retainage_pct": li_input.get("retainage_pct", retainage_pct),
                "sort_order": i,
            }
        )

    # Check overbilling
    overbilling_warnings = validate_no_overbilling(enriched_lines)
    threshold = overbilling_threshold_pct or OVERBILLING_THRESHOLD_PCT
    overbilling_events: list[dict] = []  # M-24: aggregated for ops notification
    if overbilling_warnings:
        logger.warning(
            "Overbilling detected on %d line items for project %s",
            len(overbilling_warnings),
            project_id,
        )
        # SECURITY: Check if any line item exceeds the configurable threshold.
        # When block_overbilling is True, raise an error to prevent submission.
        for warning in overbilling_warnings:
            scheduled = warning.get("scheduled", Decimal("0"))
            billed = warning.get("billed", Decimal("0"))
            if scheduled > 0:
                pct = (billed / scheduled) * Decimal("100")
                if pct > threshold:
                    msg = (
                        f"Line item '{warning.get('item_number', '?')}' billed at "
                        f"{pct:.1f}% of scheduled value (threshold: {threshold}%)"
                    )
                    if block_overbilling:
                        raise ValueError(
                            f"Overbilling blocked: {msg}. "
                            f"Set block_overbilling=False to allow with warning."
                        )
                    logger.warning("Overbilling threshold exceeded: %s", msg)
                    overbilling_events.append(
                        {
                            "item_number": warning.get("item_number"),
                            "scheduled": str(scheduled),
                            "billed": str(billed),
                            "pct": str(pct.quantize(Decimal("0.01"))),
                        }
                    )

    # M-24: Publish an event when any line crosses the threshold so ops /
    # billing leadership get alerted instead of losing the signal in logs.
    if overbilling_events:
        try:
            from app.services.orchestration.event_router import EventRouter

            router = EventRouter()
            await router.route_event(
                {
                    "type": "constructai.controls.overbilling_detected",
                    "ce-projectid": str(project_id),
                    "ce-priority": 2,
                    "data": {
                        "project_id": str(project_id),
                        "lines": overbilling_events,
                        "threshold_pct": str(threshold),
                    },
                }
            )
        except Exception:
            # Notification is best-effort; never block the pay app.
            logger.exception("Failed to publish overbilling event")

    # Compute G702 totals
    g702 = compute_g702_totals(
        line_items=enriched_lines,
        retainage_pct=retainage_pct,
        less_previous_certificates=less_previous_certificates,
        original_contract_sum=original_contract_sum,
        net_change_by_cos=net_change_by_cos,
    )

    # Create PayApplication
    pay_app = PayApplication(
        project_id=project_id,
        application_number=app_number,
        period_to=period_to,
        contractor_info=contractor_info or {},
        architect_info=architect_info or {},
        original_contract_sum=original_contract_sum,
        net_change_by_cos=net_change_by_cos,
        contract_sum_to_date=g702["contract_sum_to_date"],
        total_completed_and_stored=g702["total_completed_and_stored"],
        retainage_pct=retainage_pct,
        retainage_work_completed=g702["retainage_work_completed"],
        retainage_stored_materials=g702["retainage_stored_materials"],
        total_retainage=g702["total_retainage"],
        total_earned_less_retainage=g702["total_earned_less_retainage"],
        less_previous_certificates=less_previous_certificates,
        current_payment_due=g702["current_payment_due"],
        balance_to_finish_including_retainage=g702["balance_to_finish_including_retainage"],
        submitted_by=submitted_by,
    )
    db.add(pay_app)
    await db.flush()
    await db.refresh(pay_app)

    # Create line items with computed columns
    for el in enriched_lines:
        g703 = compute_g703_line(
            el["scheduled_value"],
            el["work_completed_previous"],
            el["work_completed_this_period"],
            el["materials_presently_stored"],
        )
        line_item = PayApplicationLineItem(
            pay_application_id=pay_app.id,
            sov_id=el.get("sov_id"),
            item_number=el["item_number"],
            description_of_work=el["description_of_work"],
            scheduled_value=el["scheduled_value"],
            work_completed_previous=el["work_completed_previous"],
            work_completed_this_period=el["work_completed_this_period"],
            materials_presently_stored=el["materials_presently_stored"],
            total_completed_and_stored=g703["total_completed_and_stored"],
            percent_complete=g703["percent_complete"],
            balance_to_finish=g703["balance_to_finish"],
            retainage_pct=el["retainage_pct"],
            sort_order=el["sort_order"],
        )
        db.add(line_item)

    await db.flush()
    await db.refresh(pay_app)
    return pay_app


async def auto_populate_pay_application(
    db: AsyncSession,
    project_id: uuid.UUID,
    period_to,
    *,
    retainage_pct: Decimal = Decimal("10.00"),
) -> dict:
    """Generate pre-populated pay app data from SOV and prior pay apps.

    Returns the input structure a client would use to create the pay app,
    with work_completed_previous auto-filled from the last certified app.
    """
    sov_items = await list_sov(db, project_id)
    if not sov_items:
        raise ValueError("No Schedule of Values found for project")

    app_number = await get_next_app_number(db, project_id)
    prev_data = await get_previous_pay_app_totals(db, project_id, app_number)
    contract_sums = await get_contract_sums_for_g702(db, project_id)

    line_items = []
    for sov in sov_items:
        work_completed_previous = prev_data["line_totals"].get(sov.id, Decimal("0"))
        line_items.append(
            {
                "sov_id": str(sov.id),
                "item_number": sov.item_number,
                "description_of_work": sov.description,
                "scheduled_value": str(sov.scheduled_value),
                "work_completed_previous": str(work_completed_previous),
                "work_completed_this_period": "0",
                "materials_presently_stored": "0",
                "retainage_pct": str(retainage_pct),
            }
        )

    return {
        "project_id": str(project_id),
        "application_number": app_number,
        "period_to": str(period_to),
        "original_contract_sum": str(contract_sums["original_contract_sum"]),
        "net_change_by_cos": str(contract_sums["net_change_by_cos"]),
        "less_previous_certificates": str(prev_data["less_previous_certificates"]),
        "line_items": line_items,
    }
