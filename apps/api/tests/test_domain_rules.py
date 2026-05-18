"""Tests for the guardrails domain-rule validators.

CSI MasterFormat / OSHA citation / RSMeans cost ranges are domain-
specific sanity checks the guardrail pipeline runs against agent
output. Pin every branch — these guard against the LLM emitting
plausibly-shaped but invalid construction data.
"""

from __future__ import annotations

import pytest

from app.services.guardrails.domain_rules import (
    CSIMasterFormatValidator,
    OSHACitationValidator,
    RSMeansCostRangeValidator,
    validate_domain,
)

# =========================================================================
# CSIMasterFormatValidator
# =========================================================================


def test_csi_validator_accepts_valid_three_pair():
    v = CSIMasterFormatValidator()
    ok, msg = v.validate("03 30 00")  # cast-in-place concrete
    assert ok is True
    assert msg == ""


def test_csi_validator_accepts_decimal_subsection():
    v = CSIMasterFormatValidator()
    ok, _ = v.validate("03 30 00.13")
    assert ok is True


def test_csi_validator_accepts_no_spaces():
    v = CSIMasterFormatValidator()
    ok, _ = v.validate("033000")
    assert ok is True


def test_csi_validator_rejects_garbage_format():
    v = CSIMasterFormatValidator()
    ok, msg = v.validate("not a csi code")
    assert ok is False
    assert "Invalid CSI format" in msg


def test_csi_validator_rejects_division_above_50():
    """Divisions 00-50 are valid (per MasterFormat 2018). Division 99
    is shaped correctly but not a real division."""
    v = CSIMasterFormatValidator()
    ok, msg = v.validate("99 99 00")
    assert ok is False
    assert "Invalid CSI division" in msg


def test_csi_validator_division_zero_accepted():
    """Division 00 = procurement & contracting requirements. Valid."""
    v = CSIMasterFormatValidator()
    ok, _ = v.validate("00 11 13")
    assert ok is True


# =========================================================================
# OSHACitationValidator
# =========================================================================


async def test_osha_validator_accepts_known_citation():
    v = OSHACitationValidator()
    ok, msg = await v.validate("29 CFR 1926.501")  # fall protection
    assert ok is True
    assert msg == ""


async def test_osha_validator_accepts_subsection_letter():
    v = OSHACitationValidator()
    ok, _ = await v.validate("29 CFR 1926.501(b)")
    assert ok is True


async def test_osha_validator_rejects_non_construction_part():
    """The validator only knows 1926 (construction). 1910 is general
    industry."""
    v = OSHACitationValidator()
    ok, msg = await v.validate("29 CFR 1910.134")
    assert ok is False
    assert "Invalid OSHA citation format" in msg


async def test_osha_validator_returns_warning_for_unknown_section():
    """Format-valid but not in the known list — accepted with a
    diagnostic message rather than rejected (the list is not
    exhaustive)."""
    v = OSHACitationValidator()
    ok, msg = await v.validate("29 CFR 1926.999")  # made-up section
    assert ok is True
    assert "not in known list" in msg


async def test_osha_validator_rejects_garbage():
    v = OSHACitationValidator()
    ok, _ = await v.validate("not a citation")
    assert ok is False


# =========================================================================
# RSMeansCostRangeValidator
# =========================================================================


def test_rsmeans_concrete_within_range():
    v = RSMeansCostRangeValidator()
    ok, msg = v.validate("03 30 00", unit_cost=200.0)  # within 150-800
    assert ok is True
    assert msg == ""


def test_rsmeans_concrete_below_range_rejected():
    v = RSMeansCostRangeValidator()
    ok, msg = v.validate("03 30 00", unit_cost=10.0)  # well below 150
    assert ok is False
    assert "below" in msg.lower()


def test_rsmeans_concrete_above_range_rejected():
    v = RSMeansCostRangeValidator()
    ok, msg = v.validate("03 30 00", unit_cost=5_000.0)  # well above 800
    assert ok is False
    assert "above" in msg.lower()


def test_rsmeans_tolerance_extends_acceptable_band():
    """Default tolerance is 30% — a value 25% below the low end should
    still be accepted."""
    v = RSMeansCostRangeValidator()
    # Concrete low=150, with 30% tolerance: 150 * 0.7 = 105.
    ok, _ = v.validate("03 30 00", unit_cost=110.0)
    assert ok is True


def test_rsmeans_unknown_division_passes_through():
    """If we don't have a benchmark for this division, accept the
    cost — never fail on unknown divisions, just on known-out-of-range."""
    v = RSMeansCostRangeValidator()
    ok, msg = v.validate("99 99 00", unit_cost=10.0)
    assert ok is True
    assert msg == ""


def test_rsmeans_tighter_tolerance_rejects_what_default_accepts():
    """Caller can pass a tighter tolerance for stricter validation."""
    v = RSMeansCostRangeValidator()
    # 110 is within 30% but not within 10% of low=150.
    ok_default, _ = v.validate("03 30 00", unit_cost=110.0)
    ok_strict, _ = v.validate("03 30 00", unit_cost=110.0, tolerance=0.10)
    assert ok_default is True
    assert ok_strict is False


# =========================================================================
# validate_domain — orchestrator
# =========================================================================


async def test_validate_domain_no_relevant_fields_returns_no_violations():
    """When the parsed_output has no CSI / OSHA / cost fields, there's
    nothing to validate — no violations recorded."""
    out = await validate_domain({"unrelated": "field"}, "estimating_agent")
    assert out == {"violations": []}


async def test_validate_domain_records_csi_violation():
    out = await validate_domain({"csi_code": "99 99 00"}, "any")
    violations = out["violations"]
    assert len(violations) == 1
    assert violations[0]["rule"] == "csi_masterformat"
    assert violations[0]["severity"] == "error"


@pytest.mark.parametrize("field_name", ["csi_code", "division_code", "masterformat_code"])
async def test_validate_domain_recognises_all_csi_field_names(field_name):
    """Three different field names map to CSI validation — pin them so
    a refactor can't accidentally drop one."""
    out = await validate_domain({field_name: "99 99 00"}, "any")
    assert len(out["violations"]) == 1


async def test_validate_domain_records_osha_violation():
    out = await validate_domain({"osha_citation": "garbage"}, "any")
    violations = out["violations"]
    assert any(v["rule"] == "osha_citation" for v in violations)


async def test_validate_domain_records_rsmeans_violation_as_warning():
    """RSMeans is a softer rule (advisory) — recorded as ``warning``
    rather than ``error``."""
    out = await validate_domain({"csi_code": "03 30 00", "unit_cost": 5_000.0}, "any")
    violations = out["violations"]
    assert any(v["rule"] == "rsmeans_range" and v["severity"] == "warning" for v in violations)


async def test_validate_domain_collects_multiple_violations():
    """All three rules can fire on a single output — the result must
    record each."""
    out = await validate_domain(
        {
            "csi_code": "99 99 00",  # CSI division invalid
            "osha_citation": "garbage",  # OSHA format invalid
            "unit_cost": 1_000_000.0,  # RSMeans out of range (CSI was bad
            # but the unit_cost branch needs csi_code too — uses the
            # invalid-format string anyway, which yields the unknown-
            # division pass-through, so no rsmeans violation here).
        },
        "any",
    )
    rules = {v["rule"] for v in out["violations"]}
    assert "csi_masterformat" in rules
    assert "osha_citation" in rules
