"""WageGuard: Davis-Bacon prevailing wage compliance service.

Core business logic for certified payroll (WH-347) generation,
apprenticeship tracking, classification mapping, and audit packaging.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wage_compliance import (
    CertifiedPayrollV2,
    PayrollLineItemV2,
    ProjectWageConfig,
    WageDetermination,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known Davis-Bacon classifications for fuzzy matching fallback
# ---------------------------------------------------------------------------
KNOWN_CLASSIFICATIONS = [
    "Carpenter",
    "Electrician",
    "Ironworker",
    "Laborer",
    "Painter",
    "Plumber",
    "Roofer",
    "Sheet Metal Worker",
    "Operating Engineer",
    "Cement Mason",
    "Pipefitter",
    "Boilermaker",
    "Bricklayer",
    "Glazier",
    "Insulator",
    "Millwright",
    "Tile Setter",
    "Drywall Finisher",
    "Elevator Constructor",
    "Sprinkler Fitter",
]

# ---------------------------------------------------------------------------
# Seed data: sample wage determinations for demo / testing
# ---------------------------------------------------------------------------
SEED_DETERMINATIONS = [
    {
        "sam_gov_id": "VA20240001",
        "state": "VA",
        "county": "Fairfax",
        "project_type": "building",
        "classifications": [
            {
                "title": "Carpenter",
                "base_rate": 28.50,
                "fringe_rate": 15.20,
                "total_rate": 43.70,
            },
            {
                "title": "Electrician",
                "base_rate": 35.00,
                "fringe_rate": 18.50,
                "total_rate": 53.50,
            },
            {
                "title": "Ironworker",
                "base_rate": 32.00,
                "fringe_rate": 22.10,
                "total_rate": 54.10,
            },
            {
                "title": "Laborer",
                "base_rate": 18.50,
                "fringe_rate": 12.00,
                "total_rate": 30.50,
            },
            {
                "title": "Painter",
                "base_rate": 24.00,
                "fringe_rate": 13.50,
                "total_rate": 37.50,
            },
            {
                "title": "Plumber",
                "base_rate": 36.50,
                "fringe_rate": 19.80,
                "total_rate": 56.30,
            },
            {
                "title": "Roofer",
                "base_rate": 26.00,
                "fringe_rate": 14.00,
                "total_rate": 40.00,
            },
            {
                "title": "Sheet Metal Worker",
                "base_rate": 33.00,
                "fringe_rate": 17.50,
                "total_rate": 50.50,
            },
            {
                "title": "Operating Engineer",
                "base_rate": 30.00,
                "fringe_rate": 20.00,
                "total_rate": 50.00,
            },
            {
                "title": "Cement Mason",
                "base_rate": 27.00,
                "fringe_rate": 14.80,
                "total_rate": 41.80,
            },
        ],
    },
    {
        "sam_gov_id": "CA20240001",
        "state": "CA",
        "county": "Los Angeles",
        "project_type": "building",
        "classifications": [
            {
                "title": "Carpenter",
                "base_rate": 46.00,
                "fringe_rate": 25.00,
                "total_rate": 71.00,
            },
            {
                "title": "Electrician",
                "base_rate": 52.00,
                "fringe_rate": 28.00,
                "total_rate": 80.00,
            },
            {
                "title": "Ironworker",
                "base_rate": 48.00,
                "fringe_rate": 30.00,
                "total_rate": 78.00,
            },
            {
                "title": "Laborer",
                "base_rate": 32.00,
                "fringe_rate": 20.00,
                "total_rate": 52.00,
            },
            {
                "title": "Plumber",
                "base_rate": 55.00,
                "fringe_rate": 30.00,
                "total_rate": 85.00,
            },
        ],
    },
    {
        "sam_gov_id": "TX20240001",
        "state": "TX",
        "county": "Harris",
        "project_type": "building",
        "classifications": [
            {
                "title": "Carpenter",
                "base_rate": 22.00,
                "fringe_rate": 10.00,
                "total_rate": 32.00,
            },
            {
                "title": "Electrician",
                "base_rate": 28.00,
                "fringe_rate": 14.00,
                "total_rate": 42.00,
            },
            {
                "title": "Laborer",
                "base_rate": 15.00,
                "fringe_rate": 8.00,
                "total_rate": 23.00,
            },
            {
                "title": "Plumber",
                "base_rate": 30.00,
                "fringe_rate": 16.00,
                "total_rate": 46.00,
            },
            {
                "title": "Operating Engineer",
                "base_rate": 25.00,
                "fringe_rate": 15.00,
                "total_rate": 40.00,
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Wage determination search + seeding
# ---------------------------------------------------------------------------


async def search_determinations(
    db: AsyncSession,
    *,
    state: str | None = None,
    county: str | None = None,
    project_type: str | None = None,
) -> list[WageDetermination]:
    """Search wage determinations with optional filters."""
    query = select(WageDetermination)
    if state:
        query = query.where(WageDetermination.state == state)
    if county:
        query = query.where(WageDetermination.county == county)
    if project_type:
        query = query.where(WageDetermination.project_type == project_type)
    result = await db.execute(query)
    return list(result.scalars().all())


async def seed_determinations(db: AsyncSession) -> int:
    """Insert seed wage determinations if they do not already exist.

    Returns the number of newly inserted determinations.
    """
    inserted = 0
    for seed in SEED_DETERMINATIONS:
        existing = await db.execute(
            select(WageDetermination).where(WageDetermination.sam_gov_id == seed["sam_gov_id"])
        )
        if existing.scalar_one_or_none() is not None:
            continue
        wd = WageDetermination(
            sam_gov_id=seed["sam_gov_id"],
            state=seed["state"],
            county=seed["county"],
            project_type=seed["project_type"],
            classifications=seed["classifications"],
        )
        db.add(wd)
        inserted += 1
    if inserted:
        await db.flush()
    return inserted


# ---------------------------------------------------------------------------
# Project configuration
# ---------------------------------------------------------------------------


async def configure_project(
    db: AsyncSession,
    project_id,
    org_id,
    config_data: dict,
) -> ProjectWageConfig:
    """Create or update the wage configuration for a project (upsert)."""
    result = await db.execute(
        select(ProjectWageConfig).where(ProjectWageConfig.project_id == project_id)
    )
    config = result.scalar_one_or_none()

    if config is None:
        config = ProjectWageConfig(
            project_id=project_id,
            organization_id=org_id,
        )
        db.add(config)

    for field, value in config_data.items():
        if value is not None and hasattr(config, field):
            setattr(config, field, value)

    await db.flush()
    await db.refresh(config)
    return config


# ---------------------------------------------------------------------------
# Payroll creation + line items
# ---------------------------------------------------------------------------


async def create_payroll(
    db: AsyncSession,
    project_id,
    org_id,
    contractor_name: str,
    week_ending,
) -> CertifiedPayrollV2:
    """Create a new certified payroll with an atomic payroll number."""
    # Atomic payroll_number: max(payroll_number) + 1 per contractor per project
    max_result = await db.execute(
        select(func.coalesce(func.max(CertifiedPayrollV2.payroll_number), 0)).where(
            CertifiedPayrollV2.project_id == project_id,
            CertifiedPayrollV2.contractor_name == contractor_name,
        )
    )
    next_number = (max_result.scalar() or 0) + 1

    payroll = CertifiedPayrollV2(
        project_id=project_id,
        organization_id=org_id,
        contractor_name=contractor_name,
        week_ending=week_ending,
        payroll_number=next_number,
    )
    db.add(payroll)
    await db.flush()
    await db.refresh(payroll)
    return payroll


async def add_line_item(
    db: AsyncSession,
    payroll_id,
    project_id,
    data: dict,
) -> PayrollLineItemV2:
    """Add a worker line item, looking up prevailing rates and computing compliance."""
    # Look up the wage determination for this project
    config_result = await db.execute(
        select(ProjectWageConfig).where(ProjectWageConfig.project_id == project_id)
    )
    config = config_result.scalar_one_or_none()

    prevailing_rate: Decimal | None = None
    prevailing_fringe: Decimal | None = None

    if config and config.wage_determination_id:
        wd = await db.get(WageDetermination, config.wage_determination_id)
        if wd and data.get("classification"):
            classification_lower = data["classification"].lower()
            for c in wd.classifications:
                if c["title"].lower() == classification_lower:
                    prevailing_rate = Decimal(str(c["base_rate"]))
                    prevailing_fringe = Decimal(str(c["fringe_rate"]))
                    break

    rate_paid = Decimal(str(data.get("rate_paid", 0)))
    fringe_paid = Decimal(str(data.get("fringe_paid", 0)))

    # Compliance: total paid >= total prevailing
    compliant: bool | None = None
    deficiency = Decimal("0")

    if prevailing_rate is not None and prevailing_fringe is not None:
        total_prevailing = prevailing_rate + prevailing_fringe
        total_paid = rate_paid + fringe_paid
        compliant = total_paid >= total_prevailing
        if not compliant:
            hours_straight = Decimal(str(data.get("hours_straight", 0)))
            hours_overtime = Decimal(str(data.get("hours_overtime", 0)))
            total_hours = hours_straight + hours_overtime
            deficiency = (total_prevailing - total_paid) * total_hours

    line_item = PayrollLineItemV2(
        payroll_id=payroll_id,
        worker_name=data["worker_name"],
        worker_last4_ssn_encrypted=data.get("worker_last4_ssn"),
        classification=data.get("classification"),
        is_apprentice=data.get("is_apprentice", False),
        apprentice_program=data.get("apprentice_program"),
        hours_straight=Decimal(str(data.get("hours_straight", 0))),
        hours_overtime=Decimal(str(data.get("hours_overtime", 0))),
        rate_paid=rate_paid,
        fringe_paid=fringe_paid,
        prevailing_rate=prevailing_rate,
        prevailing_fringe=prevailing_fringe,
        compliant=compliant,
        deficiency_amount=deficiency,
    )
    db.add(line_item)
    await db.flush()

    # Update payroll totals
    payroll = await db.get(CertifiedPayrollV2, payroll_id)
    if payroll:
        total_hours = (line_item.hours_straight or Decimal("0")) + (
            line_item.hours_overtime or Decimal("0")
        )
        payroll.total_hours = (payroll.total_hours or Decimal("0")) + total_hours
        gross = rate_paid * total_hours
        payroll.total_gross_pay = (payroll.total_gross_pay or Decimal("0")) + gross
        await db.flush()

    await db.refresh(line_item)
    return line_item


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def validate_payroll(
    db: AsyncSession,
    payroll_id,
    project_id,
) -> dict:
    """Validate all line items in a payroll for Davis-Bacon compliance.

    Returns a dict with ``payroll_id``, ``compliant`` bool, and ``flags`` list.
    Updates the payroll's compliance_flags and sets status to 'flagged' if
    there are errors.
    """
    result = await db.execute(
        select(PayrollLineItemV2).where(PayrollLineItemV2.payroll_id == payroll_id)
    )
    line_items = list(result.scalars().all())

    flags: list[dict] = []

    for li in line_items:
        if li.compliant is False:
            flags.append(
                {
                    "type": "underpayment",
                    "description": (
                        f"{li.worker_name} ({li.classification}): "
                        f"paid ${li.rate_paid}+${li.fringe_paid} vs "
                        f"prevailing ${li.prevailing_rate}+${li.prevailing_fringe}"
                    ),
                    "severity": "error",
                }
            )
        if li.classification and li.prevailing_rate is None:
            flags.append(
                {
                    "type": "unmapped_classification",
                    "description": (
                        f"{li.worker_name}: classification '{li.classification}' "
                        f"not found in wage determination"
                    ),
                    "severity": "warning",
                }
            )

    # Check apprenticeship requirements
    config_result = await db.execute(
        select(ProjectWageConfig).where(ProjectWageConfig.project_id == project_id)
    )
    config = config_result.scalar_one_or_none()

    if config and config.apprenticeship_required:
        has_apprentice = any(li.is_apprentice for li in line_items)
        if not has_apprentice and line_items:
            flags.append(
                {
                    "type": "no_apprentice_hours",
                    "description": ("Apprenticeship is required but no apprentice hours reported"),
                    "severity": "warning",
                }
            )

    payroll = await db.get(CertifiedPayrollV2, payroll_id)
    if payroll:
        payroll.compliance_flags = flags
        has_errors = any(f["severity"] == "error" for f in flags)
        if has_errors:
            payroll.status = "flagged"
        await db.flush()

    compliant = len(flags) == 0 or not any(f["severity"] == "error" for f in flags)
    return {
        "payroll_id": payroll_id,
        "compliant": compliant,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS = {
    "draft": {"submitted"},
    "flagged": {"submitted"},
    "submitted": {"reviewed", "accepted", "rejected"},
    "reviewed": {"accepted", "rejected"},
}


async def update_payroll_status(
    db: AsyncSession,
    payroll_id,
    project_id,
    status: str,
    user_id=None,
    notes: str | None = None,
) -> CertifiedPayrollV2:
    """Transition payroll status with validation."""
    payroll = await db.get(CertifiedPayrollV2, payroll_id)
    if payroll is None:
        raise ValueError(f"Payroll {payroll_id} not found")

    allowed = _VALID_TRANSITIONS.get(payroll.status, set())
    if status not in allowed:
        raise ValueError(
            f"Invalid transition from '{payroll.status}' to '{status}'. "
            f"Allowed: {sorted(allowed) if allowed else 'none'}"
        )

    now = datetime.now(UTC)
    payroll.status = status

    if status == "submitted":
        payroll.submitted_at = now
    elif status in ("reviewed", "accepted", "rejected"):
        payroll.reviewed_by = user_id
        payroll.reviewed_at = now
        payroll.review_notes = notes

    await db.flush()
    await db.refresh(payroll)
    return payroll


# ---------------------------------------------------------------------------
# Payroll listing
# ---------------------------------------------------------------------------


async def list_payrolls(
    db: AsyncSession,
    project_id,
    contractor_id=None,
) -> list[CertifiedPayrollV2]:
    """List payrolls for a project, optionally filtered by contractor."""
    query = select(CertifiedPayrollV2).where(CertifiedPayrollV2.project_id == project_id)
    if contractor_id:
        query = query.where(CertifiedPayrollV2.contractor_id == contractor_id)
    query = query.order_by(CertifiedPayrollV2.payroll_number.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Apprenticeship status
# ---------------------------------------------------------------------------


async def get_apprenticeship_status(
    db: AsyncSession,
    project_id,
) -> dict:
    """Calculate apprenticeship compliance from all payroll line items."""
    # Get all payrolls for this project
    payroll_result = await db.execute(
        select(CertifiedPayrollV2.id).where(CertifiedPayrollV2.project_id == project_id)
    )
    payroll_ids = [row[0] for row in payroll_result.all()]

    if not payroll_ids:
        return {
            "total_labor_hours": 0.0,
            "apprentice_hours": 0.0,
            "apprentice_pct": 0.0,
            "required_pct": 0.15,
            "compliant": False,
            "hours_deficit": 0.0,
            "projected_compliance_date": None,
        }

    # Sum hours across all line items
    total_result = await db.execute(
        select(
            func.coalesce(
                func.sum(PayrollLineItemV2.hours_straight + PayrollLineItemV2.hours_overtime),
                0,
            )
        ).where(PayrollLineItemV2.payroll_id.in_(payroll_ids))
    )
    total_hours = float(total_result.scalar() or 0)

    apprentice_result = await db.execute(
        select(
            func.coalesce(
                func.sum(PayrollLineItemV2.hours_straight + PayrollLineItemV2.hours_overtime),
                0,
            )
        ).where(
            PayrollLineItemV2.payroll_id.in_(payroll_ids),
            PayrollLineItemV2.is_apprentice.is_(True),
        )
    )
    apprentice_hours = float(apprentice_result.scalar() or 0)

    # Get required percentage from config
    config_result = await db.execute(
        select(ProjectWageConfig).where(ProjectWageConfig.project_id == project_id)
    )
    config = config_result.scalar_one_or_none()
    required_pct = float(config.apprenticeship_pct or 0.15) if config else 0.15

    apprentice_pct = apprentice_hours / total_hours if total_hours > 0 else 0.0
    compliant = apprentice_pct >= required_pct
    hours_deficit = max(0.0, (required_pct * total_hours) - apprentice_hours)

    return {
        "total_labor_hours": total_hours,
        "apprentice_hours": apprentice_hours,
        "apprentice_pct": round(apprentice_pct, 4),
        "required_pct": required_pct,
        "compliant": compliant,
        "hours_deficit": round(hours_deficit, 2),
        "projected_compliance_date": None,
    }


# ---------------------------------------------------------------------------
# Classification mapping
# ---------------------------------------------------------------------------


async def map_classification(
    company_classification: str,
    project_type: str = "building",
) -> dict:
    """Map a company-specific classification to a Davis-Bacon classification.

    Attempts LLM-based mapping first (Haiku), falls back to fuzzy string
    matching against known classifications.
    """
    # Try LLM mapping
    try:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-3-5-haiku-latest",
            max_tokens=100,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Map this construction classification to the closest "
                        f"Davis-Bacon classification for a {project_type} project. "
                        f"Company classification: '{company_classification}'. "
                        f"Reply with ONLY the Davis-Bacon classification name, "
                        f"nothing else."
                    ),
                }
            ],
        )
        block = response.content[0]
        suggested = (block.text if hasattr(block, "text") else str(block)).strip()
        return {"suggested_davis_bacon": suggested, "confidence": 0.85}
    except Exception:
        logger.debug("LLM classification mapping unavailable, using fuzzy match")

    # Fallback: fuzzy string matching
    best_match = ""
    best_score = 0.0

    for known in KNOWN_CLASSIFICATIONS:
        score = SequenceMatcher(
            None,
            company_classification.lower(),
            known.lower(),
        ).ratio()
        if score > best_score:
            best_score = score
            best_match = known

    return {
        "suggested_davis_bacon": best_match,
        "confidence": round(best_score, 2),
    }


# ---------------------------------------------------------------------------
# Audit package
# ---------------------------------------------------------------------------


async def generate_audit_package(
    db: AsyncSession,
    project_id,
) -> dict:
    """Aggregate all wage compliance data for a project into an audit package."""
    payrolls = await list_payrolls(db, project_id)
    payroll_count = len(payrolls)

    # Count line items
    payroll_ids = [p.id for p in payrolls]
    total_line_items = 0
    if payroll_ids:
        li_result = await db.execute(
            select(func.count(PayrollLineItemV2.id)).where(
                PayrollLineItemV2.payroll_id.in_(payroll_ids)
            )
        )
        total_line_items = li_result.scalar() or 0

    # Count compliance issues
    compliance_issues = 0
    for p in payrolls:
        if p.compliance_flags:
            compliance_issues += sum(1 for f in p.compliance_flags if f.get("severity") == "error")

    # Count unique contractors
    contractor_names = {p.contractor_name for p in payrolls if p.contractor_name}
    sub_count = len(contractor_names)

    apprenticeship_status = await get_apprenticeship_status(db, project_id)

    return {
        "payroll_count": payroll_count,
        "total_line_items": total_line_items,
        "apprenticeship_status": apprenticeship_status,
        "compliance_issues": compliance_issues,
        "sub_count": sub_count,
    }
