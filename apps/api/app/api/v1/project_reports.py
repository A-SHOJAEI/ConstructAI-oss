"""Project report API endpoints.

Routes for generating key project reports:
  - Monthly cost report
  - Schedule performance report
  - Safety trend report
  - Subcontractor performance report
  - Portfolio summary report
  - OSHA compliance report
  - Certified payroll report
  - EVM report
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.project_reports import (
    CertifiedPayrollReportResponse,
    EVMReportResponse,
    MonthlyCostReportResponse,
    OSHAComplianceReportResponse,
    PortfolioSummaryReportResponse,
    SafetyTrendReportResponse,
    SchedulePerformanceReportResponse,
    SubcontractorPerformanceReportResponse,
)
from app.services.reporting.project_reports import (
    generate_certified_payroll_report,
    generate_evm_report,
    generate_monthly_cost_report,
    generate_osha_compliance_report,
    generate_portfolio_summary_report,
    generate_safety_trend_report,
    generate_schedule_performance_report,
    generate_subcontractor_performance_report,
)

router = APIRouter()


@router.get(
    "/{project_id}/reports/monthly-cost",
    response_model=MonthlyCostReportResponse,
)
async def monthly_cost_report(
    project_id: uuid.UUID,
    month: int = Query(..., ge=1, le=12, description="Report month (1-12)"),
    year: int = Query(..., ge=2020, le=2100, description="Report year"),
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a monthly cost report with budget vs actuals by CSI division,
    change order impacts, cost projections, and cash flow summary."""
    await verify_project_access(project_id, current_user, db)
    return await generate_monthly_cost_report(db, project_id, month, year)


@router.get(
    "/{project_id}/reports/schedule-performance",
    response_model=SchedulePerformanceReportResponse,
)
async def schedule_performance_report(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a schedule performance report with SPI trend, critical path
    activities, delay analysis, and 2-week lookahead."""
    await verify_project_access(project_id, current_user, db)
    return await generate_schedule_performance_report(db, project_id)


@router.get(
    "/{project_id}/reports/safety-trend",
    response_model=SafetyTrendReportResponse,
)
async def safety_trend_report(
    project_id: uuid.UUID,
    months: int = Query(6, ge=1, le=24, description="Number of months to analyze"),
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a safety trend report with incident rates, PPE compliance,
    and top hazards over the specified period."""
    await verify_project_access(project_id, current_user, db)
    return await generate_safety_trend_report(db, project_id, months)


@router.get(
    "/{project_id}/reports/subcontractor-performance",
    response_model=SubcontractorPerformanceReportResponse,
)
async def subcontractor_performance_report(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a subcontractor performance report with schedule adherence,
    quality metrics, and RFI responsiveness scorecards."""
    await verify_project_access(project_id, current_user, db)
    return await generate_subcontractor_performance_report(db, project_id)


@router.get(
    "/{project_id}/reports/portfolio-summary",
    response_model=PortfolioSummaryReportResponse,
)
async def portfolio_summary_report(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate an executive-level cross-project portfolio summary report.
    Uses the org_id from the current user's organization."""
    await verify_project_access(project_id, current_user, db)
    org_id = current_user.org_id
    return await generate_portfolio_summary_report(db, org_id)


@router.get(
    "/{project_id}/reports/osha-compliance",
    response_model=OSHAComplianceReportResponse,
)
async def osha_compliance_report(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate an OSHA compliance report mapping project activities to
    applicable OSHA 1926 sections."""
    await verify_project_access(project_id, current_user, db)
    return await generate_osha_compliance_report(db, project_id)


@router.get(
    "/{project_id}/reports/certified-payroll",
    response_model=CertifiedPayrollReportResponse,
)
async def certified_payroll_report(
    project_id: uuid.UUID,
    period_start: date = Query(..., description="Payroll period start date"),
    period_end: date = Query(..., description="Payroll period end date"),
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate a certified payroll report with WH-347 data for the
    specified period."""
    await verify_project_access(project_id, current_user, db)
    return await generate_certified_payroll_report(db, project_id, period_start, period_end)


@router.get(
    "/{project_id}/reports/evm",
    response_model=EVMReportResponse,
)
async def evm_report(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("reports", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Generate an EVM report with S-curve data, variance analysis,
    and EAC projections."""
    await verify_project_access(project_id, current_user, db)
    return await generate_evm_report(db, project_id)
