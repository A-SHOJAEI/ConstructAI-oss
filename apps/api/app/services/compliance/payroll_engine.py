"""Certified payroll pure-math engine for DOL WH-347 compliance.

All functions are pure (no DB access, no async) for easy testing.
Every computed field uses Decimal for exact monetary arithmetic.

IMPORTANT: This module provides ESTIMATED payroll calculations only.

Limitations:
- Federal withholding uses single-filer brackets only (no married/HoH)
- Only CA, NY, OR have progressive state brackets; all others use flat approximations
- Local/city taxes cover only 5 major cities
- Tax brackets are 2026 projections -- verify against IRS Publication 15-T when released

Do NOT use this for actual certified payroll compliance without professional review.
For production compliance, integrate with a certified payroll provider API (ADP, Paychex, etc.)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any  # noqa: F401 - used in type annotations

logger = logging.getLogger(__name__)

# M-25: Helper used by ANY future logging in this module to redact payroll
# PII (contractor name, address, signer, SSN-adjacent fields). Do not log
# ``contractor_info`` dicts raw — pass them through this first.
_PAYROLL_PII_FIELDS = frozenset(
    {"name", "address", "ein", "signer_name", "signer_title", "ssn", "phone"}
)


def redact_payroll_info(info: dict) -> dict:
    """Return a copy of ``info`` with PII-bearing fields replaced by ``***``."""
    return {k: ("***" if k in _PAYROLL_PII_FIELDS and v else v) for k, v in info.items()}


COMPLIANCE_STATUS = "BETA"  # Not production-grade compliance code

ZERO = Decimal("0")
HUNDRED = Decimal("100")
TWO_PLACES = Decimal("0.01")

# Federal minimum wage (2026)
FEDERAL_MINIMUM_WAGE = Decimal(os.environ.get("PAYROLL_FEDERAL_MINIMUM_WAGE", "7.25"))

# Social Security wage cap for 2026
SS_WAGE_CAP_2026 = Decimal(os.environ.get("PAYROLL_SS_WAGE_CAP", "174900"))

# FICA rates
FICA_SS_RATE = Decimal(os.environ.get("PAYROLL_SS_RATE", "0.062"))  # 6.2% Social Security
FICA_MEDICARE_RATE = Decimal(os.environ.get("PAYROLL_MEDICARE_RATE", "0.0145"))  # 1.45% Medicare

# 2026 federal income tax brackets (single, simplified for payroll withholding)
# UPDATE PROCEDURE: Update these annually when IRS Publication 15-T is released.
# Override at runtime via PAYROLL_TAX_BRACKETS_JSON env var (JSON array of [threshold, rate] pairs).
_DEFAULT_FEDERAL_TAX_BRACKETS: list[tuple[Decimal, Decimal]] = [
    (Decimal("11925"), Decimal("0.10")),
    (Decimal("48475"), Decimal("0.12")),
    (Decimal("103350"), Decimal("0.22")),
    (Decimal("197300"), Decimal("0.24")),
    (Decimal("250525"), Decimal("0.32")),
    (Decimal("626350"), Decimal("0.35")),
    (Decimal("999999999"), Decimal("0.37")),
]

_TAX_BRACKET_CONFIG = os.environ.get("PAYROLL_TAX_BRACKETS_JSON")
if _TAX_BRACKET_CONFIG:
    try:
        _parsed = json.loads(_TAX_BRACKET_CONFIG)
        FEDERAL_TAX_BRACKETS = [
            (Decimal(str(threshold)), Decimal(str(rate))) for threshold, rate in _parsed
        ]
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Invalid PAYROLL_TAX_BRACKETS_JSON, using 2026 defaults")
        FEDERAL_TAX_BRACKETS = _DEFAULT_FEDERAL_TAX_BRACKETS
else:
    FEDERAL_TAX_BRACKETS = _DEFAULT_FEDERAL_TAX_BRACKETS

# State income tax flat rates (simplified — real states have brackets)
STATE_TAX_RATES: dict[str, Decimal] = {
    "AL": Decimal("0.050"),
    "AK": Decimal("0.000"),
    "AZ": Decimal("0.025"),
    "AR": Decimal("0.044"),
    "CA": Decimal("0.093"),
    "CO": Decimal("0.044"),
    "CT": Decimal("0.050"),
    "DE": Decimal("0.066"),
    "FL": Decimal("0.000"),
    "GA": Decimal("0.055"),
    "HI": Decimal("0.072"),
    "ID": Decimal("0.058"),
    "IL": Decimal("0.049"),
    "IN": Decimal("0.031"),
    "IA": Decimal("0.044"),
    "KS": Decimal("0.046"),
    "KY": Decimal("0.040"),
    "LA": Decimal("0.042"),
    "ME": Decimal("0.071"),
    "MD": Decimal("0.050"),
    "MA": Decimal("0.050"),
    "MI": Decimal("0.043"),
    "MN": Decimal("0.068"),
    "MS": Decimal("0.050"),
    "MO": Decimal("0.048"),
    "MT": Decimal("0.059"),
    "NE": Decimal("0.056"),
    "NV": Decimal("0.000"),
    "NH": Decimal("0.000"),
    "NJ": Decimal("0.054"),
    "NM": Decimal("0.049"),
    "NY": Decimal("0.068"),
    "NC": Decimal("0.046"),
    "ND": Decimal("0.018"),
    "OH": Decimal("0.040"),
    "OK": Decimal("0.025"),
    "OR": Decimal("0.088"),
    "PA": Decimal("0.031"),
    "RI": Decimal("0.059"),
    "SC": Decimal("0.065"),
    "SD": Decimal("0.000"),
    "TN": Decimal("0.000"),
    "TX": Decimal("0.000"),
    "UT": Decimal("0.047"),
    "VT": Decimal("0.066"),
    "VA": Decimal("0.058"),
    "WA": Decimal("0.000"),
    "WV": Decimal("0.052"),
    "WI": Decimal("0.053"),
    "WY": Decimal("0.000"),
    "DC": Decimal("0.065"),
}

# Progressive state tax brackets for high-impact states
# Each entry: list of (upper_bound, rate) tuples — contiguous brackets
# Tax is computed cumulatively: each slice from the previous upper bound to
# the current upper bound is taxed at the given rate.
_PROGRESSIVE_STATE_BRACKETS: dict[str, list[tuple[Decimal, Decimal]]] = {
    "CA": [
        (Decimal("10099"), Decimal("0.01")),
        (Decimal("23942"), Decimal("0.02")),
        (Decimal("37788"), Decimal("0.04")),
        (Decimal("52455"), Decimal("0.06")),
        (Decimal("66295"), Decimal("0.08")),
        (Decimal("338639"), Decimal("0.093")),
        (Decimal("406364"), Decimal("0.103")),
        (Decimal("677275"), Decimal("0.113")),
        (Decimal("999999999"), Decimal("0.123")),
    ],
    "NY": [
        (Decimal("8500"), Decimal("0.04")),
        (Decimal("11700"), Decimal("0.045")),
        (Decimal("13900"), Decimal("0.0525")),
        (Decimal("80650"), Decimal("0.0585")),
        (Decimal("215400"), Decimal("0.0625")),
        (Decimal("1077550"), Decimal("0.0685")),
        (Decimal("5000000"), Decimal("0.0965")),
        (Decimal("25000000"), Decimal("0.103")),
        (Decimal("999999999"), Decimal("0.109")),
    ],
    "OR": [
        (Decimal("3750"), Decimal("0.0475")),
        (Decimal("9450"), Decimal("0.0675")),
        (Decimal("125000"), Decimal("0.0875")),
        (Decimal("999999999"), Decimal("0.099")),
    ],
}

# SV-38: Local / city income tax rates for major cities
LOCAL_TAX_RATES: dict[str, Decimal] = {
    "new york city": Decimal("0.03876"),
    "nyc": Decimal("0.03876"),
    "philadelphia": Decimal("0.0375"),
    "detroit": Decimal("0.024"),
    "st. louis": Decimal("0.010"),
    "st louis": Decimal("0.010"),
    "kansas city": Decimal("0.010"),
    "kansas city mo": Decimal("0.010"),
}

# Additional Medicare Tax threshold (per employee per year)
ADDITIONAL_MEDICARE_THRESHOLD = Decimal(
    os.environ.get("PAYROLL_ADDITIONAL_MEDICARE_THRESHOLD", "200000")
)
ADDITIONAL_MEDICARE_RATE = Decimal(
    os.environ.get("PAYROLL_ADDITIONAL_MEDICARE_RATE", "0.009")
)  # 0.9%


def _calculate_progressive_state_tax(annualized_gross: Decimal, state: str) -> Decimal:
    """Calculate state income tax using progressive brackets.

    Brackets are contiguous: each (upper_bound, rate) pair taxes the slice
    from the previous upper bound to the current one, eliminating $1 gaps.

    Args:
        annualized_gross: Annualized gross pay.
        state: 2-letter state code (must be in _PROGRESSIVE_STATE_BRACKETS).

    Returns:
        Annual state tax amount.
    """
    brackets = _PROGRESSIVE_STATE_BRACKETS.get(state)
    if brackets is None:
        return ZERO

    tax = ZERO
    prev_upper = ZERO
    for upper, rate in brackets:
        if annualized_gross <= prev_upper:
            break
        taxable_in_bracket = min(annualized_gross, upper) - prev_upper
        if taxable_in_bracket > ZERO:
            tax += taxable_in_bracket * rate
        prev_upper = upper

    return _round2(tax)


# Standard DOL trade classifications
PREVAILING_WAGE_TRADES: list[str] = [
    "asbestos_worker",
    "boilermaker",
    "bricklayer",
    "carpenter",
    "cement_mason",
    "communication_technician",
    "drywall_finisher",
    "drywall_hanger",
    "electrician",
    "elevator_mechanic",
    "floor_layer",
    "glazier",
    "heat_frost_insulator",
    "ironworker_ornamental",
    "ironworker_reinforcing",
    "ironworker_structural",
    "laborer",
    "lather",
    "line_constructor",
    "marble_setter",
    "millwright",
    "operating_engineer",
    "painter",
    "pipefitter",
    "plasterer",
    "plumber",
    "power_equipment_operator",
    "roofer",
    "sheet_metal_worker",
    "sprinkler_fitter",
    "steam_fitter",
    "stone_mason",
    "terrazzo_worker",
    "tile_setter",
    "truck_driver",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FringeBenefitResult:
    """Result of fringe benefit calculation."""

    total_fringe: Decimal
    health: Decimal = ZERO
    pension: Decimal = ZERO
    vacation: Decimal = ZERO
    training: Decimal = ZERO
    other: Decimal = ZERO


@dataclass
class ComplianceResult:
    """Result of prevailing wage compliance check."""

    status: str  # compliant | underpayment
    actual_rate: Decimal
    required_rate: Decimal
    shortfall_per_hour: Decimal = ZERO
    total_shortfall: Decimal = ZERO
    total_hours: Decimal = ZERO


@dataclass
class DeductionResult:
    """Result of payroll deduction calculation."""

    federal_tax: Decimal = ZERO
    state_tax: Decimal = ZERO
    social_security: Decimal = ZERO
    medicare: Decimal = ZERO
    union_dues: Decimal = ZERO
    garnishments: Decimal = ZERO
    other: Decimal = ZERO
    total_deductions: Decimal = ZERO
    net_pay: Decimal = ZERO


@dataclass
class WH347Report:
    """Formatted data for DOL WH-347 (Statement of Compliance)."""

    contractor_name: str
    contractor_address: str
    project_name: str
    contract_number: str
    pay_period_start: date
    pay_period_end: date
    payroll_number: str
    workers: list[dict] = field(default_factory=list)
    total_gross: Decimal = ZERO
    total_deductions: Decimal = ZERO
    total_net: Decimal = ZERO
    total_fringe: Decimal = ZERO
    certification_text: str = ""


@dataclass
class PayrollValidationError:
    """A validation error found in a payroll record."""

    worker_name: str
    field: str
    message: str
    severity: str = "error"  # error | warning


@dataclass
class PeriodTotals:
    """Aggregated totals for a payroll period."""

    total_hours_straight: Decimal = ZERO
    total_hours_overtime: Decimal = ZERO
    total_hours_other: Decimal = ZERO
    total_hours: Decimal = ZERO
    total_gross: Decimal = ZERO
    total_fringe: Decimal = ZERO
    total_deductions: Decimal = ZERO
    total_net: Decimal = ZERO
    record_count: int = 0
    by_trade: dict[str, dict] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pure math functions
# ---------------------------------------------------------------------------


def _round2(value: Decimal) -> Decimal:
    """Round to 2 decimal places (money)."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def calculate_gross_pay(
    hours_straight: Decimal,
    hours_overtime: Decimal,
    hours_other: Decimal = ZERO,
    rate_straight: Decimal = ZERO,
    rate_overtime: Decimal | None = None,
) -> Decimal:
    """Calculate gross pay from hours and rates.

    If rate_overtime is not provided, it defaults to 1.5x the straight rate
    per FLSA requirements.

    Args:
        hours_straight: Regular hours worked.
        hours_overtime: Overtime hours worked.
        hours_other: Other hours (e.g., holiday, travel).
        rate_straight: Hourly rate for straight time.
        rate_overtime: Hourly rate for overtime. Defaults to 1.5 * rate_straight.

    Returns:
        Gross pay rounded to 2 decimal places.
    """
    if rate_overtime is None:
        rate_overtime = _round2(rate_straight * Decimal("1.5"))

    gross = (
        hours_straight * rate_straight
        + hours_overtime * rate_overtime
        + hours_other * rate_straight
    )
    return _round2(gross)


def calculate_fringe_benefits(
    hours_total: Decimal,
    fringe_rate: Decimal,
    fringe_breakdown: dict[str, Decimal] | None = None,
) -> FringeBenefitResult:
    """Calculate fringe benefit amounts from total hours and rate.

    Args:
        hours_total: Total hours worked (straight + OT + other).
        fringe_rate: Total fringe rate per hour.
        fringe_breakdown: Optional per-category rates (health, pension, etc.).
            If not provided, the total is allocated proportionally to default
            splits: health 40%, pension 30%, vacation 15%, training 15%.

    Returns:
        FringeBenefitResult with total and per-category amounts.
    """
    total_fringe = _round2(hours_total * fringe_rate)

    if fringe_breakdown:
        health = _round2(hours_total * fringe_breakdown.get("health", ZERO))
        pension = _round2(hours_total * fringe_breakdown.get("pension", ZERO))
        vacation = _round2(hours_total * fringe_breakdown.get("vacation", ZERO))
        training = _round2(hours_total * fringe_breakdown.get("training", ZERO))
        other = _round2(hours_total * fringe_breakdown.get("other", ZERO))
    else:
        # Default splits
        health = _round2(total_fringe * Decimal("0.40"))
        pension = _round2(total_fringe * Decimal("0.30"))
        vacation = _round2(total_fringe * Decimal("0.15"))
        training = _round2(total_fringe * Decimal("0.15"))
        other = ZERO

    return FringeBenefitResult(
        total_fringe=total_fringe,
        health=health,
        pension=pension,
        vacation=vacation,
        training=training,
        other=other,
    )


def check_prevailing_wage_compliance(
    payroll_record: dict,
    prevailing_rate: dict,
    tolerance: Decimal = Decimal("0.01"),
) -> ComplianceResult:
    """Check if a payroll record meets prevailing wage requirements.

    Compares the worker's effective rate (base + fringe per hour) against
    the required prevailing wage total rate.

    Args:
        payroll_record: Dict with keys: rate_straight, fringe_benefits,
            hours_straight, hours_overtime, hours_other.
        prevailing_rate: Dict with keys: total_rate (base + fringe required).
        tolerance: Acceptable shortfall per hour (default $0.01).

    Returns:
        ComplianceResult indicating compliant or underpayment.
    """
    actual_base = Decimal(str(payroll_record.get("rate_straight", 0)))

    # Calculate fringe per hour
    fringe_data = payroll_record.get("fringe_benefits", {})
    hours_st = Decimal(str(payroll_record.get("hours_straight", 0)))
    hours_ot = Decimal(str(payroll_record.get("hours_overtime", 0)))
    hours_other = Decimal(str(payroll_record.get("hours_other", 0)))
    total_hours = hours_st + hours_ot + hours_other

    # Total fringe from breakdown
    total_fringe_amount = ZERO
    if isinstance(fringe_data, dict):
        for key in ("health", "pension", "vacation", "training", "other"):
            total_fringe_amount += Decimal(str(fringe_data.get(key, 0)))

    fringe_per_hour = _round2(total_fringe_amount / total_hours) if total_hours > ZERO else ZERO

    actual_rate = _round2(actual_base + fringe_per_hour)
    required_rate = Decimal(str(prevailing_rate.get("total_rate", 0)))

    shortfall = _round2(required_rate - actual_rate)
    if shortfall <= tolerance:
        return ComplianceResult(
            status="compliant",
            actual_rate=actual_rate,
            required_rate=required_rate,
            shortfall_per_hour=ZERO,
            total_shortfall=ZERO,
            total_hours=total_hours,
        )

    return ComplianceResult(
        status="underpayment",
        actual_rate=actual_rate,
        required_rate=required_rate,
        shortfall_per_hour=shortfall,
        total_shortfall=_round2(shortfall * total_hours),
        total_hours=total_hours,
    )


def _calculate_federal_tax(annualized_gross: Decimal) -> Decimal:
    """Calculate federal income tax withholding using 2026 brackets.

    Uses annualized gross to determine the marginal bracket, then
    returns the per-period amount.
    """
    tax = ZERO
    prev_bracket = ZERO

    for bracket_limit, rate in FEDERAL_TAX_BRACKETS:
        if annualized_gross <= prev_bracket:
            break
        taxable_in_bracket = min(annualized_gross, bracket_limit) - prev_bracket
        if taxable_in_bracket > ZERO:
            tax += taxable_in_bracket * rate
        prev_bracket = bracket_limit

    return _round2(tax)


def calculate_payroll_deductions(
    gross_pay: Decimal,
    deduction_rules: dict | None = None,
    city: str | None = None,
) -> DeductionResult:
    """Calculate all payroll deductions from gross pay.

    Args:
        gross_pay: Gross pay for the period.
        deduction_rules: Optional dict with keys:
            - state: 2-letter state code for state tax
            - pay_periods_per_year: int (default 26 for biweekly)
            - ytd_gross: Decimal year-to-date gross (for SS cap)
            - union_dues: Decimal flat amount
            - garnishments: Decimal flat amount
            - other_deductions: Decimal flat amount
        city: SV-38: Optional city name for local/city income tax (e.g.,
            "New York City", "Philadelphia", "Detroit").

    Returns:
        DeductionResult with itemized deductions and net pay.
    """
    rules = deduction_rules or {}
    pay_periods = int(rules.get("pay_periods_per_year", 26))
    state_code = rules.get("state", "")
    ytd_gross = Decimal(str(rules.get("ytd_gross", 0)))

    # Federal income tax (annualize, compute annual tax, de-annualize)
    annualized_gross = gross_pay * Decimal(pay_periods)
    annual_federal = _calculate_federal_tax(annualized_gross)
    federal_tax = _round2(annual_federal / Decimal(pay_periods))

    # State income tax — use progressive brackets for CA, NY, OR; flat rate otherwise
    if state_code in _PROGRESSIVE_STATE_BRACKETS:
        annual_state_tax = _calculate_progressive_state_tax(annualized_gross, state_code)
        state_tax = _round2(annual_state_tax / Decimal(pay_periods))
    else:
        state_rate = STATE_TAX_RATES.get(state_code, ZERO)
        state_tax = _round2(gross_pay * state_rate)

    # SV-38: Local / city income tax
    local_tax = ZERO
    if city:
        city_key = city.lower().strip()
        local_rate = LOCAL_TAX_RATES.get(city_key, ZERO)
        if local_rate > ZERO:
            local_tax = _round2(gross_pay * local_rate)

    # Social Security (6.2% up to wage cap)
    ss_taxable = gross_pay
    if ytd_gross >= SS_WAGE_CAP_2026:
        ss_taxable = ZERO
    elif ytd_gross + gross_pay > SS_WAGE_CAP_2026:
        ss_taxable = SS_WAGE_CAP_2026 - ytd_gross
    social_security = _round2(ss_taxable * FICA_SS_RATE)

    # Medicare (1.45% — no cap)
    medicare = _round2(gross_pay * FICA_MEDICARE_RATE)

    # Additional Medicare Tax (0.9% on wages above $200,000/year)
    # This applies per employee per year, calculated on the YTD basis
    ytd_after = ytd_gross + gross_pay
    if ytd_after > ADDITIONAL_MEDICARE_THRESHOLD:
        # Determine how much of this period's gross is above the threshold
        if ytd_gross >= ADDITIONAL_MEDICARE_THRESHOLD:
            additional_medicare_taxable = gross_pay
        else:
            additional_medicare_taxable = ytd_after - ADDITIONAL_MEDICARE_THRESHOLD
        medicare += _round2(additional_medicare_taxable * ADDITIONAL_MEDICARE_RATE)

    # Union dues, garnishments, other
    union_dues = _round2(Decimal(str(rules.get("union_dues", 0))))
    garnishments = _round2(Decimal(str(rules.get("garnishments", 0))))
    other = _round2(Decimal(str(rules.get("other_deductions", 0))))

    total_deductions = _round2(
        federal_tax
        + state_tax
        + local_tax
        + social_security
        + medicare
        + union_dues
        + garnishments
        + other
    )
    net_pay = _round2(gross_pay - total_deductions)

    result = DeductionResult(
        federal_tax=federal_tax,
        state_tax=state_tax,
        social_security=social_security,
        medicare=medicare,
        union_dues=union_dues,
        garnishments=garnishments,
        other=other + local_tax,  # SV-38: local tax included in 'other' category
        total_deductions=total_deductions,
        net_pay=net_pay,
    )
    # SV-38: Store local tax breakdown as an attribute for consumers that need it
    result._local_tax = local_tax  # type: ignore[attr-defined]
    return result


# WH-347 certification statement (official DOL text)
_WH347_CERTIFICATION_TEXT = (
    "I, {signer_name}, {signer_title} of {contractor_name}, do hereby state:\n"
    "(1) That I pay or supervise the payment of the persons employed by "
    "{contractor_name} on the {project_name}; that during the payroll period "
    "commencing on the {period_start} day of {period_start_month}, {period_start_year}, "
    "and ending the {period_end} day of {period_end_month}, {period_end_year}, "
    "all persons employed on said project have been paid the full weekly wages "
    "earned, that no rebates have been or will be made either directly or "
    "indirectly to or on behalf of said {contractor_name} from the full weekly "
    "wages earned by any person and that no deductions have been made either "
    "directly or indirectly from the full wages earned by any person, other "
    "than permissible deductions as defined in Regulations, Part 3 (29 CFR "
    "Subtitle A), issued by the Secretary of Labor under the Copeland Act, "
    "as amended (48 Stat. 948, 63 Stat. 108, 72 Stat. 967; 76 Stat. 357; "
    "40 U.S.C. 3145), and described below.\n"
    "(2) That any payrolls otherwise under this contract required to be "
    "submitted for the above period are correct and complete; that the wage "
    "rates for laborers or mechanics contained therein are not less than the "
    "applicable wage rates contained in any wage determination incorporated "
    "into the contract.\n"
    "(3) That any apprentices employed in the above period are duly registered "
    "in a bona fide apprenticeship program registered with a State "
    "apprenticeship agency recognized by the Bureau of Apprenticeship and "
    "Training, United States Department of Labor, or if no such recognized "
    "agency exists in a State, are registered with the Bureau of "
    "Apprenticeship and Training, United States Department of Labor.\n"
    "(4) That:\n"
    "(a) WHERE FRINGE BENEFITS ARE PAID TO APPROVED PLANS, FUNDS, OR PROGRAMS "
    "— in addition to the basic hourly wage rates paid to each laborer or "
    "mechanic listed in the above referenced payroll, payments of fringe "
    "benefits as listed in the contract have been or will be made to "
    "appropriate programs for the benefit of such employees, except as noted "
    "in Section 4(c) below."
)


def generate_wh347_data(
    contractor_info: dict,
    project_info: dict,
    payroll_records: list[dict],
    period_start: date,
    period_end: date,
) -> WH347Report:
    """Format payroll data for DOL WH-347 certified payroll form.

    Groups records by worker and calculates all totals needed for the form.

    Args:
        contractor_info: Dict with name, address, ein.
        project_info: Dict with name, contract_number, location.
        payroll_records: List of payroll record dicts.
        period_start: Start of pay period.
        period_end: End of pay period.

    Returns:
        WH347Report with workers, totals, and certification text.
    """
    contractor_name = contractor_info.get("name", "")
    contractor_address = contractor_info.get("address", "")
    project_name = project_info.get("name", "")
    contract_number = project_info.get("contract_number", "")
    payroll_number = project_info.get("payroll_number", "1")

    # Group by worker
    worker_map: dict[str, dict] = {}
    for rec in payroll_records:
        name = rec.get("worker_name", "Unknown")
        if name not in worker_map:
            worker_map[name] = {
                "worker_name": name,
                "worker_id": rec.get("worker_id", ""),
                "trade": rec.get("trade", ""),
                "classification": rec.get("classification", ""),
                "hours_straight": ZERO,
                "hours_overtime": ZERO,
                "hours_other": ZERO,
                "rate_straight": Decimal(str(rec.get("rate_straight", 0))),
                "rate_overtime": Decimal(str(rec.get("rate_overtime", 0))),
                "gross_pay": ZERO,
                "deductions": ZERO,
                "net_pay": ZERO,
                "fringe_total": ZERO,
            }

        w = worker_map[name]
        w["hours_straight"] += Decimal(str(rec.get("hours_straight", 0)))
        w["hours_overtime"] += Decimal(str(rec.get("hours_overtime", 0)))
        w["hours_other"] += Decimal(str(rec.get("hours_other", 0)))
        w["gross_pay"] += Decimal(str(rec.get("gross_pay", 0)))
        w["net_pay"] += Decimal(str(rec.get("net_pay", 0)))

        # Sum deductions
        deductions = rec.get("deductions", {})
        if isinstance(deductions, dict):
            for val in deductions.values():
                w["deductions"] += Decimal(str(val))

        # Sum fringe
        fringe = rec.get("fringe_benefits", {})
        if isinstance(fringe, dict):
            for val in fringe.values():
                w["fringe_total"] += Decimal(str(val))

    workers = []
    total_gross = ZERO
    total_deductions = ZERO
    total_net = ZERO
    total_fringe = ZERO

    for w in worker_map.values():
        # Round all amounts
        w["hours_straight"] = _round2(w["hours_straight"])
        w["hours_overtime"] = _round2(w["hours_overtime"])
        w["hours_other"] = _round2(w["hours_other"])
        w["gross_pay"] = _round2(w["gross_pay"])
        w["deductions"] = _round2(w["deductions"])
        w["net_pay"] = _round2(w["net_pay"])
        w["fringe_total"] = _round2(w["fringe_total"])

        total_gross += w["gross_pay"]
        total_deductions += w["deductions"]
        total_net += w["net_pay"]
        total_fringe += w["fringe_total"]
        workers.append(w)

    # Build certification text with actual values
    cert_text = _WH347_CERTIFICATION_TEXT.format(
        signer_name=contractor_info.get("signer_name", "_______________"),
        signer_title=contractor_info.get("signer_title", "_______________"),
        contractor_name=contractor_name,
        project_name=project_name,
        period_start=period_start.day,
        period_start_month=period_start.strftime("%B"),
        period_start_year=period_start.year,
        period_end=period_end.day,
        period_end_month=period_end.strftime("%B"),
        period_end_year=period_end.year,
    )

    return WH347Report(
        contractor_name=contractor_name,
        contractor_address=contractor_address,
        project_name=project_name,
        contract_number=contract_number,
        pay_period_start=period_start,
        pay_period_end=period_end,
        payroll_number=str(payroll_number),
        workers=workers,
        total_gross=_round2(total_gross),
        total_deductions=_round2(total_deductions),
        total_net=_round2(total_net),
        total_fringe=_round2(total_fringe),
        certification_text=cert_text,
    )


def validate_payroll_records(
    records: list[dict],
    today: date | None = None,
) -> list[PayrollValidationError]:
    """Validate a batch of payroll records for common errors.

    Checks:
    - Hours > 0 for at least one type
    - Rate >= federal minimum wage ($7.25)
    - Gross pay = hours * rate (within $0.02 tolerance)
    - Overtime rate >= 1.5x straight rate
    - No future pay period dates
    - No duplicate workers per period

    Args:
        records: List of payroll record dicts.
        today: Current date for future-date checks. Defaults to date.today().

    Returns:
        List of PayrollValidationError (empty = valid).
    """
    if today is None:
        today = date.today()

    errors: list[PayrollValidationError] = []
    seen_workers: dict[str, set[str]] = {}  # period_key -> set of worker names

    for rec in records:
        name = rec.get("worker_name", "Unknown")
        hours_st = Decimal(str(rec.get("hours_straight", 0)))
        hours_ot = Decimal(str(rec.get("hours_overtime", 0)))
        hours_other = Decimal(str(rec.get("hours_other", 0)))
        total_hours = hours_st + hours_ot + hours_other
        rate_st = Decimal(str(rec.get("rate_straight", 0)))
        rate_ot = Decimal(str(rec.get("rate_overtime", 0)))
        gross = Decimal(str(rec.get("gross_pay", 0)))

        # Check hours > 0
        if total_hours <= ZERO:
            errors.append(
                PayrollValidationError(
                    worker_name=name,
                    field="hours",
                    message="Total hours must be greater than zero",
                )
            )

        # Check rate >= minimum wage
        if rate_st < FEDERAL_MINIMUM_WAGE and rate_st > ZERO:
            errors.append(
                PayrollValidationError(
                    worker_name=name,
                    field="rate_straight",
                    message=(
                        f"Straight rate ${rate_st} is below federal minimum wage "
                        f"${FEDERAL_MINIMUM_WAGE}"
                    ),
                )
            )

        # Check overtime rate >= 1.5x straight
        min_ot_rate = _round2(rate_st * Decimal("1.5"))
        if hours_ot > ZERO and rate_ot < min_ot_rate:
            errors.append(
                PayrollValidationError(
                    worker_name=name,
                    field="rate_overtime",
                    message=(f"Overtime rate ${rate_ot} is below required 1.5x (${min_ot_rate})"),
                )
            )

        # Check gross pay matches calculated
        expected_gross = calculate_gross_pay(hours_st, hours_ot, hours_other, rate_st, rate_ot)
        tolerance = Decimal("0.02")
        if abs(gross - expected_gross) > tolerance:
            errors.append(
                PayrollValidationError(
                    worker_name=name,
                    field="gross_pay",
                    message=(
                        f"Gross pay ${gross} does not match calculated "
                        f"${expected_gross} (tolerance: ${tolerance})"
                    ),
                )
            )

        # Check for future dates
        period_end = rec.get("pay_period_end")
        if period_end:
            if isinstance(period_end, str):
                try:
                    period_end = date.fromisoformat(period_end)
                except (ValueError, TypeError):
                    period_end = None
            if isinstance(period_end, date) and period_end > today:
                errors.append(
                    PayrollValidationError(
                        worker_name=name,
                        field="pay_period_end",
                        message=f"Pay period end date {period_end} is in the future",
                        severity="warning",
                    )
                )

        # Check for duplicates
        period_start = rec.get("pay_period_start", "")
        period_end_str = str(rec.get("pay_period_end", ""))
        period_key = f"{period_start}_{period_end_str}"
        if period_key not in seen_workers:
            seen_workers[period_key] = set()
        if name in seen_workers[period_key]:
            errors.append(
                PayrollValidationError(
                    worker_name=name,
                    field="worker_name",
                    message=(
                        f"Duplicate worker '{name}' in period {period_start} to {period_end_str}"
                    ),
                )
            )
        seen_workers[period_key].add(name)

    return errors


def calculate_period_totals(
    records: list[dict],
) -> PeriodTotals:
    """Calculate aggregated totals for a payroll period.

    Args:
        records: List of payroll record dicts.

    Returns:
        PeriodTotals with hours, pay, and by-trade breakdown.
    """
    totals = PeriodTotals()

    for rec in records:
        hours_st = Decimal(str(rec.get("hours_straight", 0)))
        hours_ot = Decimal(str(rec.get("hours_overtime", 0)))
        hours_other = Decimal(str(rec.get("hours_other", 0)))
        gross = Decimal(str(rec.get("gross_pay", 0)))
        net = Decimal(str(rec.get("net_pay", 0)))

        totals.total_hours_straight += hours_st
        totals.total_hours_overtime += hours_ot
        totals.total_hours_other += hours_other
        totals.total_hours += hours_st + hours_ot + hours_other
        totals.total_gross += gross
        totals.total_net += net
        totals.record_count += 1

        # Fringe
        fringe = rec.get("fringe_benefits", {})
        if isinstance(fringe, dict):
            for val in fringe.values():
                totals.total_fringe += Decimal(str(val))

        # Deductions
        deductions = rec.get("deductions", {})
        if isinstance(deductions, dict):
            for val in deductions.values():
                totals.total_deductions += Decimal(str(val))

        # By trade
        trade = rec.get("trade", "unknown")
        if trade not in totals.by_trade:
            totals.by_trade[trade] = {
                "hours": ZERO,
                "gross": ZERO,
                "count": 0,
            }
        totals.by_trade[trade]["hours"] += hours_st + hours_ot + hours_other
        totals.by_trade[trade]["gross"] += gross
        totals.by_trade[trade]["count"] += 1

    # Round all totals
    totals.total_hours_straight = _round2(totals.total_hours_straight)
    totals.total_hours_overtime = _round2(totals.total_hours_overtime)
    totals.total_hours_other = _round2(totals.total_hours_other)
    totals.total_hours = _round2(totals.total_hours)
    totals.total_gross = _round2(totals.total_gross)
    totals.total_fringe = _round2(totals.total_fringe)
    totals.total_deductions = _round2(totals.total_deductions)
    totals.total_net = _round2(totals.total_net)

    return totals
