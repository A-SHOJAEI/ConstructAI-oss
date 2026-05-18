"""Stage 6: Routing decision based on confidence thresholds."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Confidence thresholds per agent output type
# auto: minimum confidence for auto-approval
# human: minimum confidence for human review (below = expert)
ROUTING_THRESHOLDS: dict[str, dict[str, float | None]] = {
    "document_classification": {"auto": 0.90, "human": 0.70},
    "cost_estimate": {"auto": 0.85, "human": 0.60},
    "schedule_analysis": {"auto": 0.85, "human": 0.65},
    # SECURITY (C-11): Safety alerts must NEVER be auto-approved.
    # All safety-related decisions require human review regardless of
    # model confidence.  Previously set to auto=0.95 which allowed
    # bypassing human oversight for safety-critical decisions.
    "safety_alert": {"auto": None, "human": 0.0},
    "change_order_impact": {"auto": None, "human": 0.0},
    "daily_report": {"auto": 0.80, "human": 0.50},
    "rfi_draft": {"auto": None, "human": 0.0},
    "evm_snapshot": {"auto": 0.85, "human": 0.65},
    "quality_inspection": {"auto": 0.85, "human": 0.65},
}

# SECURITY [L-12]: Default thresholds for unknown agent types always require
# human review (auto=None). This is the safest default — unknown agents must
# never be auto-approved.
DEFAULT_THRESHOLDS = {"auto": None, "human": 0.60}


def decide_route(
    confidence: float,
    agent_name: str,
    validation_errors: list[dict],
) -> str:
    """Decide routing based on confidence and errors.

    Returns: "auto_approve", "human_review", or
    "expert_escalation"
    """
    # Any errors always require at least human review
    has_errors = any(e.get("severity") == "error" for e in validation_errors)
    if has_errors:
        return "expert_escalation"

    thresholds = ROUTING_THRESHOLDS.get(
        agent_name,
        DEFAULT_THRESHOLDS,
    )

    auto_threshold = thresholds.get("auto")
    human_threshold = thresholds.get("human", 0.0) or 0.0

    # Always-human-review agents (auto threshold is None)
    if auto_threshold is None:
        return "human_review"

    if confidence >= auto_threshold:
        return "auto_approve"
    if confidence >= human_threshold:
        return "human_review"
    return "expert_escalation"
