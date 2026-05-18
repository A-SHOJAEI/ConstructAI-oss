"""Tests for the payroll engine pure helpers (PII redaction, gross
pay, fringe benefits, prevailing wage check).

Pin FLSA-aligned overtime calculation, fringe benefit splits, and
the [security] PII redaction that prevents SSN / DOB leakage.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.compliance.payroll_engine import (
    FICA_MEDICARE_RATE,
    FICA_SS_RATE,
    SS_WAGE_CAP_2026,
    STATE_TAX_RATES,
    ZERO,
    FringeBenefitResult,
    calculate_fringe_benefits,
    calculate_gross_pay,
    redact_payroll_info,
)

# =========================================================================
# redact_payroll_info — [security] PII redaction
# =========================================================================


def test_redact_ssn():
    """[security] SSN must NEVER appear in logs or error messages."""
    out = redact_payroll_info({"ssn": "123-45-6789", "classification": "Carpenter"})
    assert out["ssn"] == "***"
    # classification is NOT a PII field:
    assert out["classification"] == "Carpenter"


def test_redact_name():
    """[M-25] Worker/contractor name is PII per the payroll engine
    redaction list — pin so a refactor can't quietly drop it."""
    out = redact_payroll_info({"name": "Alice"})
    assert out["name"] == "***"


def test_redact_address():
    """Worker home addresses are PII per Davis-Bacon submission rules."""
    out = redact_payroll_info({"address": "123 Main St"})
    assert out["address"] == "***"


def test_redact_ein_signer_phone():
    """EIN, signer_name, signer_title, phone all in the documented
    PII set."""
    out = redact_payroll_info(
        {
            "ein": "12-3456789",
            "signer_name": "John Doe",
            "signer_title": "President",
            "phone": "555-1234",
        }
    )
    assert out["ein"] == "***"
    assert out["signer_name"] == "***"
    assert out["signer_title"] == "***"
    assert out["phone"] == "***"


def test_redact_preserves_non_pii_fields():
    """Non-PII fields like classification, hours, rate pass through."""
    out = redact_payroll_info(
        {
            "classification": "Carpenter",
            "hours_straight": 40,
            "rate_straight": 28.50,
        }
    )
    assert out["classification"] == "Carpenter"
    assert out["hours_straight"] == 40
    assert out["rate_straight"] == 28.50


def test_redact_empty_pii_field_unchanged():
    """An empty/None PII field stays empty (no redaction needed)."""
    out = redact_payroll_info({"ssn": "", "address": None})
    assert out["ssn"] == ""
    assert out["address"] is None


def test_redact_returns_new_dict():
    """Redaction must NOT mutate the input."""
    original = {"ssn": "123-45-6789", "name": "Alice"}
    out = redact_payroll_info(original)
    assert original["ssn"] == "123-45-6789"  # untouched
    assert out is not original


# =========================================================================
# Tax constants
# =========================================================================


def test_fica_rates_canonical():
    """[FLSA invariant] FICA Social Security rate 6.2%, Medicare 1.45%.
    Pin so a refactor can't silently change federal payroll math."""
    assert Decimal("0.062") == FICA_SS_RATE
    assert Decimal("0.0145") == FICA_MEDICARE_RATE


def test_ss_wage_cap_2026_canonical():
    """2026 Social Security wage cap is $174,900 (IRS-set)."""
    assert Decimal("174900") == SS_WAGE_CAP_2026


def test_state_tax_rates_includes_no_income_tax_states():
    """Pin: states with no income tax (TX, FL, WA, NV, SD, AK, WY, TN,
    NH) all have 0.000 rate."""
    no_tax = {"TX", "FL", "WA", "NV", "SD", "AK", "WY", "TN", "NH"}
    for state in no_tax:
        assert STATE_TAX_RATES[state] == Decimal("0.000")


def test_state_tax_rates_california_high():
    """California flat-rate proxy is among the highest documented."""
    assert STATE_TAX_RATES["CA"] >= Decimal("0.05")


def test_state_tax_rates_all_50_states_plus_dc():
    """All 50 states + DC must be present."""
    states = {
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "DC",
    }
    assert states.issubset(set(STATE_TAX_RATES.keys()))


# =========================================================================
# calculate_gross_pay
# =========================================================================


def test_gross_pay_straight_only():
    """40h × $25/hr = $1000."""
    out = calculate_gross_pay(
        hours_straight=Decimal("40"),
        hours_overtime=ZERO,
        rate_straight=Decimal("25"),
    )
    assert out == Decimal("1000.00")


def test_gross_pay_with_overtime_default_15x():
    """[FLSA] OT defaults to 1.5× straight rate. 40h @ $20 + 10h OT @ $30 = $1100."""
    out = calculate_gross_pay(
        hours_straight=Decimal("40"),
        hours_overtime=Decimal("10"),
        rate_straight=Decimal("20"),
    )
    assert out == Decimal("1100.00")


def test_gross_pay_explicit_overtime_rate():
    """Caller-provided OT rate overrides default."""
    out = calculate_gross_pay(
        hours_straight=Decimal("40"),
        hours_overtime=Decimal("10"),
        rate_straight=Decimal("20"),
        rate_overtime=Decimal("40"),  # 2x premium
    )
    assert out == Decimal("1200.00")


def test_gross_pay_includes_other_hours():
    """Holiday / travel hours paid at straight rate."""
    out = calculate_gross_pay(
        hours_straight=Decimal("40"),
        hours_overtime=ZERO,
        hours_other=Decimal("8"),
        rate_straight=Decimal("25"),
    )
    assert out == Decimal("1200.00")  # 40 + 8 = 48 × 25


def test_gross_pay_zero_hours_zero_pay():
    out = calculate_gross_pay(
        hours_straight=ZERO,
        hours_overtime=ZERO,
        rate_straight=Decimal("25"),
    )
    assert out == Decimal("0.00")


def test_gross_pay_rounded_to_two_decimals():
    """Money values rounded to 2dp (HALF_UP via _round2)."""
    out = calculate_gross_pay(
        hours_straight=Decimal("33.333"),
        hours_overtime=ZERO,
        rate_straight=Decimal("17.77"),
    )
    # 33.333 × 17.77 = 592.32741 → rounds to 592.33
    assert out == Decimal("592.33")


# =========================================================================
# calculate_fringe_benefits
# =========================================================================


def test_fringe_benefits_total_calculation():
    """8h × $10/hr = $80 total fringe."""
    out = calculate_fringe_benefits(
        hours_total=Decimal("8"),
        fringe_rate=Decimal("10"),
    )
    assert out.total_fringe == Decimal("80.00")


def test_fringe_benefits_default_split_health_pension_vacation_training():
    """Without explicit breakdown: 40% health + 30% pension + 15%
    vacation + 15% training = 100%, other = 0."""
    out = calculate_fringe_benefits(
        hours_total=Decimal("100"),
        fringe_rate=Decimal("10"),
    )
    # Total $1000 split:
    assert out.health == Decimal("400.00")
    assert out.pension == Decimal("300.00")
    assert out.vacation == Decimal("150.00")
    assert out.training == Decimal("150.00")
    assert out.other == ZERO


def test_fringe_benefits_explicit_breakdown():
    """When breakdown provided, each category uses its rate (not the
    proportional split of total_fringe)."""
    out = calculate_fringe_benefits(
        hours_total=Decimal("100"),
        fringe_rate=Decimal("10"),  # total
        fringe_breakdown={
            "health": Decimal("5"),
            "pension": Decimal("3"),
            "vacation": Decimal("1"),
            "training": Decimal("1"),
        },
    )
    # Each category × hours:
    assert out.health == Decimal("500.00")
    assert out.pension == Decimal("300.00")
    assert out.vacation == Decimal("100.00")
    assert out.training == Decimal("100.00")


def test_fringe_benefits_zero_hours_zero_fringe():
    out = calculate_fringe_benefits(
        hours_total=ZERO,
        fringe_rate=Decimal("10"),
    )
    assert out.total_fringe == ZERO
    assert out.health == ZERO


def test_fringe_benefits_returns_dataclass():
    """FringeBenefitResult schema — pin field names."""
    out = calculate_fringe_benefits(
        hours_total=Decimal("40"),
        fringe_rate=Decimal("5"),
    )
    assert isinstance(out, FringeBenefitResult)
    assert hasattr(out, "total_fringe")
    assert hasattr(out, "health")
    assert hasattr(out, "pension")
    assert hasattr(out, "vacation")
    assert hasattr(out, "training")
    assert hasattr(out, "other")


def test_fringe_benefits_partial_breakdown_other_defaults_zero():
    """Only health + pension specified → vacation/training/other use
    breakdown defaults (zero, since not in dict)."""
    out = calculate_fringe_benefits(
        hours_total=Decimal("100"),
        fringe_rate=Decimal("8"),
        fringe_breakdown={
            "health": Decimal("4"),
            "pension": Decimal("4"),
        },
    )
    assert out.health == Decimal("400.00")
    assert out.pension == Decimal("400.00")
    assert out.vacation == ZERO
    assert out.training == ZERO
    assert out.other == ZERO
