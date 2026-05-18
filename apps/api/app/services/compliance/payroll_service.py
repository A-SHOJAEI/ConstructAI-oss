"""Certified payroll DB service layer.

Handles CRUD for payroll records, prevailing wage lookups, certified report
generation, and compliance summaries. Uses payroll_engine.py for all math.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import (
    CertifiedPayrollReport,
    PayrollRecord,
    PrevailingWageRate,
)
from app.models.project import Project
from app.services.compliance.payroll_engine import (
    ZERO,
    _round2,
    calculate_fringe_benefits,
    calculate_gross_pay,
    check_prevailing_wage_compliance,
    generate_wh347_data,
)

logger = logging.getLogger(__name__)


@dataclass
class ComplianceSummary:
    """Summary of prevailing wage compliance for a project."""

    total_records: int
    compliant_records: int
    underpayment_records: int
    review_records: int
    compliance_rate: Decimal
    total_underpayment: Decimal
    trades_with_issues: list[str]


# ---------------------------------------------------------------------------
# Prevailing wage lookup
# ---------------------------------------------------------------------------


async def lookup_prevailing_wage(
    db: AsyncSession,
    state: str,
    county: str | None,
    trade: str,
    as_of_date: date | None = None,
) -> PrevailingWageRate | None:
    """Look up the prevailing wage rate for a location, trade, and date.

    First tries exact match on state + county + trade where
    effective_date <= as_of_date. Falls back to state-level (county=None)
    if county-specific rate is not found.

    Args:
        db: Database session.
        state: 2-letter state code.
        county: County name (optional).
        trade: Trade classification.
        as_of_date: Date for rate lookup. Defaults to today.

    Returns:
        PrevailingWageRate or None if not found.
    """
    if as_of_date is None:
        as_of_date = date.today()

    state = state.upper()

    # Try county-specific first
    if county:
        result = await db.execute(
            select(PrevailingWageRate)
            .where(
                PrevailingWageRate.location_state == state,
                PrevailingWageRate.location_county == county,
                PrevailingWageRate.trade == trade,
                PrevailingWageRate.effective_date <= as_of_date,
            )
            .order_by(PrevailingWageRate.effective_date.desc())
            .limit(1)
        )
        rate = result.scalars().first()
        if rate is not None:
            # Check expiration
            if rate.expiration_date and rate.expiration_date < as_of_date:
                logger.warning(
                    "Prevailing wage rate %s expired on %s",
                    rate.id,
                    rate.expiration_date,
                )
            else:
                return rate

    # Fall back to state-level
    result = await db.execute(
        select(PrevailingWageRate)
        .where(
            PrevailingWageRate.location_state == state,
            PrevailingWageRate.location_county.is_(None),
            PrevailingWageRate.trade == trade,
            PrevailingWageRate.effective_date <= as_of_date,
        )
        .order_by(PrevailingWageRate.effective_date.desc())
        .limit(1)
    )
    rate = result.scalars().first()
    if rate and rate.expiration_date and rate.expiration_date < as_of_date:
        logger.warning(
            "State-level prevailing wage rate %s expired on %s",
            rate.id,
            rate.expiration_date,
        )
    return rate


# ---------------------------------------------------------------------------
# Payroll record CRUD
# ---------------------------------------------------------------------------


async def create_payroll_record(
    db: AsyncSession,
    project_id: uuid.UUID,
    record_data: dict,
) -> PayrollRecord:
    """Create a single payroll record with auto compliance check.

    Computes gross pay, looks up prevailing wage, and checks compliance.

    Args:
        db: Database session.
        project_id: UUID of the project.
        record_data: Dict with worker_name, trade, classification,
            pay_period_start, pay_period_end, hours_*, rate_*, etc.

    Returns:
        Created PayrollRecord.
    """
    hours_st = Decimal(str(record_data.get("hours_straight", 0)))
    hours_ot = Decimal(str(record_data.get("hours_overtime", 0)))
    hours_other = Decimal(str(record_data.get("hours_other", 0)))
    rate_st = Decimal(str(record_data.get("rate_straight", 0)))
    rate_ot_raw = record_data.get("rate_overtime")
    rate_ot = Decimal(str(rate_ot_raw)) if rate_ot_raw is not None else None

    # Calculate gross pay
    gross = calculate_gross_pay(hours_st, hours_ot, hours_other, rate_st, rate_ot)
    if rate_ot is None:
        rate_ot = _round2(rate_st * Decimal("1.5"))

    # Calculate fringe benefits
    total_hours = hours_st + hours_ot + hours_other
    fringe_rate = Decimal(str(record_data.get("fringe_rate", 0)))
    fringe_breakdown = record_data.get("fringe_breakdown")
    fringe_result = calculate_fringe_benefits(total_hours, fringe_rate, fringe_breakdown)
    fringe_dict = {
        "health": str(fringe_result.health),
        "pension": str(fringe_result.pension),
        "vacation": str(fringe_result.vacation),
        "training": str(fringe_result.training),
        "other": str(fringe_result.other),
    }

    # Deductions (pass through if provided)
    deductions = record_data.get("deductions", {})
    net_pay_raw = record_data.get("net_pay")
    if net_pay_raw is not None:
        net_pay = Decimal(str(net_pay_raw))
    else:
        total_deductions = ZERO
        if isinstance(deductions, dict):
            for val in deductions.values():
                total_deductions += Decimal(str(val))
        net_pay = _round2(gross - total_deductions)

    # Check prevailing wage compliance
    compliance_status = "review"
    prevailing_wage_rate_id = None

    project = await db.get(Project, project_id)
    if project:
        # Try to find state from project address
        state = record_data.get("state")
        county = record_data.get("county")
        trade = record_data.get("trade", "")

        if state:
            pw_rate = await lookup_prevailing_wage(
                db,
                state,
                county,
                trade,
                as_of_date=record_data.get("pay_period_start"),
            )
            if pw_rate:
                prevailing_wage_rate_id = pw_rate.id
                compliance = check_prevailing_wage_compliance(
                    {
                        "rate_straight": rate_st,
                        "fringe_benefits": fringe_dict,
                        "hours_straight": hours_st,
                        "hours_overtime": hours_ot,
                        "hours_other": hours_other,
                    },
                    {"total_rate": pw_rate.total_rate},
                )
                compliance_status = compliance.status

    record = PayrollRecord(
        project_id=project_id,
        worker_name=record_data["worker_name"],
        worker_id=record_data.get("worker_id"),
        trade=record_data.get("trade", ""),
        classification=record_data.get("classification", ""),
        pay_period_start=record_data["pay_period_start"],
        pay_period_end=record_data["pay_period_end"],
        hours_straight=hours_st,
        hours_overtime=hours_ot,
        hours_other=hours_other,
        rate_straight=rate_st,
        rate_overtime=rate_ot,
        gross_pay=gross,
        deductions=deductions,
        net_pay=net_pay,
        fringe_benefits=fringe_dict,
        prevailing_wage_rate_id=prevailing_wage_rate_id,
        compliance_status=compliance_status,
        created_by=record_data.get("created_by"),
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record


async def create_payroll_batch(
    db: AsyncSession,
    project_id: uuid.UUID,
    records: list[dict],
) -> list[PayrollRecord]:
    """Create multiple payroll records in a batch.

    Each record is created independently so one failure does not block others.

    Args:
        db: Database session.
        project_id: UUID of the project.
        records: List of record dicts.

    Returns:
        List of created PayrollRecord objects.
    """
    created = []
    for record_data in records:
        record = await create_payroll_record(db, project_id, record_data)
        created.append(record)
    return created


# ---------------------------------------------------------------------------
# Certified payroll report generation
# ---------------------------------------------------------------------------


async def generate_certified_report(
    db: AsyncSession,
    project_id: uuid.UUID,
    period_start: date,
    period_end: date,
    contractor_info: dict,
) -> CertifiedPayrollReport:
    """Generate a WH-347 certified payroll report for a project period.

    Fetches all payroll records for the period, generates WH-347 data,
    and persists the report.

    Args:
        db: Database session.
        project_id: UUID of the project.
        period_start: Start of pay period.
        period_end: End of pay period.
        contractor_info: Dict with name, address, signer_name, signer_title.

    Returns:
        Created CertifiedPayrollReport.
    """
    # Fetch project info
    project = await db.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found")

    project_info = {
        "name": project.name,
        "contract_number": contractor_info.get("contract_number", ""),
    }

    # Fetch payroll records for the period
    result = await db.execute(
        select(PayrollRecord)
        .where(
            PayrollRecord.project_id == project_id,
            PayrollRecord.pay_period_start >= period_start,
            PayrollRecord.pay_period_end <= period_end,
        )
        .order_by(PayrollRecord.worker_name)
    )
    records = list(result.scalars().all())

    if not records:
        raise ValueError("No payroll records found for the specified period")

    # Convert ORM records to dicts for the engine
    record_dicts = []
    for r in records:
        record_dicts.append(
            {
                "worker_name": r.worker_name,
                "worker_id": r.worker_id,
                "trade": r.trade,
                "classification": r.classification,
                "hours_straight": r.hours_straight,
                "hours_overtime": r.hours_overtime,
                "hours_other": r.hours_other,
                "rate_straight": r.rate_straight,
                "rate_overtime": r.rate_overtime,
                "gross_pay": r.gross_pay,
                "deductions": r.deductions,
                "net_pay": r.net_pay,
                "fringe_benefits": r.fringe_benefits,
            }
        )

    # Determine next report number
    count_result = await db.execute(
        select(func.count(CertifiedPayrollReport.id)).where(
            CertifiedPayrollReport.project_id == project_id
        )
    )
    report_count = count_result.scalar_one()
    report_number = str(report_count + 1)

    # Add payroll_number to project_info for WH-347
    project_info["payroll_number"] = report_number

    # Generate WH-347 data
    wh347 = generate_wh347_data(
        contractor_info=contractor_info,
        project_info=project_info,
        payroll_records=record_dicts,
        period_start=period_start,
        period_end=period_end,
    )

    # Serialize workers for JSONB storage
    serialized_workers = []
    for w in wh347.workers:
        serialized = {}
        for k, v in w.items():
            if isinstance(v, Decimal):
                serialized[k] = str(v)
            else:
                serialized[k] = v
        serialized_workers.append(serialized)

    report = CertifiedPayrollReport(
        project_id=project_id,
        report_number=report_number,
        pay_period_start=period_start,
        pay_period_end=period_end,
        contractor_name=wh347.contractor_name,
        contractor_address=wh347.contractor_address,
        project_name=wh347.project_name,
        contract_number=wh347.contract_number,
        payroll_records=serialized_workers,
        total_gross_pay=wh347.total_gross,
        total_fringe=wh347.total_fringe,
        status="draft",
    )
    db.add(report)
    await db.flush()
    await db.refresh(report)
    return report


async def certify_report(
    db: AsyncSession,
    report_id: uuid.UUID,
    certified_by: uuid.UUID,
) -> CertifiedPayrollReport:
    """Certify a payroll report (mark as officially signed).

    Args:
        db: Database session.
        report_id: UUID of the report.
        certified_by: UUID of the certifying user.

    Returns:
        Updated CertifiedPayrollReport.

    Raises:
        ValueError: If report not found or already certified.
    """
    report = await db.get(CertifiedPayrollReport, report_id)
    if report is None:
        raise ValueError("Report not found")
    if report.status == "certified":
        raise ValueError("Report is already certified")
    if report.status == "submitted":
        raise ValueError("Report is already submitted")

    report.status = "certified"
    report.certified_by = certified_by
    report.certified_at = datetime.now(UTC)

    await db.flush()
    await db.refresh(report)
    return report


# ---------------------------------------------------------------------------
# List / query functions
# ---------------------------------------------------------------------------


async def list_payroll_records(
    db: AsyncSession,
    project_id: uuid.UUID,
    period_start: date | None = None,
    period_end: date | None = None,
    trade: str | None = None,
) -> list[PayrollRecord]:
    """List payroll records for a project with optional filters.

    Args:
        db: Database session.
        project_id: UUID of the project.
        period_start: Filter by period start >= this date.
        period_end: Filter by period end <= this date.
        trade: Filter by trade classification.

    Returns:
        List of PayrollRecord.
    """
    query = (
        select(PayrollRecord)
        .where(PayrollRecord.project_id == project_id)
        .order_by(PayrollRecord.pay_period_start.desc(), PayrollRecord.worker_name)
    )

    if period_start:
        query = query.where(PayrollRecord.pay_period_start >= period_start)
    if period_end:
        query = query.where(PayrollRecord.pay_period_end <= period_end)
    if trade:
        query = query.where(PayrollRecord.trade == trade)

    result = await db.execute(query)
    return list(result.scalars().all())


async def list_reports(
    db: AsyncSession,
    project_id: uuid.UUID,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[CertifiedPayrollReport], int]:
    """List certified payroll reports for a project.

    Args:
        db: Database session.
        project_id: UUID of the project.
        skip: Number of records to skip.
        limit: Maximum records to return.

    Returns:
        Tuple of (reports list, total count).
    """
    count_result = await db.execute(
        select(func.count(CertifiedPayrollReport.id)).where(
            CertifiedPayrollReport.project_id == project_id
        )
    )
    total = count_result.scalar_one()

    result = await db.execute(
        select(CertifiedPayrollReport)
        .where(CertifiedPayrollReport.project_id == project_id)
        .order_by(CertifiedPayrollReport.pay_period_start.desc())
        .offset(skip)
        .limit(limit)
    )
    reports = list(result.scalars().all())
    return reports, total


async def get_compliance_summary(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> ComplianceSummary:
    """Get prevailing wage compliance summary for a project.

    Args:
        db: Database session.
        project_id: UUID of the project.

    Returns:
        ComplianceSummary with compliance rates and underpayment totals.
    """
    result = await db.execute(select(PayrollRecord).where(PayrollRecord.project_id == project_id))
    records = list(result.scalars().all())

    total = len(records)
    compliant = 0
    underpayment = 0
    review = 0
    total_underpayment_amount = ZERO
    trades_with_issues: set[str] = set()

    for rec in records:
        if rec.compliance_status == "compliant":
            compliant += 1
        elif rec.compliance_status == "underpayment":
            underpayment += 1
            trades_with_issues.add(rec.trade)
            # Calculate underpayment amount if prevailing wage rate is linked
            if rec.prevailing_wage_rate_id:
                pw_rate = await db.get(PrevailingWageRate, rec.prevailing_wage_rate_id)
                if pw_rate:
                    compliance = check_prevailing_wage_compliance(
                        {
                            "rate_straight": rec.rate_straight,
                            "fringe_benefits": rec.fringe_benefits,
                            "hours_straight": rec.hours_straight,
                            "hours_overtime": rec.hours_overtime,
                            "hours_other": rec.hours_other,
                        },
                        {"total_rate": pw_rate.total_rate},
                    )
                    total_underpayment_amount += compliance.total_shortfall
        else:
            review += 1

    compliance_rate = (
        _round2(Decimal(compliant) / Decimal(total) * Decimal("100")) if total > 0 else ZERO
    )

    return ComplianceSummary(
        total_records=total,
        compliant_records=compliant,
        underpayment_records=underpayment,
        review_records=review,
        compliance_rate=compliance_rate,
        total_underpayment=_round2(total_underpayment_amount),
        trades_with_issues=sorted(trades_with_issues),
    )
