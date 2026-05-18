"""Certified payroll and prevailing wage API endpoints."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.pagination import PaginationMeta
from app.schemas.payroll import (
    CertifiedPayrollReportListResponse,
    CertifiedPayrollReportResponse,
    CertifiedPayrollReportSummary,
    CertifiedReportGenerateRequest,
    ComplianceSummaryResponse,
    PayrollRecordBatchCreate,
    PayrollRecordCreate,
    PayrollRecordListResponse,
    PayrollRecordResponse,
    PrevailingWageRateResponse,
)
from app.services.compliance.payroll_service import (
    certify_report,
    create_payroll_batch,
    create_payroll_record,
    generate_certified_report,
    get_compliance_summary,
    list_payroll_records,
    list_reports,
    lookup_prevailing_wage,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Prevailing Wages
# ---------------------------------------------------------------------------


@router.get(
    "/prevailing-wages",
    response_model=PrevailingWageRateResponse | None,
)
async def get_prevailing_wage(
    state: str = Query(..., min_length=2, max_length=2, description="2-letter state code"),
    trade: str = Query(..., description="Trade classification"),
    county: str | None = Query(default=None, description="County name"),
    as_of_date: date | None = Query(default=None, description="Effective date"),
    current_user: User = Depends(require_permission("payroll", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Look up prevailing wage rate for a location and trade."""
    rate = await lookup_prevailing_wage(db, state, county, trade, as_of_date)
    if rate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No prevailing wage rate found for {state}/{county}/{trade}",
        )
    return rate


# ---------------------------------------------------------------------------
# Payroll Records
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/payroll/records",
    response_model=PayrollRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_record(
    project_id: uuid.UUID,
    request: PayrollRecordCreate,
    current_user: User = Depends(require_permission("payroll", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Create a payroll record with auto-compliance check."""
    await verify_project_access(project_id, current_user, db)

    record_data = request.model_dump()
    record_data["created_by"] = current_user.id

    try:
        record = await create_payroll_record(db, project_id, record_data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return record


@router.post(
    "/{project_id}/payroll/records/batch",
    response_model=PayrollRecordListResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_records_batch(
    project_id: uuid.UUID,
    request: PayrollRecordBatchCreate,
    current_user: User = Depends(require_permission("payroll", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Batch create payroll records."""
    await verify_project_access(project_id, current_user, db)

    record_dicts = []
    for rec in request.records:
        d = rec.model_dump()
        d["created_by"] = current_user.id
        record_dicts.append(d)

    try:
        records = await create_payroll_batch(db, project_id, record_dicts)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    return PayrollRecordListResponse(
        data=cast(list[PayrollRecordResponse], records),
        meta=PaginationMeta(has_more=False),
    )


@router.get(
    "/{project_id}/payroll/records",
    response_model=PayrollRecordListResponse,
)
async def list_records(
    project_id: uuid.UUID,
    period_start: date | None = Query(default=None),
    period_end: date | None = Query(default=None),
    trade: str | None = Query(default=None),
    current_user: User = Depends(require_permission("payroll", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List payroll records for a project with filters."""
    await verify_project_access(project_id, current_user, db)

    records = await list_payroll_records(db, project_id, period_start, period_end, trade)
    return PayrollRecordListResponse(
        data=cast(list[PayrollRecordResponse], records),
        meta=PaginationMeta(has_more=False),
    )


# ---------------------------------------------------------------------------
# Certified Payroll Reports
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/payroll/reports/generate",
    response_model=CertifiedPayrollReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_report(
    project_id: uuid.UUID,
    request: CertifiedReportGenerateRequest,
    current_user: User = Depends(require_permission("payroll", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a WH-347 certified payroll report for a pay period."""
    await verify_project_access(project_id, current_user, db)

    contractor_info = {
        "name": request.contractor_name,
        "address": request.contractor_address or "",
        "contract_number": request.contract_number or "",
        "signer_name": request.signer_name or "",
        "signer_title": request.signer_title or "",
    }

    try:
        report = await generate_certified_report(
            db,
            project_id,
            request.pay_period_start,
            request.pay_period_end,
            contractor_info,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return report


@router.post(
    "/{project_id}/payroll/reports/{report_id}/certify",
    response_model=CertifiedPayrollReportResponse,
)
async def certify_payroll_report(
    project_id: uuid.UUID,
    report_id: uuid.UUID,
    current_user: User = Depends(require_permission("payroll", "approve")),
    db: AsyncSession = Depends(get_db),
):
    """Certify (sign) a payroll report."""
    await verify_project_access(project_id, current_user, db)

    try:
        report = await certify_report(db, report_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return report


@router.get(
    "/{project_id}/payroll/reports",
    response_model=CertifiedPayrollReportListResponse,
)
async def list_payroll_reports(
    project_id: uuid.UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("payroll", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List certified payroll reports for a project."""
    await verify_project_access(project_id, current_user, db)

    reports, total = await list_reports(db, project_id, skip, limit)
    has_more = (skip + limit) < total

    return CertifiedPayrollReportListResponse(
        data=cast(list[CertifiedPayrollReportSummary], reports),
        meta=PaginationMeta(has_more=has_more),
        total=total,
    )


# ---------------------------------------------------------------------------
# Compliance Summary
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/payroll/compliance",
    response_model=ComplianceSummaryResponse,
)
async def get_project_compliance(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("payroll", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Get prevailing wage compliance summary for a project."""
    await verify_project_access(project_id, current_user, db)

    summary = await get_compliance_summary(db, project_id)
    return ComplianceSummaryResponse(
        total_records=summary.total_records,
        compliant_records=summary.compliant_records,
        underpayment_records=summary.underpayment_records,
        review_records=summary.review_records,
        compliance_rate=summary.compliance_rate,
        total_underpayment=summary.total_underpayment,
        trades_with_issues=summary.trades_with_issues,
    )
