"""Tests for the safety-violation severity classifier.

Pure lookup + business rules. The "override only escalates" property is
the security invariant that protects against malicious downgrade of
P1/P2 alerts.
"""

from __future__ import annotations

from app.services.safety.severity_classifier import (
    SEVERITY_ORDER,
    SEVERITY_RULES,
    classify_severity,
)

# ---- base mapping -------------------------------------------------------


def test_restricted_zone_breach_is_critical():
    assert classify_severity("restricted", "zone_breach") == "P1_critical"


def test_crane_swing_zone_breach_is_critical():
    assert classify_severity("crane_swing", "zone_breach") == "P1_critical"


def test_ppe_missing_hardhat_is_high():
    assert classify_severity("ppe_required", "missing_hardhat") == "P2_high"


def test_pedestrian_only_zone_breach_is_low():
    assert classify_severity("pedestrian_only", "zone_breach") == "P4_low"


def test_unknown_combination_is_info():
    assert classify_severity("ghost-zone", "ghost-violation") == "P5_info"


# ---- low-confidence downgrade ------------------------------------------


def test_low_confidence_downgrades_one_level():
    """Confidence < 0.6 demotes the alert by one level — protects
    against false-positive flooding when CV detection is uncertain."""
    high_conf = classify_severity("ppe_required", "missing_hardhat", confidence=0.95)
    low_conf = classify_severity("ppe_required", "missing_hardhat", confidence=0.4)
    assert high_conf == "P2_high"
    assert low_conf == "P3_medium"


def test_low_confidence_does_not_demote_below_info():
    """``P5_info`` is the floor — no further demotion possible."""
    assert classify_severity("unknown", "unknown", confidence=0.1) == "P5_info"


def test_confidence_at_threshold_does_not_demote():
    """0.6 is the cutoff; values >= 0.6 keep the original level."""
    assert classify_severity("ppe_required", "missing_hardhat", confidence=0.6) == "P2_high"


def test_low_confidence_demotes_critical_to_high():
    out = classify_severity("restricted", "zone_breach", confidence=0.5)
    assert out == "P2_high"


# ---- severity_override (escalate-only) ----------------------------------


def test_override_escalates_to_higher_severity():
    """Operator can manually escalate a P3 to P1."""
    out = classify_severity(
        "general",
        "missing_hardhat",
        severity_override="P1_critical",
    )
    assert out == "P1_critical"


def test_override_cannot_downgrade_severity():
    """Security invariant: override-only-escalates. Trying to demote a
    P1 to P5 must be ignored."""
    out = classify_severity(
        "restricted",
        "zone_breach",
        severity_override="P5_info",
    )
    assert out == "P1_critical"


def test_override_cannot_downgrade_p2_to_p3():
    out = classify_severity(
        "ppe_required",
        "missing_hardhat",
        severity_override="P4_low",
    )
    assert out == "P2_high"


def test_override_at_same_level_keeps_base():
    out = classify_severity(
        "ppe_required",
        "missing_hardhat",
        severity_override="P2_high",
    )
    assert out == "P2_high"


def test_invalid_override_label_ignored():
    """A malformed override (not in SEVERITY_ORDER) must NOT crash —
    just falls through to the base classification."""
    out = classify_severity(
        "ppe_required",
        "missing_hardhat",
        severity_override="bogus-level",
    )
    assert out == "P2_high"


def test_override_with_low_confidence_can_still_escalate():
    """Confidence demotes from P2 → P3, then operator can override
    back up to P1. Both rules apply in sequence."""
    out = classify_severity(
        "ppe_required",
        "missing_hardhat",
        confidence=0.3,  # would demote to P3
        severity_override="P1_critical",  # operator override
    )
    assert out == "P1_critical"


# ---- ordering invariant -------------------------------------------------


def test_severity_order_is_strict_priority_descending():
    """SEVERITY_ORDER must be sorted from most severe to least —
    lower index = higher priority. Several other rules depend on this
    invariant (the override-escalate-only check, the demotion floor)."""
    assert SEVERITY_ORDER[0] == "P1_critical"
    assert SEVERITY_ORDER[-1] == "P5_info"


def test_every_rule_target_is_in_severity_order():
    """Every value in SEVERITY_RULES must be a known severity label —
    otherwise the override-escalate logic would crash with ValueError
    on .index()."""
    for value in SEVERITY_RULES.values():
        assert value in SEVERITY_ORDER
