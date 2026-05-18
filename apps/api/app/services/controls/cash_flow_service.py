"""Cash flow service: DB-aware orchestration for cash flow forecasting and lien waivers."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cash_flow import CashFlowSnapshot, LienWaiver
from app.models.evm import ChangeOrder
from app.models.pay_application import PayApplication, ScheduleOfValues
from app.models.project import Project
from app.models.scheduling import ScheduleActivity
from app.services.controls.cash_flow_engine import (
    LienWaiverAnalysis,
    compute_actual_cash_flow,
    compute_planned_cash_curve,
    evaluate_lien_waiver_coverage,
    forecast_cash_flow,
    run_cash_flow_monte_carlo,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cash flow forecast
# ---------------------------------------------------------------------------


async def generate_cash_flow_forecast(
    db: AsyncSession,
    project_id: uuid.UUID,
    config: dict | None = None,
    created_by: uuid.UUID | None = None,
) -> dict:
    """Generate a cash flow forecast for a project.

    Fetches SOV, pay apps, change orders, and schedule activities from DB,
    calls pure engine functions, optionally runs Monte Carlo, and saves a snapshot.

    Config options:
        payment_lag_owner_days: int (default 30)
        payment_lag_sub_days: int (default 45)
        retainage_pct: Decimal (default 10)
        include_monte_carlo: bool (default True)
        num_simulations: int (default 5000)
    """
    config = config or {}
    payment_lag_owner_days = config.get("payment_lag_owner_days", 30)
    retainage_pct = Decimal(str(config.get("retainage_pct", 10)))
    include_monte_carlo = config.get("include_monte_carlo", True)
    num_simulations = config.get("num_simulations", 5000)

    # Fetch project for dates
    project = await db.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found")

    project_start = getattr(project, "start_date", None) or date.today()
    project_end = getattr(project, "end_date", None) or getattr(project, "target_completion", None)
    if project_end is None:
        # Default: 12 months from start
        project_end = date(project_start.year + 1, project_start.month, project_start.day)

    # Fetch SOV items
    sov_result = await db.execute(
        select(ScheduleOfValues)
        .where(ScheduleOfValues.project_id == project_id)
        .order_by(ScheduleOfValues.sort_order)
    )
    sov_items_db: list[ScheduleOfValues] = list(sov_result.scalars().all())

    sov_items = [
        {
            "item_number": s.item_number,
            "csi_code": s.csi_code,
            "scheduled_value": s.scheduled_value,
        }
        for s in sov_items_db
    ]

    # Fetch schedule activities
    act_result = await db.execute(
        select(ScheduleActivity)
        .where(ScheduleActivity.project_id == project_id)
        .order_by(ScheduleActivity.start_date)
    )
    activities_db: list[ScheduleActivity] = list(act_result.scalars().all())

    schedule_activities = [
        {
            "activity_code": a.activity_code,
            "wbs_code": a.wbs_code,
            "early_start": a.early_start or a.start_date,
            "early_finish": a.early_finish or a.finish_date,
        }
        for a in activities_db
    ]

    # Fetch pay applications
    pa_result = await db.execute(
        select(PayApplication)
        .where(PayApplication.project_id == project_id)
        .order_by(PayApplication.application_number)
    )
    pay_apps_db: list[PayApplication] = list(pa_result.scalars().all())

    pay_apps = [
        {
            "period_to": pa.period_to,
            "current_payment_due": pa.current_payment_due,
            "total_completed_and_stored": pa.total_completed_and_stored,
            "status": pa.status,
            "paid_at": pa.paid_at,
            "application_number": pa.application_number,
            "contractor_info": getattr(pa, "contractor_info", {}),
        }
        for pa in pay_apps_db
    ]

    # Fetch approved change orders
    co_result = await db.execute(
        select(ChangeOrder).where(
            ChangeOrder.project_id == project_id,
            ChangeOrder.status == "approved",
        )
    )
    cos_db: list[ChangeOrder] = list(co_result.scalars().all())

    change_orders = [
        {
            "approved_date": getattr(co, "approved_date", None)
            or getattr(co, "created_at", date.today()),
            "cost_impact": co.cost_impact,
        }
        for co in cos_db
    ]

    # Compute planned curve
    planned_curve = compute_planned_cash_curve(
        sov_items=sov_items,
        schedule_activities=schedule_activities,
        project_start=project_start,
        project_end=project_end,
    )

    # Compute actual cash flow
    actual_curve = compute_actual_cash_flow(
        pay_apps=pay_apps,
        change_orders=change_orders,
        payment_lag_owner_days=payment_lag_owner_days,
    )

    # Calculate remaining months
    today = date.today()
    if project_end > today:
        remaining_months = (project_end.year - today.year) * 12 + (project_end.month - today.month)
    else:
        remaining_months = 0

    # Run forecast
    forecast = forecast_cash_flow(
        planned_curve=planned_curve,
        actual_curve=actual_curve,
        remaining_months=remaining_months,
        retainage_pct=retainage_pct,
        payment_lag_owner_days=payment_lag_owner_days,
    )

    # Optional Monte Carlo
    confidence_intervals = None
    if include_monte_carlo and forecast.monthly_projections:
        try:
            ci = run_cash_flow_monte_carlo(
                forecast=forecast,
                num_simulations=num_simulations,
            )
            confidence_intervals = {
                "p10": [str(v) for v in ci.p10],
                "p50": [str(v) for v in ci.p50],
                "p90": [str(v) for v in ci.p90],
                "worst_month_position": str(ci.worst_month_position),
                "months_negative": ci.months_negative,
            }
        except RuntimeError:
            logger.warning("Monte Carlo unavailable, skipping confidence intervals")

    # Build response
    forecast_data = {
        "monthly_projections": [
            {
                "month": str(p.month),
                "planned_billings": str(p.planned_billings),
                "actual_billings": str(p.actual_billings),
                "expected_receipts": str(p.expected_receipts),
                "actual_receipts": str(p.actual_receipts),
                "net_cash_position": str(p.net_cash_position),
                "cumulative_billed": str(p.cumulative_billed),
                "cumulative_received": str(p.cumulative_received),
            }
            for p in forecast.monthly_projections
        ],
        "summary": {
            "total_contract_value": str(forecast.total_contract_value),
            "total_billed": str(forecast.total_billed),
            "total_received": str(forecast.total_received),
            "retainage_held": str(forecast.retainage_held),
            "months_remaining": forecast.months_remaining,
        },
        "risk_indicators": forecast.risk_indicators,
        "confidence_intervals": confidence_intervals,
    }

    # Save snapshot
    snapshot = CashFlowSnapshot(
        project_id=project_id,
        snapshot_date=today,
        forecast_data=forecast_data,
        config=config,
        created_by=created_by,
    )
    db.add(snapshot)
    await db.flush()
    await db.refresh(snapshot)

    forecast_data["snapshot_id"] = str(snapshot.id)
    forecast_data["project_id"] = str(project_id)
    forecast_data["generated_at"] = str(snapshot.created_at)

    logger.info(
        "Cash flow forecast generated for project %s: %d months, %d risk indicators",
        project_id,
        len(forecast.monthly_projections),
        len(forecast.risk_indicators),
    )

    return forecast_data


# ---------------------------------------------------------------------------
# Cash flow history
# ---------------------------------------------------------------------------


async def get_cash_flow_history(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> list[dict]:
    """Return historical monthly cash flow from pay application data.

    Builds a month-by-month view of billings and receipts from all
    pay applications for the project.
    """
    result = await db.execute(
        select(PayApplication)
        .where(PayApplication.project_id == project_id)
        .order_by(PayApplication.application_number)
    )
    pay_apps_db = list(result.scalars().all())

    if not pay_apps_db:
        return []

    pay_apps = [
        {
            "period_to": pa.period_to,
            "current_payment_due": pa.current_payment_due,
            "total_completed_and_stored": pa.total_completed_and_stored,
            "status": pa.status,
            "paid_at": pa.paid_at,
            "application_number": pa.application_number,
            "contractor_info": getattr(pa, "contractor_info", {}),
        }
        for pa in pay_apps_db
    ]

    actual_curve = compute_actual_cash_flow(
        pay_apps=pay_apps,
        change_orders=[],
    )

    return [
        {
            "month": str(p.month),
            "actual_billings": str(p.actual_billings),
            "actual_receipts": str(p.actual_receipts),
            "expected_receipts": str(p.expected_receipts),
            "net_cash_position": str(p.net_cash_position),
            "cumulative_billed": str(p.cumulative_billed),
            "cumulative_received": str(p.cumulative_received),
        }
        for p in actual_curve
    ]


# ---------------------------------------------------------------------------
# Lien waiver CRUD
# ---------------------------------------------------------------------------


VALID_WAIVER_TYPES = {
    "conditional_partial",
    "conditional_final",
    "unconditional_partial",
    "unconditional_final",
}

VALID_WAIVER_STATUSES = {"pending", "received", "void"}


async def create_lien_waiver(
    db: AsyncSession,
    project_id: uuid.UUID,
    waiver_data: dict,
) -> LienWaiver:
    """Create a new lien waiver for a project."""
    waiver_type = waiver_data.get("waiver_type")
    if waiver_type not in VALID_WAIVER_TYPES:
        raise ValueError(
            f"waiver_type must be one of {sorted(VALID_WAIVER_TYPES)}, got '{waiver_type}'"
        )

    # Validate project exists
    project = await db.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found")

    # Validate pay_application_id if provided
    pay_app_id = waiver_data.get("pay_application_id")
    if pay_app_id:
        pay_app = await db.get(PayApplication, pay_app_id)
        if pay_app is None:
            raise ValueError("Pay application not found")
        if pay_app.project_id != project_id:
            raise ValueError("Pay application does not belong to this project")

    waiver = LienWaiver(
        project_id=project_id,
        pay_application_id=pay_app_id,
        waiver_type=waiver_type,
        vendor_name=waiver_data["vendor_name"],
        amount=Decimal(str(waiver_data["amount"]))
        if waiver_data.get("amount") is not None
        else None,
        through_date=waiver_data.get("through_date"),
        signed_date=waiver_data.get("signed_date"),
        status=waiver_data.get("status", "pending"),
        document_url=waiver_data.get("document_url"),
        notes=waiver_data.get("notes"),
    )
    db.add(waiver)
    await db.flush()
    await db.refresh(waiver)

    logger.info(
        "Lien waiver created: %s for vendor '%s' on project %s",
        waiver.id,
        waiver.vendor_name,
        project_id,
    )
    return waiver


async def list_lien_waivers(
    db: AsyncSession,
    project_id: uuid.UUID,
    status: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[LienWaiver], int]:
    """List lien waivers for a project with optional status filter.

    Returns (waivers, total_count).
    """
    query = select(LienWaiver).where(LienWaiver.project_id == project_id)
    count_query = select(func.count(LienWaiver.id)).where(LienWaiver.project_id == project_id)

    if status:
        if status not in VALID_WAIVER_STATUSES:
            raise ValueError(f"status must be one of {sorted(VALID_WAIVER_STATUSES)}")
        query = query.where(LienWaiver.status == status)
        count_query = count_query.where(LienWaiver.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = query.order_by(LienWaiver.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    waivers = list(result.scalars().all())

    return waivers, total


async def update_lien_waiver(
    db: AsyncSession,
    waiver_id: uuid.UUID,
    update_data: dict,
) -> LienWaiver:
    """Update a lien waiver (status, signed_date, document_url, notes)."""
    waiver = await db.get(LienWaiver, waiver_id, with_for_update=True)
    if waiver is None:
        raise ValueError("Lien waiver not found")

    # Validate status transition if provided
    new_status = update_data.get("status")
    if new_status is not None:
        if new_status not in VALID_WAIVER_STATUSES:
            raise ValueError(f"status must be one of {sorted(VALID_WAIVER_STATUSES)}")

        # Cannot un-void a waiver
        if waiver.status == "void" and new_status != "void":
            raise ValueError("Cannot change status of a void waiver")

    # Apply updates
    allowed_fields = {"status", "signed_date", "document_url", "notes"}
    for field_name in allowed_fields:
        if field_name in update_data and update_data[field_name] is not None:
            setattr(waiver, field_name, update_data[field_name])

    await db.flush()
    await db.refresh(waiver)

    logger.info("Lien waiver updated: %s, status=%s", waiver_id, waiver.status)
    return waiver


# ---------------------------------------------------------------------------
# Lien waiver analysis
# ---------------------------------------------------------------------------


async def evaluate_project_lien_coverage(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> LienWaiverAnalysis:
    """Evaluate lien waiver coverage for a project.

    Fetches all waivers and pay apps, then calls the pure engine function.
    """
    # Fetch waivers
    result = await db.execute(select(LienWaiver).where(LienWaiver.project_id == project_id))
    waivers_db = list(result.scalars().all())

    waivers = [
        {
            "vendor_name": w.vendor_name,
            "through_date": w.through_date,
            "status": w.status,
            "amount": w.amount,
            "signed_date": w.signed_date,
            "waiver_type": w.waiver_type,
        }
        for w in waivers_db
    ]

    # Fetch pay apps
    pa_result2 = await db.execute(
        select(PayApplication)
        .where(PayApplication.project_id == project_id)
        .order_by(PayApplication.application_number)
    )
    pay_apps_db2: list[PayApplication] = list(pa_result2.scalars().all())

    pay_apps = [
        {
            "period_to": pa.period_to,
            "current_payment_due": pa.current_payment_due,
            "application_number": pa.application_number,
            "contractor_info": getattr(pa, "contractor_info", {}),
        }
        for pa in pay_apps_db2
    ]

    return evaluate_lien_waiver_coverage(waivers=waivers, pay_apps=pay_apps)
