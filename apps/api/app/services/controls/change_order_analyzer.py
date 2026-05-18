"""AI-powered change order analysis."""

from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Project-type-specific thresholds
# ---------------------------------------------------------------------------

_PROJECT_TYPE_THRESHOLDS: dict[str, dict[str, Decimal]] = {
    "commercial": {
        "cost_high": Decimal("5"),
        "cost_medium": Decimal("2"),
    },
    "infrastructure": {
        "cost_high": Decimal("3"),
        "cost_medium": Decimal("1"),
    },
    "residential": {
        "cost_high": Decimal("8"),
        "cost_medium": Decimal("4"),
    },
}

# ---------------------------------------------------------------------------
# Risk-level-specific recommendations
# ---------------------------------------------------------------------------

_RISK_RECOMMENDATIONS: dict[str, list[str]] = {
    "high": [
        "Requires executive review and formal approval before proceeding",
        "Conduct detailed impact analysis on critical path activities",
        "Engage cost estimator for independent cost verification",
        "Review bonding and insurance implications",
        "Evaluate potential claims or dispute exposure",
        "Schedule formal change control board (CCB) meeting",
    ],
    "medium": [
        "Project manager review and approval recommended",
        "Update cost forecast and contingency allocation",
        "Assess impact on current schedule milestones",
        "Document root cause to prevent recurrence",
        "Notify owner/client of potential schedule or cost effects",
    ],
    "low": [
        "Standard project manager approval sufficient",
        "Update project logs and tracking registers",
        "Monitor for potential cumulative effects from similar changes",
    ],
}


async def analyze_change_order(
    title: str,
    description: str,
    change_type: str,
    cost_impact: Decimal,
    schedule_impact_days: int,
    project_budget: Decimal | None = None,
    project_duration_days: int | None = None,
    project_type: str = "commercial",
    cumulative_changes: list[dict] | None = None,
) -> dict:
    """Analyze a change order for risk and impact.

    Parameters
    ----------
    title: Change order title
    description: Change order description
    change_type: Type of change (owner_directed, field_condition,
        design_error, value_engineering, regulatory)
    cost_impact: Cost impact amount
    schedule_impact_days: Schedule impact in days
    project_budget: Optional total project budget
    project_duration_days: Optional total project duration in days
    project_type: Project type for threshold selection
        ("commercial", "infrastructure", "residential")
    cumulative_changes: Optional list of previous change orders, each with
        "cost_impact" (Decimal/float) and "schedule_impact" (int) keys,
        for cumulative impact tracking.

    Returns risk score (0-10), category analysis,
    and actionable recommendations.
    """
    # Get thresholds for project type
    thresholds = _PROJECT_TYPE_THRESHOLDS.get(
        project_type,
        _PROJECT_TYPE_THRESHOLDS["commercial"],
    )
    cost_high_threshold = thresholds["cost_high"]
    cost_medium_threshold = thresholds["cost_medium"]

    # Risk scoring based on multiple factors
    risk_factors = []
    risk_score = Decimal("0")

    # Cost impact relative to project budget
    # Guard against small-budget division amplification: if the project
    # budget is unreasonably small (< $1,000), percentage calculations
    # would produce misleadingly large values, so we cap cost_pct.
    if project_budget and project_budget > 0:
        cost_pct = abs(cost_impact) / project_budget * 100
        if project_budget < Decimal("1000"):
            cost_pct = min(cost_pct, Decimal("1000"))
        if cost_pct > cost_high_threshold:
            risk_factors.append(
                f"High cost impact: {cost_pct:.1f}% of budget "
                f"(threshold: {cost_high_threshold}% for {project_type})"
            )
            risk_score += Decimal("3")
        elif cost_pct > cost_medium_threshold:
            risk_factors.append(
                f"Moderate cost impact: {cost_pct:.1f}% of budget "
                f"(threshold: {cost_medium_threshold}% for {project_type})"
            )
            risk_score += Decimal("2")
        else:
            risk_score += Decimal("1")
    elif abs(cost_impact) > 100000:
        risk_factors.append("Large absolute cost impact")
        risk_score += Decimal("2.5")

    # Schedule impact (use absolute value so negative/acceleration days
    # are scored by magnitude, not direction)
    abs_schedule_days = abs(schedule_impact_days)
    if abs_schedule_days > 30:
        risk_factors.append(f"Major schedule impact: {schedule_impact_days} days")
        risk_score += Decimal("3")
    elif abs_schedule_days > 14:
        risk_factors.append(f"Moderate schedule impact: {schedule_impact_days} days")
        risk_score += Decimal("2")
    elif abs_schedule_days > 0:
        risk_score += Decimal("1")

    # Change type risk weighting
    type_weights = {
        "owner_directed": Decimal("1"),
        "field_condition": Decimal("2"),
        "design_error": Decimal("2.5"),
        "value_engineering": Decimal("0.5"),
        "regulatory": Decimal("3"),
    }
    type_risk = type_weights.get(change_type, Decimal("1.5"))
    risk_score += type_risk

    # Cap risk score at 10
    risk_score = min(risk_score, Decimal("10"))

    # Determine initial risk level
    if risk_score >= 7:
        risk_level = "high"
    elif risk_score >= 4:
        risk_level = "medium"
    else:
        risk_level = "low"

    # -----------------------------------------------------------------------
    # Cumulative impact tracking
    # -----------------------------------------------------------------------
    cumulative_assessment = None
    if cumulative_changes is not None:
        cum_cost = sum(Decimal(str(co.get("cost_impact", 0))) for co in cumulative_changes)
        cum_schedule = sum(int(co.get("schedule_impact", 0)) for co in cumulative_changes)

        # Include current change order in cumulative totals
        cum_cost_total = cum_cost + cost_impact
        cum_schedule_total = cum_schedule + schedule_impact_days
        num_changes = len(cumulative_changes) + 1

        cumulative_assessment = {
            "total_changes": num_changes,
            "cumulative_cost_impact": str(cum_cost_total),
            "cumulative_schedule_impact_days": cum_schedule_total,
        }

        # Auto-escalate to critical if cumulative cost > 15% of budget
        if project_budget and project_budget > 0:
            cum_cost_pct = abs(cum_cost_total) / project_budget * 100
            cumulative_assessment["cumulative_cost_pct"] = float(round(cum_cost_pct, 2))
            if cum_cost_pct > Decimal("15"):
                risk_level = "critical"
                risk_factors.append(
                    f"CRITICAL: Cumulative cost impact {cum_cost_pct:.1f}% "
                    f"exceeds 15% of project budget"
                )
                risk_score = Decimal("10")
            elif cum_cost_pct > Decimal("10"):
                risk_factors.append(
                    f"Warning: Cumulative cost impact {cum_cost_pct:.1f}% approaching 15% threshold"
                )
                if risk_level == "low":
                    risk_level = "medium"

    # -----------------------------------------------------------------------
    # Actionable recommendations based on risk level
    # -----------------------------------------------------------------------
    recommendations = list(
        _RISK_RECOMMENDATIONS.get(
            risk_level if risk_level != "critical" else "high",
            _RISK_RECOMMENDATIONS["low"],
        )
    )

    # Critical-level additions
    if risk_level == "critical":
        recommendations.insert(
            0,
            "CRITICAL: Cumulative changes exceed budget threshold - "
            "halt non-essential changes pending executive review",
        )
        recommendations.append("Conduct comprehensive project re-baseline assessment")

    # Context-specific recommendations
    if schedule_impact_days > 0:
        recommendations.append("Update project schedule with revised durations")
    if cost_impact > 0:
        recommendations.append("Review contingency budget allocation")
    elif cost_impact < 0:
        recommendations.append("Deductive change order: verify credit is reflected in budget")

    if change_type == "design_error":
        recommendations.append("Investigate design QA/QC process to prevent future errors")
    elif change_type == "field_condition":
        recommendations.append("Review site investigation reports for similar conditions")
    elif change_type == "regulatory":
        recommendations.append("Verify all related code sections for cascading requirements")

    result = {
        "risk_score": float(round(risk_score, 2)),
        "risk_level": risk_level,
        "risk_factors": risk_factors,
        "project_type": project_type,
        "change_type_analysis": {
            "type": change_type,
            "type_risk_weight": str(type_risk),
        },
        "impact_summary": {
            "cost_impact": str(cost_impact),
            "schedule_impact_days": schedule_impact_days,
        },
        "recommendations": recommendations,
    }

    if cumulative_assessment is not None:
        result["cumulative_assessment"] = cumulative_assessment

    logger.info(
        "Change order analyzed: risk_score=%.2f, level=%s, type=%s",
        risk_score,
        result["risk_level"],
        project_type,
    )
    return result
