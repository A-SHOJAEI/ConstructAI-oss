"""Five-level severity assignment for safety violations."""

from __future__ import annotations

SEVERITY_RULES: dict[tuple[str, str], str] = {
    ("restricted", "zone_breach"): "P1_critical",
    ("crane_swing", "zone_breach"): "P1_critical",
    ("excavation", "zone_breach"): "P1_critical",
    ("ppe_required", "missing_hardhat"): "P2_high",
    ("ppe_required", "missing_vest"): "P2_high",
    ("ppe_required", "missing_gloves"): "P2_high",
    ("equipment_only", "zone_breach"): "P3_medium",
    ("general", "missing_hardhat"): "P3_medium",
    ("pedestrian_only", "zone_breach"): "P4_low",
    ("general", "missing_vest"): "P4_low",
}

SEVERITY_ORDER = ["P1_critical", "P2_high", "P3_medium", "P4_low", "P5_info"]


def classify_severity(
    zone_type: str,
    violation_type: str,
    confidence: float = 1.0,
    severity_override: str | None = None,
) -> str:
    """Classify severity of a safety violation.

    The ``severity_override`` may only ESCALATE severity (move to a
    higher priority level, i.e., a lower index in ``SEVERITY_ORDER``).
    Attempts to downgrade are silently ignored to prevent malicious
    suppression of safety alerts.
    """
    # Compute the base severity before override
    base = SEVERITY_RULES.get((zone_type, violation_type), "P5_info")

    # Low confidence downgrades by one level
    if confidence < 0.6 and base != "P5_info":
        idx = SEVERITY_ORDER.index(base)
        base = SEVERITY_ORDER[min(idx + 1, len(SEVERITY_ORDER) - 1)]

    # Apply override only if it escalates (lower index = higher severity)
    if severity_override and severity_override in SEVERITY_ORDER:
        override_idx = SEVERITY_ORDER.index(severity_override)
        base_idx = SEVERITY_ORDER.index(base)
        if override_idx < base_idx:
            # Override is more severe — allow escalation
            return severity_override
        # Override would downgrade — ignore it

    return base
