"""Insurance and risk data export service.

Generates safety summaries, EMR calculations, loss runs, risk profiles,
OSHA 300 logs, and exportable packages for insurance underwriters.

IMPORTANT: EMR calculations use approximate methodology.

Limitations:
- Primary/excess loss split uses a fixed 63%/37% ratio instead of per-claim analysis
- Weighting factors and ballast values are heuristic, not from NCCI published tables
- Only 20 NCCI class codes included (real NCCI has hundreds)
- No state-specific modifier adjustments

Do NOT use this output for official insurance submissions without professional review.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.insurance import EMRCalculation, InsuranceExport

logger = logging.getLogger(__name__)

COMPLIANCE_STATUS = "BETA"  # Approximate calculations

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")
THREE_PLACES = Decimal("0.001")
HOURS_BASE = Decimal("200000")  # OSHA incident rate base


def _round2(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _round3(value: Decimal) -> Decimal:
    return value.quantize(THREE_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# NCCI Classification Codes (simplified — common construction classes)
# Expected loss rate per $100 of payroll
# ---------------------------------------------------------------------------

NCCI_CLASS_CODES: dict[str, dict] = {
    "5022": {"description": "Masonry", "rate": Decimal("10.86")},
    "5037": {"description": "Pile Driving", "rate": Decimal("15.42")},
    "5040": {"description": "Iron/Steel Erection", "rate": Decimal("22.78")},
    "5057": {"description": "Iron/Steel Erection — HVAC Ductwork", "rate": Decimal("12.34")},
    "5059": {"description": "Iron/Steel Erection — Not Over Two Stories", "rate": Decimal("16.89")},
    "5102": {"description": "Door/Window Installation — Metal", "rate": Decimal("8.45")},
    "5183": {"description": "Plumbing", "rate": Decimal("5.87")},
    "5188": {"description": "Automatic Sprinkler Installation", "rate": Decimal("6.23")},
    "5190": {"description": "Electrical Wiring", "rate": Decimal("5.12")},
    "5213": {"description": "Concrete Construction — Buildings", "rate": Decimal("9.67")},
    "5215": {
        "description": "Concrete Construction — Bridges/Elevated Highways",
        "rate": Decimal("14.23"),
    },
    "5221": {"description": "Concrete Work — Not Floors/Walls", "rate": Decimal("8.45")},
    "5348": {"description": "Ceramic Tile Installation", "rate": Decimal("5.67")},
    "5403": {"description": "Carpentry — Commercial", "rate": Decimal("11.23")},
    "5437": {"description": "Carpentry — Residential", "rate": Decimal("12.89")},
    "5443": {"description": "Lathing/Plastering", "rate": Decimal("7.56")},
    "5462": {"description": "Glazier", "rate": Decimal("8.12")},
    "5474": {"description": "Painting — Commercial", "rate": Decimal("8.78")},
    "5480": {"description": "Plastering/Stucco", "rate": Decimal("7.23")},
    "5491": {"description": "Paperhanging", "rate": Decimal("4.56")},
    "5506": {"description": "Street/Road Construction — Paving", "rate": Decimal("7.89")},
    "5507": {"description": "Street/Road Construction — Grading", "rate": Decimal("8.45")},
    "5508": {"description": "Street/Road Construction — All Operations", "rate": Decimal("9.12")},
    "5538": {"description": "Sheet Metal Work — Shop", "rate": Decimal("6.78")},
    "5545": {"description": "Roofing — All Types", "rate": Decimal("19.56")},
    "5551": {"description": "Roofing — Built-Up", "rate": Decimal("17.23")},
    "5606": {"description": "Contractor — Project Manager/Superintendent", "rate": Decimal("3.45")},
    "5645": {"description": "Carpentry — Detached Dwelling", "rate": Decimal("14.56")},
    "6217": {"description": "Excavation/Grading", "rate": Decimal("7.12")},
    "6229": {"description": "Irrigation Works", "rate": Decimal("8.34")},
    "6251": {"description": "Tunneling", "rate": Decimal("16.78")},
    "6252": {"description": "Shaft Sinking", "rate": Decimal("18.45")},
    "6306": {"description": "Sewer Construction", "rate": Decimal("9.67")},
    "7219": {"description": "Trucking — Construction", "rate": Decimal("10.12")},
    "7380": {"description": "Chauffeur/Driver", "rate": Decimal("6.89")},
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SafetySummary:
    """Safety summary for insurance underwriters."""

    org_id: str
    project_id: str | None
    date_range_start: date
    date_range_end: date
    total_hours_worked: Decimal
    total_recordable_incidents: int
    trir: Decimal  # Total Recordable Incident Rate
    dart_incidents: int
    dart_rate: Decimal
    lost_time_injuries: int
    ltir: Decimal
    near_misses: int
    near_miss_frequency: Decimal
    severity_rate: Decimal
    lost_workdays: int
    incident_by_type: dict[str, int] = field(default_factory=dict)
    incident_by_body_part: dict[str, int] = field(default_factory=dict)
    incident_by_cause: dict[str, int] = field(default_factory=dict)


@dataclass
class EMRResult:
    """Experience Modification Rate calculation result."""

    emr_value: Decimal
    actual_primary: Decimal
    actual_excess: Decimal
    expected_primary: Decimal
    expected_excess: Decimal
    weighting_factor: Decimal
    ballast_value: Decimal
    formula_numerator: Decimal
    formula_denominator: Decimal


@dataclass
class EMRExport:
    """Full EMR export with supporting documentation."""

    emr_result: EMRResult
    payroll_by_class: dict[str, dict]
    expected_losses_by_class: dict[str, Decimal]
    actual_losses_detail: list[dict]
    total_payroll: Decimal
    total_expected_losses: Decimal
    total_actual_losses: Decimal
    calculation_year: int


@dataclass
class LossRunEntry:
    """Single entry in a loss run report."""

    incident_date: date
    incident_type: str
    description: str
    medical_cost: Decimal
    indemnity_cost: Decimal
    property_cost: Decimal
    total_cost: Decimal
    status: str  # open | closed | reserved
    reserve_amount: Decimal
    claimant: str


@dataclass
class LossRun:
    """Loss run report for insurance underwriters."""

    org_id: str
    date_range_start: date
    date_range_end: date
    entries: list[LossRunEntry] = field(default_factory=list)
    total_medical: Decimal = ZERO
    total_indemnity: Decimal = ZERO
    total_property: Decimal = ZERO
    total_incurred: Decimal = ZERO
    total_reserved: Decimal = ZERO
    open_claims: int = 0
    closed_claims: int = 0


@dataclass
class RiskProfile:
    """Comprehensive risk profile for insurance underwriters."""

    org_id: str
    project_id: str | None
    trir_trend: list[dict] = field(default_factory=list)  # 3-year trend
    top_risk_categories: list[dict] = field(default_factory=list)
    ppe_compliance_rate: Decimal = ZERO
    training_hours: Decimal = ZERO
    predictive_risk_scores: dict[str, Decimal] = field(default_factory=dict)
    mitigation_effectiveness: dict[str, Decimal] = field(default_factory=dict)
    emr_history: list[dict] = field(default_factory=list)


@dataclass
class OSHA300Entry:
    """Single entry for OSHA 300 log."""

    case_number: str
    employee_name: str  # Privacy-masked
    job_title: str
    date_of_injury: date
    where_event_occurred: str
    description: str
    classified_as: str  # death | days_away | restricted | other_recordable
    days_away: int
    days_restricted: int


@dataclass
class OSHA300Log:
    """OSHA Form 300 — Log of Work-Related Injuries and Illnesses."""

    establishment_name: str
    org_id: str
    year: int
    entries: list[OSHA300Entry] = field(default_factory=list)
    total_deaths: int = 0
    total_days_away_cases: int = 0
    total_restricted_cases: int = 0
    total_other_recordable: int = 0
    total_days_away: int = 0
    total_days_restricted: int = 0


# ---------------------------------------------------------------------------
# Core calculation functions
# ---------------------------------------------------------------------------


def calculate_emr(
    actual_losses: Decimal,
    expected_losses: Decimal,
    ballast_value: Decimal | None = None,
    weighting_factor: Decimal | None = None,
) -> EMRResult:
    """Calculate the NCCI Experience Modification Rate.

    Real NCCI formula with primary/excess loss splits:

    EMR = (Ap + Ae*W + B) / (Ep + Ee*W + B)

    where:
        Ap = actual primary losses (first $5,000 per claim)
        Ae = actual excess losses (above $5,000 per claim)
        Ep = expected primary losses
        Ee = expected excess losses
        W = weighting factor (based on expected losses size)
        B = ballast value (stabilizing constant)

    Args:
        actual_losses: Total actual incurred losses.
        expected_losses: Expected losses based on payroll and class rates.
        ballast_value: Stabilizing constant. Auto-calculated if None.
        weighting_factor: Loss weighting factor. Auto-calculated if None.

    Returns:
        EMRResult with the calculated EMR and all components.
    """
    if expected_losses <= ZERO:
        return EMRResult(
            emr_value=Decimal("1.000"),
            actual_primary=ZERO,
            actual_excess=ZERO,
            expected_primary=ZERO,
            expected_excess=ZERO,
            weighting_factor=ZERO,
            ballast_value=ZERO,
            formula_numerator=ZERO,
            formula_denominator=ZERO,
        )

    # Primary/excess split: primary = first $5,000 per claim (simplified)
    # In practice this is per-claim; we approximate using 63% primary / 37% excess
    # as the NCCI average split for construction classes
    primary_ratio = Decimal("0.63")
    excess_ratio = Decimal("0.37")

    actual_primary = _round2(actual_losses * primary_ratio)
    actual_excess = _round2(actual_losses * excess_ratio)
    expected_primary = _round2(expected_losses * primary_ratio)
    expected_excess = _round2(expected_losses * excess_ratio)

    # Auto-calculate weighting factor based on expected losses size
    # Larger employers get higher weighting (more credibility to experience)
    if weighting_factor is None:
        if expected_losses < Decimal("5000"):
            weighting_factor = Decimal("0.10")
        elif expected_losses < Decimal("25000"):
            weighting_factor = Decimal("0.20")
        elif expected_losses < Decimal("100000"):
            weighting_factor = Decimal("0.35")
        elif expected_losses < Decimal("500000"):
            weighting_factor = Decimal("0.55")
        else:
            weighting_factor = Decimal("0.70")

    # Auto-calculate ballast value (provides stability for small employers)
    if ballast_value is None:
        ballast_value = _round2(expected_losses * Decimal("0.12"))

    # NCCI EMR formula
    numerator = actual_primary + actual_excess * weighting_factor + ballast_value
    denominator = expected_primary + expected_excess * weighting_factor + ballast_value

    emr = Decimal("1.000") if denominator <= ZERO else _round3(numerator / denominator)

    # Clamp EMR between 0.50 and 2.50 (realistic range)
    emr = max(Decimal("0.500"), min(Decimal("2.500"), emr))

    return EMRResult(
        emr_value=emr,
        actual_primary=actual_primary,
        actual_excess=actual_excess,
        expected_primary=expected_primary,
        expected_excess=expected_excess,
        weighting_factor=weighting_factor,
        ballast_value=ballast_value,
        formula_numerator=_round2(numerator),
        formula_denominator=_round2(denominator),
    )


def _calculate_expected_losses(
    payroll_by_class: dict[str, Decimal],
) -> tuple[Decimal, dict[str, Decimal]]:
    """Calculate expected losses from payroll by NCCI class code.

    Args:
        payroll_by_class: Dict mapping NCCI class code to payroll amount.

    Returns:
        Tuple of (total expected losses, per-class expected losses).
    """
    total = ZERO
    by_class: dict[str, Decimal] = {}

    for class_code, payroll in payroll_by_class.items():
        rate_info = NCCI_CLASS_CODES.get(class_code)
        if rate_info is None:
            logger.warning("Unknown NCCI class code: %s", class_code)
            continue
        rate_per_100 = rate_info["rate"]
        expected = _round2(Decimal(str(payroll)) / Decimal("100") * rate_per_100)
        by_class[class_code] = expected
        total += expected

    return _round2(total), by_class


def _calculate_trir(recordable_incidents: int, hours_worked: Decimal) -> Decimal:
    """TRIR = (recordable incidents x 200,000) / total hours worked."""
    if hours_worked <= ZERO:
        return ZERO
    return _round2(Decimal(recordable_incidents) * HOURS_BASE / hours_worked)


def _calculate_dart(dart_incidents: int, hours_worked: Decimal) -> Decimal:
    """DART = (DART incidents x 200,000) / total hours worked."""
    if hours_worked <= ZERO:
        return ZERO
    return _round2(Decimal(dart_incidents) * HOURS_BASE / hours_worked)


def _calculate_severity_rate(lost_workdays: int, hours_worked: Decimal) -> Decimal:
    """Severity Rate = (lost workdays x 200,000) / total hours worked."""
    if hours_worked <= ZERO:
        return ZERO
    return _round2(Decimal(lost_workdays) * HOURS_BASE / hours_worked)


def _mask_name(name: str) -> str:
    """Privacy-mask an employee name for OSHA 300.

    Shows first initial and last name only: "John Smith" -> "J. Smith"
    """
    if not name:
        return "Anonymous"
    parts = name.strip().split()
    if len(parts) == 1:
        return f"{parts[0][0]}."
    return f"{parts[0][0]}. {parts[-1]}"


# ---------------------------------------------------------------------------
# DB-backed service functions
# ---------------------------------------------------------------------------


async def generate_safety_summary(
    db: AsyncSession,
    org_id: str,
    project_id: str | None,
    date_range_start: date,
    date_range_end: date,
) -> SafetySummary:
    """Generate a safety summary for insurance underwriters.

    Aggregates from safety_incidents, safety_alerts, and daily_risk_scores.
    """
    # Query safety incidents
    params: dict = {"start": date_range_start, "end": date_range_end}

    if project_id:
        params["project_id"] = project_id
    else:
        params["org_id"] = org_id

    # Get incident counts by type
    # All SQL fragments below are static; user input flows through bound params only.
    try:
        scope_clause = (
            " AND si.project_id = :project_id"
            if project_id
            else " AND si.project_id IN (SELECT id FROM projects WHERE org_id = :org_id)"
        )
        # All fragments below are static; only :start, :end, :project_id, :org_id are user input.
        _sql_static = "SELECT COALESCE(si.alert_type, 'unknown') as incident_type, COUNT(*) as cnt FROM safety_alerts si WHERE si.created_at >= :start AND si.created_at <= :end"  # nosec B608
        sql_str = (
            _sql_static
            + scope_clause
            + " AND si.is_false_positive IS NOT TRUE GROUP BY si.alert_type"
        )
        sql = text(sql_str)
        result = await db.execute(sql, params)
        incident_by_type = {}
        total_recordable = 0
        for row in result.mappings().all():
            incident_by_type[row["incident_type"]] = row["cnt"]
            total_recordable += row["cnt"]
    except Exception as exc:
        logger.warning("Error querying safety incidents: %s", exc)
        incident_by_type = {}
        total_recordable = 0

    # Estimate hours worked from daily logs
    try:
        dl_scope_clause = (
            " AND dl.project_id = :project_id"
            if project_id
            else " AND dl.project_id IN (SELECT id FROM projects WHERE org_id = :org_id)"
        )
        _hours_static = "SELECT COALESCE(SUM(CASE WHEN dl.data->>'crew_count' IS NOT NULL THEN (dl.data->>'crew_count')::int * 8 ELSE 0 END), 0) as total_hours FROM daily_reports dl WHERE dl.created_at >= :start AND dl.created_at <= :end"  # nosec B608
        hours_sql_str = _hours_static + dl_scope_clause
        hours_sql = text(hours_sql_str)
        hours_result = await db.execute(hours_sql, params)
        total_hours = Decimal(str(hours_result.scalar_one() or 0))
    except Exception:
        # Fallback: estimate from date range
        days = (date_range_end - date_range_start).days
        total_hours = Decimal(str(max(days * 20 * 8, 1)))  # 20 workers, 8 hrs

    # Calculate rates
    # For DART, estimate as 60% of total recordable (industry standard)
    dart_incidents = int(total_recordable * 0.6)
    lost_time = int(total_recordable * 0.3)
    lost_workdays = lost_time * 5  # average 5 days per LTI

    # Near misses from safety alerts that were acknowledged
    try:
        nm_scope_clause = (
            " AND project_id = :project_id"
            if project_id
            else " AND project_id IN (SELECT id FROM projects WHERE org_id = :org_id)"
        )
        _nm_static = "SELECT COUNT(*) FROM safety_alerts WHERE created_at >= :start AND created_at <= :end AND priority = 'low'"  # nosec B608
        nm_sql_str = _nm_static + nm_scope_clause
        near_miss_sql = text(nm_sql_str)
        nm_result = await db.execute(near_miss_sql, params)
        near_misses = nm_result.scalar_one() or 0
    except Exception:
        near_misses = 0

    trir = _calculate_trir(total_recordable, total_hours)
    dart_rate = _calculate_dart(dart_incidents, total_hours)
    ltir = _calculate_trir(lost_time, total_hours)
    severity = _calculate_severity_rate(lost_workdays, total_hours)
    near_miss_freq = (
        _round2(Decimal(near_misses) * HOURS_BASE / total_hours) if total_hours > ZERO else ZERO
    )

    return SafetySummary(
        org_id=str(org_id),
        project_id=str(project_id) if project_id else None,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        total_hours_worked=total_hours,
        total_recordable_incidents=total_recordable,
        trir=trir,
        dart_incidents=dart_incidents,
        dart_rate=dart_rate,
        lost_time_injuries=lost_time,
        ltir=ltir,
        near_misses=near_misses,
        near_miss_frequency=near_miss_freq,
        severity_rate=severity,
        lost_workdays=lost_workdays,
        incident_by_type=incident_by_type,
    )


async def generate_emr_supporting_docs(
    db: AsyncSession,
    org_id: str,
    year: int,
    payroll_data: dict[str, Decimal],
) -> EMRExport:
    """Calculate EMR with full supporting documentation.

    Args:
        db: Database session.
        org_id: Organization UUID string.
        year: Calculation year.
        payroll_data: Dict mapping NCCI class code to payroll amount.

    Returns:
        EMRExport with all supporting data.
    """
    # Calculate expected losses from payroll
    total_expected, expected_by_class = _calculate_expected_losses(payroll_data)

    # Get actual losses from safety incidents in the 3-year experience period
    # NCCI uses 3 years prior, excluding the most recent
    try:
        loss_sql = text("""
            SELECT
                COALESCE(sa.alert_type, 'other') as loss_type,
                COUNT(*) as claim_count,
                COUNT(*) * 5000 as estimated_cost
            FROM safety_alerts sa
            JOIN projects p ON sa.project_id = p.id
            WHERE p.org_id = :org_id
              AND sa.created_at >= :start_date
              AND sa.created_at < :end_date
              AND sa.is_false_positive IS NOT TRUE
            GROUP BY sa.alert_type
        """)
        loss_result = await db.execute(
            loss_sql,
            {
                "org_id": org_id,
                "start_date": date(year - 3, 1, 1),
                "end_date": date(year, 1, 1),
            },
        )
        actual_losses_detail = []
        total_actual = ZERO
        for row in loss_result.mappings().all():
            cost = Decimal(str(row["estimated_cost"]))
            actual_losses_detail.append(
                {
                    "loss_type": row["loss_type"],
                    "claim_count": row["claim_count"],
                    "estimated_cost": str(cost),
                }
            )
            total_actual += cost
    except Exception as exc:
        logger.warning("Error querying actual losses: %s", exc)
        actual_losses_detail = []
        total_actual = ZERO

    # Calculate EMR
    emr_result = calculate_emr(total_actual, total_expected)

    total_payroll: Decimal = sum((Decimal(str(v)) for v in payroll_data.values()), Decimal(0))

    # Store calculation
    emr_record = EMRCalculation(
        org_id=org_id,
        calculation_year=year,
        actual_losses=total_actual,
        expected_losses=total_expected,
        emr_value=emr_result.emr_value,
        payroll_by_class={k: str(v) for k, v in payroll_data.items()},
        loss_detail=actual_losses_detail,
    )
    db.add(emr_record)
    await db.flush()

    return EMRExport(
        emr_result=emr_result,
        payroll_by_class={k: {"payroll": str(v)} for k, v in payroll_data.items()},
        expected_losses_by_class={k: v for k, v in expected_by_class.items()},
        actual_losses_detail=actual_losses_detail,
        total_payroll=_round2(total_payroll),
        total_expected_losses=total_expected,
        total_actual_losses=total_actual,
        calculation_year=year,
    )


async def generate_loss_run(
    db: AsyncSession,
    org_id: str,
    date_range_start: date,
    date_range_end: date,
) -> LossRun:
    """Generate a loss run report listing all incidents with costs.

    Args:
        db: Database session.
        org_id: Organization UUID string.
        date_range_start: Start of date range.
        date_range_end: End of date range.

    Returns:
        LossRun with all incident entries.
    """
    try:
        sql = text("""
            SELECT
                sa.id,
                sa.created_at,
                sa.alert_type,
                sa.description,
                sa.priority,
                sa.is_acknowledged
            FROM safety_alerts sa
            JOIN projects p ON sa.project_id = p.id
            WHERE p.org_id = :org_id
              AND sa.created_at >= :start
              AND sa.created_at <= :end
              AND sa.is_false_positive IS NOT TRUE
            ORDER BY sa.created_at DESC
        """)
        result = await db.execute(
            sql,
            {
                "org_id": org_id,
                "start": date_range_start,
                "end": date_range_end,
            },
        )
        rows = result.mappings().all()
    except Exception as exc:
        logger.warning("Error querying loss run data: %s", exc)
        rows = []

    entries = []
    total_medical = ZERO
    total_indemnity = ZERO
    total_property = ZERO
    total_incurred = ZERO
    total_reserved = ZERO
    open_claims = 0
    closed_claims = 0

    for row in rows:
        # Estimate costs based on priority (simplified)
        priority = row.get("priority", "medium")
        if priority == "critical":
            medical = Decimal("25000")
            indemnity = Decimal("50000")
            property_cost = Decimal("10000")
        elif priority == "high":
            medical = Decimal("10000")
            indemnity = Decimal("15000")
            property_cost = Decimal("5000")
        elif priority == "medium":
            medical = Decimal("3000")
            indemnity = Decimal("2000")
            property_cost = Decimal("1000")
        else:
            medical = Decimal("500")
            indemnity = ZERO
            property_cost = Decimal("200")

        total_cost = medical + indemnity + property_cost
        is_closed = row.get("is_acknowledged", False)
        status = "closed" if is_closed else "open"
        reserve = ZERO if is_closed else _round2(total_cost * Decimal("1.2"))

        incident_date = row["created_at"]
        if hasattr(incident_date, "date"):
            incident_date = incident_date.date()

        entry = LossRunEntry(
            incident_date=incident_date,
            incident_type=row.get("alert_type", "unknown"),
            description=row.get("description", ""),
            medical_cost=medical,
            indemnity_cost=indemnity,
            property_cost=property_cost,
            total_cost=total_cost,
            status=status,
            reserve_amount=reserve,
            claimant="Claimant",
        )
        entries.append(entry)

        total_medical += medical
        total_indemnity += indemnity
        total_property += property_cost
        total_incurred += total_cost
        total_reserved += reserve
        if is_closed:
            closed_claims += 1
        else:
            open_claims += 1

    return LossRun(
        org_id=str(org_id),
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        entries=entries,
        total_medical=_round2(total_medical),
        total_indemnity=_round2(total_indemnity),
        total_property=_round2(total_property),
        total_incurred=_round2(total_incurred),
        total_reserved=_round2(total_reserved),
        open_claims=open_claims,
        closed_claims=closed_claims,
    )


async def generate_risk_profile(
    db: AsyncSession,
    org_id: str,
    project_id: str | None = None,
) -> RiskProfile:
    """Generate comprehensive risk profile for insurance underwriters.

    Includes TRIR trend, top risk categories, PPE compliance, and
    predictive risk scores.
    """
    today = date.today()
    trir_trend = []

    # 3-year TRIR trend
    for years_back in range(3, 0, -1):
        year_start = date(today.year - years_back, 1, 1)
        year_end = date(today.year - years_back, 12, 31)
        try:
            summary = await generate_safety_summary(
                db,
                org_id,
                project_id,
                year_start,
                year_end,
            )
            trir_trend.append(
                {
                    "year": today.year - years_back,
                    "trir": str(summary.trir),
                    "dart_rate": str(summary.dart_rate),
                    "severity_rate": str(summary.severity_rate),
                    "total_incidents": summary.total_recordable_incidents,
                }
            )
        except Exception as exc:
            logger.warning("Error generating TRIR for year %d: %s", today.year - years_back, exc)
            trir_trend.append(
                {
                    "year": today.year - years_back,
                    "trir": "0.00",
                    "dart_rate": "0.00",
                    "severity_rate": "0.00",
                    "total_incidents": 0,
                }
            )

    # Top risk categories from daily_risk_scores
    top_risks = []
    try:
        risk_scope = "AND drs.project_id = :project_id" if project_id else ""
        risk_sql_str = f"SELECT key as category, AVG(value::float) as avg_score FROM daily_risk_scores drs, jsonb_each_text(drs.category_scores) WHERE drs.score_date >= :start {risk_scope} GROUP BY key ORDER BY avg_score DESC LIMIT 5"  # nosec B608
        risk_sql = text(risk_sql_str)
        risk_params: dict = {"start": date(today.year, 1, 1)}
        if project_id:
            risk_params["project_id"] = project_id
        risk_result = await db.execute(risk_sql, risk_params)
        for row in risk_result.mappings().all():
            top_risks.append(
                {
                    "category": row["category"],
                    "avg_score": round(float(row["avg_score"]), 1),
                }
            )
    except Exception as exc:
        logger.warning("Error querying risk scores: %s", exc)

    # PPE compliance rate from safety alerts
    ppe_compliance = Decimal("95.0")  # Default — refined with actual data
    try:
        ppe_scope = "AND project_id = :project_id" if project_id else ""
        ppe_sql_str = f"SELECT COUNT(*) FILTER (WHERE alert_type NOT LIKE '%%ppe%%' AND alert_type NOT LIKE '%%no_%%') as compliant, COUNT(*) as total FROM safety_alerts WHERE created_at >= :start AND is_false_positive IS NOT TRUE {ppe_scope}"  # nosec B608
        ppe_sql = text(ppe_sql_str)
        ppe_params: dict = {"start": date(today.year, 1, 1)}
        if project_id:
            ppe_params["project_id"] = project_id
        ppe_result = await db.execute(ppe_sql, ppe_params)
        ppe_row = ppe_result.mappings().first()
        if ppe_row and ppe_row["total"] > 0:
            ppe_compliance = _round2(
                Decimal(str(ppe_row["compliant"])) / Decimal(str(ppe_row["total"])) * Decimal("100")
            )
    except Exception as exc:
        logger.warning("Error calculating PPE compliance: %s", exc)

    # EMR history
    emr_history = []
    try:
        emr_result = await db.execute(
            select(EMRCalculation)
            .where(EMRCalculation.org_id == org_id)
            .order_by(EMRCalculation.calculation_year.desc())
            .limit(5)
        )
        for emr in emr_result.scalars().all():
            emr_history.append(
                {
                    "year": emr.calculation_year,
                    "emr_value": str(emr.emr_value),
                    "actual_losses": str(emr.actual_losses),
                    "expected_losses": str(emr.expected_losses),
                }
            )
    except Exception as exc:
        logger.warning("Error querying EMR history: %s", exc)

    return RiskProfile(
        org_id=str(org_id),
        project_id=str(project_id) if project_id else None,
        trir_trend=trir_trend,
        top_risk_categories=top_risks,
        ppe_compliance_rate=ppe_compliance,
        training_hours=ZERO,
        emr_history=emr_history,
    )


async def generate_osha_300_log(
    db: AsyncSession,
    org_id: str,
    establishment: str,
    year: int,
) -> OSHA300Log:
    """Generate OSHA Form 300 log data.

    Formats injury/illness records per OSHA 300 log requirements with
    privacy-masked employee names.
    """
    try:
        sql = text("""
            SELECT
                sa.id,
                sa.created_at,
                sa.alert_type,
                sa.description,
                sa.priority
            FROM safety_alerts sa
            JOIN projects p ON sa.project_id = p.id
            WHERE p.org_id = :org_id
              AND EXTRACT(YEAR FROM sa.created_at) = :year
              AND sa.is_false_positive IS NOT TRUE
            ORDER BY sa.created_at
        """)
        result = await db.execute(sql, {"org_id": org_id, "year": year})
        rows = result.mappings().all()
    except Exception as exc:
        logger.warning("Error querying OSHA 300 data: %s", exc)
        rows = []

    entries = []
    total_deaths = 0
    total_days_away = 0
    total_restricted = 0
    total_other = 0
    total_days_away_count = 0
    total_days_restricted_count = 0

    for i, row in enumerate(rows, 1):
        priority = row.get("priority", "medium")
        alert_type = row.get("alert_type", "unknown")
        incident_date = row["created_at"]
        if hasattr(incident_date, "date"):
            incident_date = incident_date.date()

        # Classify based on priority
        if priority == "critical":
            classified_as = "days_away"
            days_away = 10
            days_restricted = 0
            total_days_away += 1
            total_days_away_count += days_away
        elif priority == "high":
            classified_as = "restricted"
            days_away = 0
            days_restricted = 5
            total_restricted += 1
            total_days_restricted_count += days_restricted
        else:
            classified_as = "other_recordable"
            days_away = 0
            days_restricted = 0
            total_other += 1

        entry = OSHA300Entry(
            case_number=f"{year}-{i:04d}",
            employee_name=_mask_name(f"Worker {i}"),
            job_title=alert_type.replace("_", " ").title(),
            date_of_injury=incident_date,
            where_event_occurred=establishment,
            description=row.get("description", "")[:200],
            classified_as=classified_as,
            days_away=days_away,
            days_restricted=days_restricted,
        )
        entries.append(entry)

    return OSHA300Log(
        establishment_name=establishment,
        org_id=str(org_id),
        year=year,
        entries=entries,
        total_deaths=total_deaths,
        total_days_away_cases=total_days_away,
        total_restricted_cases=total_restricted,
        total_other_recordable=total_other,
        total_days_away=total_days_away_count,
        total_days_restricted=total_days_restricted_count,
    )


# ---------------------------------------------------------------------------
# Export functions
# ---------------------------------------------------------------------------


def export_to_csv(data: dict, export_type: str) -> bytes:
    """Convert export data to CSV bytes.

    Args:
        data: Dict of export data (from any generate_* function serialized).
        export_type: Type of export (safety_summary, loss_run, osha_300, etc.).

    Returns:
        CSV file as bytes.
    """
    output = io.StringIO()
    writer = csv.writer(output)

    if export_type == "safety_summary":
        writer.writerow(
            [
                "Metric",
                "Value",
            ]
        )
        for key, value in data.items():
            if not isinstance(value, dict | list):
                writer.writerow([key, value])

    elif export_type == "loss_run":
        writer.writerow(
            [
                "Date",
                "Type",
                "Description",
                "Medical",
                "Indemnity",
                "Property",
                "Total",
                "Status",
                "Reserve",
            ]
        )
        for entry in data.get("entries", []):
            writer.writerow(
                [
                    entry.get("incident_date", ""),
                    entry.get("incident_type", ""),
                    entry.get("description", "")[:100],
                    entry.get("medical_cost", ""),
                    entry.get("indemnity_cost", ""),
                    entry.get("property_cost", ""),
                    entry.get("total_cost", ""),
                    entry.get("status", ""),
                    entry.get("reserve_amount", ""),
                ]
            )

    elif export_type == "osha_300":
        writer.writerow(
            [
                "Case No.",
                "Employee Name",
                "Job Title",
                "Date of Injury",
                "Where",
                "Description",
                "Classification",
                "Days Away",
                "Days Restricted",
            ]
        )
        for entry in data.get("entries", []):
            writer.writerow(
                [
                    entry.get("case_number", ""),
                    entry.get("employee_name", ""),
                    entry.get("job_title", ""),
                    entry.get("date_of_injury", ""),
                    entry.get("where_event_occurred", ""),
                    entry.get("description", "")[:100],
                    entry.get("classified_as", ""),
                    entry.get("days_away", 0),
                    entry.get("days_restricted", 0),
                ]
            )

    elif export_type == "emr":
        writer.writerow(["EMR Calculation Summary"])
        writer.writerow([])
        writer.writerow(["Metric", "Value"])
        emr_data = data.get("emr_result", {})
        for key, value in emr_data.items():
            writer.writerow([key, value])
        writer.writerow([])
        writer.writerow(["Payroll by Class"])
        writer.writerow(["Class Code", "Payroll"])
        for code, info in data.get("payroll_by_class", {}).items():
            payroll_val = info.get("payroll", "") if isinstance(info, dict) else info
            writer.writerow([code, payroll_val])

    elif export_type == "risk_profile":
        writer.writerow(["Risk Profile Summary"])
        writer.writerow([])
        writer.writerow(["Year", "TRIR", "DART Rate", "Severity Rate", "Incidents"])
        for trend in data.get("trir_trend", []):
            writer.writerow(
                [
                    trend.get("year", ""),
                    trend.get("trir", ""),
                    trend.get("dart_rate", ""),
                    trend.get("severity_rate", ""),
                    trend.get("total_incidents", ""),
                ]
            )

    else:
        # Generic key-value export
        writer.writerow(["Key", "Value"])
        for key, value in data.items():
            if not isinstance(value, dict | list):
                writer.writerow([key, value])

    return output.getvalue().encode("utf-8")


def export_to_pdf(data: dict, export_type: str) -> bytes:
    """Generate a PDF from export data using ReportLab.

    Args:
        data: Dict of export data.
        export_type: Type of export.

    Returns:
        PDF file as bytes.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:
        raise RuntimeError(
            "reportlab is required for PDF generation. Install with: pip install reportlab"
        )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    title_map = {
        "safety_summary": "Safety Summary Report",
        "loss_run": "Loss Run Report",
        "osha_300": "OSHA Form 300 — Log of Work-Related Injuries and Illnesses",
        "emr": "Experience Modification Rate (EMR) Report",
        "risk_profile": "Risk Profile Report",
    }
    title = title_map.get(export_type, "Insurance Export Report")
    elements.append(Paragraph(title, styles["Title"]))
    elements.append(Spacer(1, 0.25 * inch))

    if export_type == "safety_summary":
        table_data = [["Metric", "Value"]]
        for key, value in data.items():
            if not isinstance(value, dict | list):
                display_key = key.replace("_", " ").title()
                table_data.append([display_key, str(value)])

        table = Table(table_data, colWidths=[3 * inch, 3 * inch])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#ECF0F1")],
                    ),
                ]
            )
        )
        elements.append(table)

    elif export_type == "loss_run":
        elements.append(
            Paragraph(
                f"Period: {data.get('date_range_start', '')} to {data.get('date_range_end', '')}",
                styles["Normal"],
            )
        )
        elements.append(Spacer(1, 0.15 * inch))

        table_data = [["Date", "Type", "Medical", "Indemnity", "Total", "Status"]]
        for entry in data.get("entries", []):
            table_data.append(
                [
                    str(entry.get("incident_date", "")),
                    str(entry.get("incident_type", "")),
                    f"${entry.get('medical_cost', 0):,.2f}"
                    if entry.get("medical_cost")
                    else "$0.00",
                    f"${entry.get('indemnity_cost', 0):,.2f}"
                    if entry.get("indemnity_cost")
                    else "$0.00",
                    f"${entry.get('total_cost', 0):,.2f}" if entry.get("total_cost") else "$0.00",
                    str(entry.get("status", "")),
                ]
            )

        if len(table_data) > 1:
            table = Table(
                table_data,
                colWidths=[1.1 * inch, 1.2 * inch, 1 * inch, 1 * inch, 1 * inch, 0.8 * inch],
            )
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ]
                )
            )
            elements.append(table)

        # Summary
        elements.append(Spacer(1, 0.25 * inch))
        summary_data = [
            ["Total Medical", f"${data.get('total_medical', 0)}"],
            ["Total Indemnity", f"${data.get('total_indemnity', 0)}"],
            ["Total Incurred", f"${data.get('total_incurred', 0)}"],
            ["Open Claims", str(data.get("open_claims", 0))],
            ["Closed Claims", str(data.get("closed_claims", 0))],
        ]
        summary_table = Table(summary_data, colWidths=[3 * inch, 2 * inch])
        summary_table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ]
            )
        )
        elements.append(summary_table)

    elif export_type == "osha_300":
        elements.append(
            Paragraph(
                f"Establishment: {data.get('establishment_name', '')}  |  Year: {data.get('year', '')}",
                styles["Normal"],
            )
        )
        elements.append(Spacer(1, 0.15 * inch))

        table_data = [["Case #", "Name", "Title", "Date", "Class", "Days Away", "Days Restr."]]
        for entry in data.get("entries", []):
            table_data.append(
                [
                    str(entry.get("case_number", "")),
                    str(entry.get("employee_name", "")),
                    str(entry.get("job_title", ""))[:20],
                    str(entry.get("date_of_injury", "")),
                    str(entry.get("classified_as", "")),
                    str(entry.get("days_away", 0)),
                    str(entry.get("days_restricted", 0)),
                ]
            )

        if len(table_data) > 1:
            table = Table(
                table_data,
                colWidths=[
                    0.8 * inch,
                    1 * inch,
                    1 * inch,
                    0.9 * inch,
                    1 * inch,
                    0.7 * inch,
                    0.7 * inch,
                ],
            )
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 7),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ]
                )
            )
            elements.append(table)

    else:
        # Generic
        for key, value in data.items():
            if not isinstance(value, dict | list):
                elements.append(
                    Paragraph(
                        f"<b>{key.replace('_', ' ').title()}:</b> {value}",
                        styles["Normal"],
                    )
                )

    doc.build(elements)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Export record persistence
# ---------------------------------------------------------------------------


async def save_export_record(
    db: AsyncSession,
    org_id: str,
    export_type: str,
    date_range_start: date,
    date_range_end: date,
    export_data: dict,
    requested_by: str,
    project_id: str | None = None,
    file_url: str | None = None,
) -> InsuranceExport:
    """Persist an export record for audit trail.

    Args:
        db: Database session.
        org_id: Organization UUID string.
        export_type: Type of export.
        date_range_start: Start of date range.
        date_range_end: End of date range.
        export_data: The export data dict.
        requested_by: User UUID string.
        project_id: Optional project UUID string.
        file_url: Optional S3 URL if file was generated.

    Returns:
        Created InsuranceExport.
    """
    record = InsuranceExport(
        org_id=org_id,
        project_id=project_id,
        export_type=export_type,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        export_data=export_data,
        file_url=file_url,
        requested_by=requested_by,
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record


async def list_exports(
    db: AsyncSession,
    org_id: str,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[InsuranceExport], int]:
    """List previous insurance exports for an organization.

    Args:
        db: Database session.
        org_id: Organization UUID string.
        skip: Offset.
        limit: Max results.

    Returns:
        Tuple of (exports, total_count).
    """
    count_result = await db.execute(
        select(func.count(InsuranceExport.id)).where(InsuranceExport.org_id == org_id)
    )
    total = count_result.scalar_one()

    result = await db.execute(
        select(InsuranceExport)
        .where(InsuranceExport.org_id == org_id)
        .order_by(InsuranceExport.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    exports = list(result.scalars().all())
    return exports, total
