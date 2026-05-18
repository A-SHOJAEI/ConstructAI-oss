"""Schedule delay prediction using ML-based risk assessment.

Analyzes historical schedule data, weather forecasts, and resource
availability to predict potential delays before they occur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

logger = logging.getLogger(__name__)


@dataclass
class DelayRisk:
    activity_id: str
    activity_name: str
    risk_score: float  # 0.0 - 1.0
    predicted_delay_days: int
    risk_factors: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)


@dataclass
class DelayPrediction:
    project_id: str
    prediction_date: str
    overall_risk: float
    predicted_completion_date: str
    original_completion_date: str
    delay_days: int
    high_risk_activities: list[DelayRisk] = field(default_factory=list)
    weather_impact_days: int = 0
    resource_conflict_days: int = 0


# Risk factor weights (must sum to 1.0)
RISK_WEIGHTS = {
    "low_float": 0.27,
    "weather_exposure": 0.17,
    "resource_contention": 0.22,
    "predecessor_delay": 0.22,
    "complexity": 0.12,
}

# Activities exposed to weather
WEATHER_SENSITIVE_ACTIVITIES = {
    "excavation",
    "foundation",
    "concrete",
    "roofing",
    "exterior",
    "grading",
    "paving",
    "masonry",
    "steel_erection",
    "earthwork",
}


def _assess_float_risk(activity: dict) -> float:
    """Lower total float = higher risk of causing project delay."""
    total_float = activity.get("total_float", 0)
    if total_float <= 0:
        return 1.0  # Critical path
    if total_float <= 3:
        return 0.8
    if total_float <= 7:
        return 0.5
    if total_float <= 14:
        return 0.3
    return 0.1


def _assess_weather_risk(
    activity: dict,
    weather_data: list[dict] | None = None,
) -> float:
    """Assess weather-related delay risk."""
    name = (activity.get("name", "") + " " + activity.get("type", "")).lower()
    is_weather_sensitive = any(kw in name for kw in WEATHER_SENSITIVE_ACTIVITIES)

    if not is_weather_sensitive:
        return 0.0

    if not weather_data:
        return 0.3  # Default moderate risk for outdoor work

    # Count adverse weather days during activity window
    start = activity.get("early_start", 0)
    duration = activity.get("duration_days", 0)
    adverse_days = 0

    for day_data in weather_data:
        day_num = day_data.get("day", 0)
        if start <= day_num < start + duration and (
            day_data.get("precipitation_mm", 0) > 10
            or day_data.get("wind_speed_kmh", 0) > 50
            or day_data.get("temperature_c", 20) < -5
        ):
            adverse_days += 1

    if duration == 0:
        return 0.0
    return min(1.0, adverse_days / max(duration * 0.3, 1))


def _assess_resource_risk(
    activity: dict,
    all_activities: list[dict],
) -> float:
    """Assess resource contention risk."""
    resources = activity.get("resources", {})
    if not resources:
        return 0.0

    start = activity.get("early_start", 0)
    duration = activity.get("duration_days", 0)

    # Count concurrent activities using same resources
    conflicts = 0
    for other in all_activities:
        if other.get("id") == activity.get("id"):
            continue
        o_start = other.get("early_start", 0)
        o_duration = other.get("duration_days", 0)
        o_end = o_start + o_duration
        act_end = start + duration

        # Check temporal overlap
        if o_start < act_end and o_end > start:
            other_resources = other.get("resources", {})
            shared = set(resources) & set(other_resources)
            if shared:
                conflicts += 1

    if conflicts == 0:
        return 0.0
    if conflicts <= 2:
        return 0.3
    if conflicts <= 5:
        return 0.6
    return 0.9


def _assess_predecessor_risk(
    activity: dict,
    activities_by_id: dict[str, dict],
) -> float:
    """Assess risk from predecessor delays."""
    predecessors = activity.get("predecessors", [])
    if not predecessors:
        return 0.0

    max_pred_risk = 0.0
    for pred_id in predecessors:
        pred = activities_by_id.get(str(pred_id))
        if not pred:
            continue
        pred_float = pred.get("total_float", 0)
        if pred_float <= 0:
            max_pred_risk = max(max_pred_risk, 0.8)
        elif pred_float <= 3:
            max_pred_risk = max(max_pred_risk, 0.5)
        elif pred_float <= 7:
            max_pred_risk = max(max_pred_risk, 0.3)

    return max_pred_risk


def _assess_complexity_risk(activity: dict) -> float:
    """Assess risk from task complexity."""
    duration = activity.get("duration_days", 0)
    num_resources = len(activity.get("resources", {}))
    num_predecessors = len(activity.get("predecessors", []))

    score = 0.0
    if duration > 20:
        score += 0.3
    elif duration > 10:
        score += 0.15
    if num_resources > 3:
        score += 0.2
    if num_predecessors > 3:
        score += 0.2

    return min(1.0, score)


def _recommend_actions(risk: DelayRisk) -> list[str]:
    """Generate recommended mitigation actions based on risk factors."""
    actions = []
    for factor in risk.risk_factors:
        if "float" in factor.lower():
            actions.append("Add buffer time or fast-track predecessors")
        elif "weather" in factor.lower():
            actions.append("Schedule weather-sensitive work during favorable windows")
            actions.append("Prepare contingency plans for adverse weather")
        elif "resource" in factor.lower():
            actions.append("Pre-book resources or identify backup crews")
            actions.append("Consider resource leveling to reduce contention")
        elif "predecessor" in factor.lower():
            actions.append("Monitor predecessor progress closely")
            actions.append("Identify acceleration options for predecessor tasks")
        elif "complexity" in factor.lower():
            actions.append("Break into smaller work packages")
            actions.append("Assign experienced crew leads")
    return actions


async def predict_delays(
    project_id: str,
    activities: list[dict],
    weather_data: list[dict] | None = None,
    target_completion: str | None = None,
) -> DelayPrediction:
    """Predict potential schedule delays for a project.

    Parameters
    ----------
    project_id:
        Project identifier.
    activities:
        Schedule activities with CPM data.
    weather_data:
        Optional weather forecast data per day.
    target_completion:
        Original target completion date (ISO format).

    Returns
    -------
    DelayPrediction with risk assessment.
    """
    activities_by_id = {str(a.get("id", "")): a for a in activities}
    risks: list[DelayRisk] = []

    for activity in activities:
        risk_factors = []
        weighted_score = 0.0

        # Assess each risk factor
        float_risk = _assess_float_risk(activity)
        weather_risk = _assess_weather_risk(activity, weather_data)
        resource_risk = _assess_resource_risk(activity, activities)
        predecessor_risk = _assess_predecessor_risk(activity, activities_by_id)
        complexity_risk = _assess_complexity_risk(activity)

        weighted_score += float_risk * RISK_WEIGHTS["low_float"]
        weighted_score += weather_risk * RISK_WEIGHTS["weather_exposure"]
        weighted_score += resource_risk * RISK_WEIGHTS["resource_contention"]
        weighted_score += predecessor_risk * RISK_WEIGHTS["predecessor_delay"]
        weighted_score += complexity_risk * RISK_WEIGHTS["complexity"]

        if float_risk > 0.5:
            risk_factors.append(f"Low float ({activity.get('total_float', 0)} days)")
        if weather_risk > 0.3:
            risk_factors.append("Weather exposure risk")
        if resource_risk > 0.3:
            risk_factors.append("Resource contention")
        if predecessor_risk > 0.3:
            risk_factors.append("Predecessor delay risk")
        if complexity_risk > 0.3:
            risk_factors.append("High complexity")

        # Estimate delay days based on risk score
        duration = activity.get("duration_days", 0)
        predicted_delay = round(weighted_score * duration * 0.3)

        risk = DelayRisk(
            activity_id=str(activity.get("id", "")),
            activity_name=activity.get("name", "Unknown"),
            risk_score=round(weighted_score, 3),
            predicted_delay_days=predicted_delay,
            risk_factors=risk_factors,
        )
        risk.recommended_actions = _recommend_actions(risk)
        risks.append(risk)

    # Sort by risk score descending
    risks.sort(key=lambda r: r.risk_score, reverse=True)

    # Calculate overall metrics
    high_risk = [r for r in risks if r.risk_score > 0.5]
    overall_risk = sum(r.risk_score for r in risks) / len(risks) if risks else 0.0
    total_predicted_delay = sum(r.predicted_delay_days for r in high_risk)

    # Weather impact
    weather_days = sum(1 for r in risks if any("Weather" in f for f in r.risk_factors))

    # Resource conflicts
    resource_days = sum(
        r.predicted_delay_days for r in risks if any("Resource" in f for f in r.risk_factors)
    )

    # Dates
    today = date.today()
    original_date = target_completion or (today + timedelta(days=180)).isoformat()
    predicted_date = (
        date.fromisoformat(original_date) + timedelta(days=total_predicted_delay)
    ).isoformat()

    prediction = DelayPrediction(
        project_id=project_id,
        prediction_date=today.isoformat(),
        overall_risk=round(overall_risk, 3),
        predicted_completion_date=predicted_date,
        original_completion_date=original_date,
        delay_days=total_predicted_delay,
        high_risk_activities=high_risk[:10],
        weather_impact_days=weather_days,
        resource_conflict_days=resource_days,
    )

    logger.info(
        "Delay prediction for %s: overall_risk=%.2f, predicted_delay=%d days, "
        "%d high-risk activities",
        project_id,
        overall_risk,
        total_predicted_delay,
        len(high_risk),
    )

    return prediction
