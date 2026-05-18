"""AIA G702/G703 Pay Application API endpoints."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date
from decimal import Decimal
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.pay_application import PayApplication
from app.models.user import User
from app.schemas.pagination import PaginationMeta
from app.schemas.pay_application import (
    PayApplicationCreate,
    PayApplicationListResponse,
    PayApplicationResponse,
    PayApplicationSummary,
    PayApplicationUpdate,
    SOVBulkCreate,
    SOVLineItemResponse,
    SOVListResponse,
)
from app.services.controls.pay_application_service import (
    auto_populate_pay_application,
    create_pay_application,
    create_sov_bulk,
    create_sov_from_estimate,
    list_sov,
)
from app.services.controls.pdf_generator import generate_g702_pdf, generate_g703_pdf

logger = logging.getLogger(__name__)

router = APIRouter()

# Valid status transitions for pay applications
_VALID_STATUS_TRANSITIONS = {
    "draft": {"submitted"},
    "submitted": {"reviewed", "draft"},  # allow rejection back to draft
    "reviewed": {"certified", "submitted"},  # allow rejection back to submitted
    "certified": set(),  # no transitions from certified (immutable)
}

# ---------------------------------------------------------------------------
# Schedule of Values
# ---------------------------------------------------------------------------


@router.post(
    "/sov",
    response_model=SOVListResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule_of_values(
    request: SOVBulkCreate,
    current_user: User = Depends(require_permission("pay_applications", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Bulk create Schedule of Values line items."""
    await verify_project_access(request.project_id, current_user, db)

    items = [li.model_dump() for li in request.line_items]
    sov_items = await create_sov_bulk(db, request.project_id, items)
    return SOVListResponse(
        data=cast(list[SOVLineItemResponse], sov_items),
        meta=PaginationMeta(has_more=False),
    )


@router.get(
    "/sov",
    response_model=SOVListResponse,
)
async def list_schedule_of_values(
    project_id: uuid.UUID = Query(...),
    skip: int = Query(default=0, ge=0, description="Number of items to skip"),
    limit: int = Query(default=100, ge=1, le=500, description="Maximum items to return"),
    current_user: User = Depends(require_permission("pay_applications", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List SOV line items for a project with pagination."""
    await verify_project_access(project_id, current_user, db)
    items = await list_sov(db, project_id)
    total = len(items)
    paginated = items[skip : skip + limit]
    has_more = (skip + limit) < total
    return SOVListResponse(
        data=cast(list[SOVLineItemResponse], paginated),
        meta=PaginationMeta(has_more=has_more),
    )


@router.post(
    "/sov/from-estimate",
    response_model=SOVListResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_sov_from_cost_estimate(
    project_id: uuid.UUID = Query(...),
    estimate_id: uuid.UUID = Query(...),
    current_user: User = Depends(require_permission("pay_applications", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Auto-populate SOV from a cost estimate."""
    await verify_project_access(project_id, current_user, db)

    try:
        items = await create_sov_from_estimate(db, project_id, estimate_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return SOVListResponse(
        data=cast(list[SOVLineItemResponse], items),
        meta=PaginationMeta(has_more=False),
    )


# ---------------------------------------------------------------------------
# Pay Applications
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=PayApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_new_pay_application(
    request: PayApplicationCreate,
    current_user: User = Depends(require_permission("pay_applications", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new pay application with computed G702/G703 fields."""
    await verify_project_access(request.project_id, current_user, db)

    line_items_input = [li.model_dump() for li in request.line_items]
    try:
        pay_app = await create_pay_application(
            db,
            project_id=request.project_id,
            period_to=request.period_to,
            line_items_input=line_items_input,
            contractor_info=request.contractor_info,
            architect_info=request.architect_info,
            retainage_pct=request.retainage_pct,
            submitted_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return pay_app


@router.get(
    "",
    response_model=PayApplicationListResponse,
)
async def list_pay_applications(
    project_id: uuid.UUID = Query(...),
    cursor: str | None = Query(default=None),
    # M-9: cap offset so deep paging can't trigger O(N) heap scans on
    # large projects. Past this point, clients should switch to cursor.
    skip: int = Query(
        default=0,
        ge=0,
        le=10_000,
        description="Records to skip (offset pagination, max 10000 — use cursor beyond this)",
    ),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("pay_applications", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List pay applications (summary view) for a project.

    Supports both cursor-based and offset-based pagination. When *cursor*
    is provided it takes precedence; otherwise *skip*/*limit* are applied.
    """
    await verify_project_access(project_id, current_user, db)

    query = (
        select(PayApplication)
        .where(PayApplication.project_id == project_id)
        .order_by(PayApplication.application_number.desc())
    )
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor format: must be a valid UUID",
            )
        cursor_app = await db.get(PayApplication, cursor_uuid)
        if cursor_app:
            query = query.where(PayApplication.created_at < cursor_app.created_at)
    elif skip > 0:
        query = query.offset(skip)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    apps = list(result.scalars().all())

    has_more = len(apps) > limit
    if has_more:
        apps = apps[:limit]

    next_cursor = str(apps[-1].id) if has_more and apps else None
    return PayApplicationListResponse(
        data=cast(list[PayApplicationSummary], apps),
        meta=PaginationMeta(cursor=next_cursor, has_more=has_more),
    )


@router.get(
    "/{pay_app_id}",
    response_model=PayApplicationResponse,
)
async def get_pay_application(
    pay_app_id: uuid.UUID,
    current_user: User = Depends(require_permission("pay_applications", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get a pay application with full line items."""
    pay_app = await db.get(PayApplication, pay_app_id)
    if pay_app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pay application not found"
        )
    await verify_project_access(pay_app.project_id, current_user, db)
    return pay_app


@router.patch(
    "/{pay_app_id}",
    response_model=PayApplicationResponse,
)
async def update_pay_application(
    pay_app_id: uuid.UUID,
    request: PayApplicationUpdate,
    current_user: User = Depends(require_permission("pay_applications", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a draft pay application. Recomputes all derived fields."""
    pay_app = await db.get(PayApplication, pay_app_id, with_for_update=True)
    if pay_app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pay application not found"
        )
    await verify_project_access(pay_app.project_id, current_user, db)

    update_data = request.model_dump(exclude_unset=True)

    # Block line item modifications on non-draft pay applications
    if "line_items" in update_data and pay_app.status != "draft":
        raise HTTPException(
            status_code=422, detail="Can only update line items on draft pay applications"
        )

    # Validate status transition if status is being changed
    if "status" in update_data and update_data["status"] is not None:
        new_status = update_data["status"]
        allowed = _VALID_STATUS_TRANSITIONS.get(pay_app.status, set())
        if new_status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status transition from '{pay_app.status}' to '{new_status}'. "
                f"Allowed transitions: {sorted(allowed) if allowed else 'none (immutable)'}.",
            )

    # Block non-status, non-line-item field changes on non-draft pay applications
    other_fields = set(update_data.keys()) - {"status", "line_items"}
    if other_fields and pay_app.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Can only update draft pay applications",
        )

    # If line items provided, recreate with full recomputation
    if "line_items" in update_data and update_data["line_items"] is not None:
        from app.models.pay_application import PayApplicationLineItem
        from app.services.controls.pay_application_math import (
            compute_g702_totals,
            compute_g703_line,
            validate_no_overbilling,
        )

        async with db.begin_nested():
            # Delete existing line items
            for li in list(pay_app.line_items or []):
                await db.delete(li)
            await db.flush()

            retainage_pct = update_data.get("retainage_pct", pay_app.retainage_pct)
            line_items_input = [li.model_dump() for li in (request.line_items or [])]

            # Get previous app data for Column D
            from app.services.controls.pay_application_service import get_previous_pay_app_totals

            prev_data = await get_previous_pay_app_totals(
                db, pay_app.project_id, pay_app.application_number
            )

            enriched_lines = []
            for i, li_input in enumerate(line_items_input):
                sov_id = li_input.get("sov_id")
                work_prev = (
                    prev_data["line_totals"].get(sov_id, Decimal("0")) if sov_id else Decimal("0")
                )
                enriched_lines.append(
                    {
                        **li_input,
                        "work_completed_previous": work_prev,
                        "retainage_pct": li_input.get("retainage_pct", retainage_pct),
                        "sort_order": i,
                    }
                )

            # Validate no overbilling before committing
            overbilling_warnings = validate_no_overbilling(enriched_lines)
            if overbilling_warnings:
                items = ", ".join(
                    f"item {w['item_number']} (excess ${w['excess']})" for w in overbilling_warnings
                )
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Overbilling detected: {items}",
                )

            # Compute G702 totals
            g702 = compute_g702_totals(
                line_items=enriched_lines,
                retainage_pct=retainage_pct,
                less_previous_certificates=prev_data["less_previous_certificates"],
                original_contract_sum=pay_app.original_contract_sum,
                net_change_by_cos=pay_app.net_change_by_cos,
            )

            # Update G702 fields
            for key, val in g702.items():
                setattr(pay_app, key, val)
            pay_app.retainage_pct = retainage_pct
            pay_app.less_previous_certificates = prev_data["less_previous_certificates"]

            # Create new line items
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
    else:
        # Update simple fields
        for field in ("period_to", "contractor_info", "architect_info", "status"):
            if field in update_data and update_data[field] is not None:
                setattr(pay_app, field, update_data[field])

    await db.flush()
    await db.refresh(pay_app)
    return pay_app


@router.post("/{pay_app_id}/submit", response_model=PayApplicationResponse)
async def submit_pay_application(
    pay_app_id: uuid.UUID,
    current_user: User = Depends(require_permission("pay_applications", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Transition pay app from draft to submitted."""
    pay_app = await db.get(PayApplication, pay_app_id, with_for_update=True)
    if pay_app is None:
        raise HTTPException(status_code=404, detail="Pay application not found")
    await verify_project_access(pay_app.project_id, current_user, db)

    if pay_app.status != "draft":
        raise HTTPException(status_code=422, detail="Can only submit draft pay applications")

    from datetime import datetime

    pay_app.status = "submitted"
    pay_app.submitted_by = current_user.id
    pay_app.submitted_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(pay_app)
    return pay_app


@router.post("/{pay_app_id}/certify", response_model=PayApplicationResponse)
async def certify_pay_application(
    pay_app_id: uuid.UUID,
    current_user: User = Depends(require_permission("pay_applications", "approve")),
    db: AsyncSession = Depends(get_db),
):
    """Architect certifies the pay application."""
    pay_app = await db.get(PayApplication, pay_app_id, with_for_update=True)
    if pay_app is None:
        raise HTTPException(status_code=404, detail="Pay application not found")
    await verify_project_access(pay_app.project_id, current_user, db)

    if pay_app.status not in ("submitted", "reviewed"):
        raise HTTPException(
            status_code=422, detail="Can only certify submitted or reviewed pay applications"
        )

    from datetime import datetime

    pay_app.status = "certified"
    pay_app.certified_by = current_user.id
    pay_app.certified_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(pay_app)

    # IG-14: After certification, check if auto-payment is configured for this
    # project. If so, submit a payment transaction. Failures are logged as
    # warnings — they never block the certification response.
    try:
        from app.models.instant_pay import PaymentIntegrationConfig
        from app.services.controls.instant_pay_service import submit_payment

        config_result = await db.execute(
            select(PaymentIntegrationConfig).where(
                PaymentIntegrationConfig.project_id == pay_app.project_id,
                PaymentIntegrationConfig.is_active.is_(True),
            )
        )
        payment_config = config_result.scalars().first()

        if payment_config and payment_config.auto_generate_pay_apps:
            try:
                await submit_payment(
                    db=db,
                    pay_application_id=pay_app.id,
                )
                logger.info(
                    "Auto-submitted payment for certified pay app %s (project %s)",
                    pay_app_id,
                    pay_app.project_id,
                )
            except ValueError as pay_err:
                logger.warning(
                    "Auto-payment submission failed for pay app %s: %s",
                    pay_app_id,
                    pay_err,
                )
            except Exception:
                logger.warning(
                    "Unexpected error during auto-payment for pay app %s",
                    pay_app_id,
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "Failed to check payment integration config for project %s",
            pay_app.project_id,
            exc_info=True,
        )

    return pay_app


# ---------------------------------------------------------------------------
# PDF Generation
# ---------------------------------------------------------------------------


@router.get("/{pay_app_id}/pdf/g702")
async def download_g702_pdf(
    pay_app_id: uuid.UUID,
    current_user: User = Depends(require_permission("pay_applications", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate and download G702 PDF."""
    pay_app = await db.get(PayApplication, pay_app_id)
    if pay_app is None:
        raise HTTPException(status_code=404, detail="Pay application not found")
    await verify_project_access(pay_app.project_id, current_user, db)

    from app.models.project import Project

    project = await db.get(Project, pay_app.project_id)
    project_name = project.name if project else "Unknown Project"

    pay_app_data = {
        "application_number": pay_app.application_number,
        "period_to": str(pay_app.period_to),
        "original_contract_sum": str(pay_app.original_contract_sum),
        "net_change_by_cos": str(pay_app.net_change_by_cos),
        "contract_sum_to_date": str(pay_app.contract_sum_to_date),
        "total_completed_and_stored": str(pay_app.total_completed_and_stored),
        "retainage_pct": str(pay_app.retainage_pct),
        "retainage_work_completed": str(pay_app.retainage_work_completed),
        "retainage_stored_materials": str(pay_app.retainage_stored_materials),
        "total_retainage": str(pay_app.total_retainage),
        "total_earned_less_retainage": str(pay_app.total_earned_less_retainage),
        "less_previous_certificates": str(pay_app.less_previous_certificates),
        "current_payment_due": str(pay_app.current_payment_due),
        "balance_to_finish_including_retainage": str(pay_app.balance_to_finish_including_retainage),
    }

    contractor_name = pay_app.contractor_info.get("name", "")
    architect_name = pay_app.architect_info.get("name", "")

    pdf_bytes = generate_g702_pdf(pay_app_data, project_name, contractor_name, architect_name)

    import io

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="G702_App{pay_app.application_number}.pdf"'
        },
    )


@router.get("/{pay_app_id}/pdf/g703")
async def download_g703_pdf(
    pay_app_id: uuid.UUID,
    current_user: User = Depends(require_permission("pay_applications", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate and download G703 continuation sheet PDF."""
    pay_app = await db.get(PayApplication, pay_app_id)
    if pay_app is None:
        raise HTTPException(status_code=404, detail="Pay application not found")
    await verify_project_access(pay_app.project_id, current_user, db)

    from app.models.project import Project

    project = await db.get(Project, pay_app.project_id)
    project_name = project.name if project else "Unknown Project"

    line_items = []
    for li in sorted(pay_app.line_items or [], key=lambda x: x.sort_order):
        line_items.append(
            {
                "item_number": li.item_number,
                "description_of_work": li.description_of_work,
                "scheduled_value": str(li.scheduled_value),
                "work_completed_previous": str(li.work_completed_previous),
                "work_completed_this_period": str(li.work_completed_this_period),
                "materials_presently_stored": str(li.materials_presently_stored),
                "total_completed_and_stored": str(li.total_completed_and_stored),
                "percent_complete": str(li.percent_complete),
                "balance_to_finish": str(li.balance_to_finish),
            }
        )

    pay_app_data = {
        "application_number": pay_app.application_number,
        "period_to": str(pay_app.period_to),
    }

    pdf_bytes = generate_g703_pdf(line_items, pay_app_data, project_name)

    import io

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="G703_App{pay_app.application_number}.pdf"'
        },
    )


# ---------------------------------------------------------------------------
# Auto-populate
# ---------------------------------------------------------------------------


@router.get("/{project_id}/export")
async def export_pay_applications(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("pay_applications", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Export all pay applications for a project as a CSV file."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(PayApplication)
        .where(PayApplication.project_id == project_id)
        .order_by(PayApplication.application_number.asc())
    )
    result = await db.execute(query)
    apps = result.scalars().all()

    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "application_number",
            "period_to",
            "status",
            "original_contract_sum",
            "net_change_by_cos",
            "contract_sum_to_date",
            "total_completed_and_stored",
            "total_retainage",
            "current_payment_due",
            "balance_to_finish_including_retainage",
            "created_at",
        ]
    )
    for app in apps:
        writer.writerow(
            [
                app.application_number,
                str(app.period_to) if app.period_to else "",
                app.status,
                str(app.original_contract_sum),
                str(app.net_change_by_cos),
                str(app.contract_sum_to_date),
                str(app.total_completed_and_stored),
                str(app.total_retainage),
                str(app.current_payment_due),
                str(app.balance_to_finish_including_retainage),
                app.created_at.isoformat() if app.created_at else "",
            ]
        )

    csv_bytes = output.getvalue().encode("utf-8")
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=pay_applications_export.csv"},
    )


@router.get("/auto-populate")
async def auto_populate(
    project_id: uuid.UUID = Query(...),
    period_to: date = Query(...),
    current_user: User = Depends(require_permission("pay_applications", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Preview auto-populated pay app data from SOV and prior pay apps."""
    await verify_project_access(project_id, current_user, db)

    try:
        data = await auto_populate_pay_application(db, project_id, period_to)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return data
