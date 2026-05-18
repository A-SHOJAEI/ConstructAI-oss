"""Bid/No-Bid Decision Engine — pure computational scoring.

No database, no LLM, no I/O. Takes dicts in, returns dicts out.
Designed for testability and deterministic behavior.

12 weighted scoring factors evaluate bid opportunities against
organizational context. Cold-start blending transitions from
industry averages to org-specific data as bid history grows.

Cross-project analytics integration (IG-03): ``get_historical_bid_context``
queries org-wide performance patterns and feeds them into the bid scoring
pipeline to improve decision quality based on historical outcomes.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDUSTRY_WIN_RATES: dict[str, float] = {
    "hard_bid": 0.12,
    "negotiated": 0.25,
    "design_build": 0.20,
    "cmar": 0.22,
    "ipd": 0.30,
}

DEFAULT_INDUSTRY_WIN_RATE = 0.18

DEFAULT_FACTOR_WEIGHTS: dict[str, float] = {
    "historical_win_rate": 0.15,
    "owner_relationship": 0.12,
    "backlog_capacity": 0.12,
    "geographic_familiarity": 0.10,
    "project_size_fit": 0.08,
    "delivery_method_expertise": 0.08,
    "competition_level": 0.08,
    "estimating_availability": 0.07,
    "bonding_impact": 0.05,
    "strategic_alignment": 0.05,
    "risk_profile": 0.05,
    "margin_potential": 0.05,
}

# Recommendation thresholds
THRESHOLD_STRONG_PURSUE = 75
THRESHOLD_PURSUE = 55
THRESHOLD_CONDITIONAL = 40

_EARTH_RADIUS_KM = 6371.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FactorScore:
    """Result of scoring a single factor."""

    score: float  # 0-100
    weight: float
    reasoning: str

    @property
    def weighted_score(self) -> float:
        return self.score * self.weight


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _blend_factor(
    org_value: float,
    industry_value: float,
    bid_count: int,
    min_bids: int = 5,
    full_bids: int = 20,
) -> float:
    """Blend between industry average and org-specific value.

    - < min_bids: pure industry value
    - >= full_bids: pure org value
    - In between: linear interpolation
    """
    if bid_count < min_bids:
        return industry_value
    if bid_count >= full_bids:
        return org_value
    ratio = (bid_count - min_bids) / (full_bids - min_bids)
    return industry_value * (1 - ratio) + org_value * ratio


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class BidDecisionEngine:
    """Scores bid opportunities using 12 weighted factors.

    Args:
        custom_weights: Optional dict overriding DEFAULT_FACTOR_WEIGHTS.
    """

    def __init__(self, custom_weights: dict[str, float] | None = None):
        self.weights = dict(DEFAULT_FACTOR_WEIGHTS)
        if custom_weights:
            for k, v in custom_weights.items():
                if k in self.weights:
                    self.weights[k] = v
            # Re-normalize to sum to 1.0
            total = sum(self.weights.values())
            if total > 0:
                self.weights = {k: v / total for k, v in self.weights.items()}

    def score_opportunity(self, opportunity: dict, org_context: dict) -> dict:
        """Score a bid opportunity against org context.

        Args:
            opportunity: Dict with keys: name, project_type, delivery_method,
                estimated_value, location, latitude, longitude, owner_name, etc.
            org_context: Dict with keys: bid_count, win_count, bids_by_type,
                bids_by_method, office_latitude, office_longitude,
                previous_owners, current_backlog, max_capacity,
                estimating_capacity, bonding_capacity, bonding_used,
                strategic_sectors, avg_project_size, competitors, etc.

        Returns:
            Dict with composite_score, recommendation, win_probability,
            factor_scores (detailed breakdown), and status.
        """
        factor_scores = {}
        scoring_methods = {
            "historical_win_rate": self._score_historical_win_rate,
            "owner_relationship": self._score_owner_relationship,
            "backlog_capacity": self._score_backlog_capacity,
            "geographic_familiarity": self._score_geographic_familiarity,
            "project_size_fit": self._score_project_size_fit,
            "delivery_method_expertise": self._score_delivery_method_expertise,
            "competition_level": self._score_competition_level,
            "estimating_availability": self._score_estimating_availability,
            "bonding_impact": self._score_bonding_impact,
            "strategic_alignment": self._score_strategic_alignment,
            "risk_profile": self._score_risk_profile,
            "margin_potential": self._score_margin_potential,
        }

        for factor_name, method in scoring_methods.items():
            weight = self.weights.get(factor_name, 0.0)
            try:
                fs = method(opportunity, org_context)
                fs.weight = weight
            except Exception:
                fs = FactorScore(score=50.0, weight=weight, reasoning="Unable to evaluate")
            factor_scores[factor_name] = fs

        composite = self._compute_composite_score(factor_scores)
        recommendation = self._generate_recommendation(composite)
        delivery_method = opportunity.get("delivery_method", "hard_bid")
        win_prob = self._calibrate_win_probability(composite, delivery_method)

        return {
            "composite_score": composite,
            "recommendation": recommendation,
            "win_probability": win_prob,
            "factor_scores": {
                name: {
                    "score": round(fs.score, 1),
                    "weight": round(fs.weight, 3),
                    "weighted_score": round(fs.weighted_score, 1),
                    "reasoning": fs.reasoning,
                }
                for name, fs in factor_scores.items()
            },
            "status": "scored",
        }

    # ------------------------------------------------------------------
    # Individual scoring factors
    # ------------------------------------------------------------------

    def _score_historical_win_rate(self, opp: dict, ctx: dict) -> FactorScore:
        """Score based on org's historical win rate for this project type."""
        bid_count = ctx.get("bid_count", 0)
        win_count = ctx.get("win_count", 0)
        project_type = opp.get("project_type", "commercial")

        # Type-specific win rate
        bids_by_type = ctx.get("bids_by_type", {})
        type_data = bids_by_type.get(project_type, {})
        type_bids = type_data.get("total", 0)
        type_wins = type_data.get("won", 0)

        org_win_rate = (
            type_wins / type_bids
            if type_bids > 0
            else (win_count / bid_count if bid_count > 0 else 0.0)
        )
        industry_rate = INDUSTRY_WIN_RATES.get(
            opp.get("delivery_method", "hard_bid"), DEFAULT_INDUSTRY_WIN_RATE
        )

        blended = _blend_factor(org_win_rate, industry_rate, bid_count)
        # Map win rate to score: 0% → 20, 15% → 50, 30%+ → 90
        score = _clamp(20 + (blended / 0.30) * 70)

        return FactorScore(
            score=score,
            weight=0.0,
            reasoning=f"Win rate {blended:.0%} (org: {org_win_rate:.0%}, "
            f"industry: {industry_rate:.0%}, {bid_count} total bids)",
        )

    def _score_owner_relationship(self, opp: dict, ctx: dict) -> FactorScore:
        """Score based on previous work with the project owner."""
        owner = opp.get("owner_name", "")
        previous_owners = ctx.get("previous_owners", {})

        if not owner or not previous_owners:
            return FactorScore(score=50.0, weight=0.0, reasoning="No owner history available")

        owner_lower = owner.lower()
        owner_data = None
        for key, data in previous_owners.items():
            if key.lower() == owner_lower:
                owner_data = data
                break

        if owner_data is None:
            return FactorScore(score=30.0, weight=0.0, reasoning=f"No prior work with {owner}")

        project_count = owner_data.get("projects", 0)
        satisfaction = owner_data.get("satisfaction", 0.5)
        # More projects + higher satisfaction → higher score
        relationship_score = _clamp(40 + project_count * 10 + satisfaction * 30)

        return FactorScore(
            score=relationship_score,
            weight=0.0,
            reasoning=f"{project_count} prior projects with {owner}, "
            f"satisfaction: {satisfaction:.0%}",
        )

    def _score_backlog_capacity(self, opp: dict, ctx: dict) -> FactorScore:
        """Score based on current backlog vs max capacity."""
        current_backlog = ctx.get("current_backlog", 0)
        max_capacity = ctx.get("max_capacity", 0)
        est_value = opp.get("estimated_value", 0) or 0

        if max_capacity <= 0:
            return FactorScore(score=50.0, weight=0.0, reasoning="No capacity data available")

        utilization = current_backlog / max_capacity
        projected = (current_backlog + est_value) / max_capacity

        if projected > 1.0:
            score = _clamp(20 - (projected - 1.0) * 50)
            reasoning = f"Would exceed capacity ({projected:.0%} utilization)"
        elif utilization < 0.5:
            score = 90.0  # Hungry for work
            reasoning = f"Low backlog ({utilization:.0%}), need work"
        elif utilization < 0.8:
            score = 75.0
            reasoning = f"Healthy backlog ({utilization:.0%})"
        else:
            score = _clamp(75 - (utilization - 0.8) * 150)
            reasoning = f"High backlog ({utilization:.0%}), projected {projected:.0%}"

        return FactorScore(score=score, weight=0.0, reasoning=reasoning)

    def _score_geographic_familiarity(self, opp: dict, ctx: dict) -> FactorScore:
        """Score based on distance to office and previous work in area."""
        opp_lat = opp.get("latitude")
        opp_lon = opp.get("longitude")
        office_lat = ctx.get("office_latitude")
        office_lon = ctx.get("office_longitude")

        if not all([opp_lat, opp_lon, office_lat, office_lon]):
            # Fallback: check location string
            location = opp.get("location", "")
            familiar_locations = ctx.get("familiar_locations", [])
            if location and any(loc.lower() in location.lower() for loc in familiar_locations):
                return FactorScore(
                    score=75.0, weight=0.0, reasoning=f"Familiar location: {location}"
                )
            return FactorScore(score=50.0, weight=0.0, reasoning="No geographic data available")

        # `all(...)` above guarantees each coordinate is non-None at this point.
        assert office_lat is not None and office_lon is not None
        assert opp_lat is not None and opp_lon is not None
        distance = _haversine_distance(
            float(office_lat), float(office_lon), float(opp_lat), float(opp_lon)
        )
        # < 50km → 95, 50-150km → 70-95, 150-500km → 40-70, > 500km → < 40
        if distance < 50:
            score = 95.0
        elif distance < 150:
            score = 95 - (distance - 50) * 0.25
        elif distance < 500:
            score = 70 - (distance - 150) * (30 / 350)
        else:
            score = max(10.0, 40 - (distance - 500) * 0.03)

        return FactorScore(
            score=_clamp(score),
            weight=0.0,
            reasoning=f"{distance:.0f} km from office",
        )

    def _score_project_size_fit(self, opp: dict, ctx: dict) -> FactorScore:
        """Score how well project value matches org's sweet spot."""
        est_value = opp.get("estimated_value", 0) or 0
        avg_size = ctx.get("avg_project_size", 0)
        min_size = ctx.get("min_project_size", 0)
        max_size = ctx.get("max_project_size", 0)

        if avg_size <= 0:
            return FactorScore(score=50.0, weight=0.0, reasoning="No project size history")

        if min_size > 0 and max_size > 0:
            if min_size <= est_value <= max_size:
                # Within range — score based on distance from average
                ratio = est_value / avg_size if avg_size > 0 else 1.0
                deviation = abs(ratio - 1.0)
                score = _clamp(90 - deviation * 40)
                reasoning = f"${est_value:,.0f} within range (${min_size:,.0f}-${max_size:,.0f})"
            elif est_value < min_size:
                score = _clamp(40 - (1 - est_value / min_size) * 30)
                reasoning = f"${est_value:,.0f} below typical minimum ${min_size:,.0f}"
            else:
                score = _clamp(40 - (est_value / max_size - 1) * 30)
                reasoning = f"${est_value:,.0f} above typical maximum ${max_size:,.0f}"
        else:
            ratio = est_value / avg_size if avg_size > 0 else 1.0
            deviation = abs(ratio - 1.0)
            score = _clamp(90 - deviation * 50)
            reasoning = f"${est_value:,.0f} vs avg ${avg_size:,.0f} (ratio {ratio:.1f}x)"

        return FactorScore(score=score, weight=0.0, reasoning=reasoning)

    def _score_delivery_method_expertise(self, opp: dict, ctx: dict) -> FactorScore:
        """Score based on org's experience with the delivery method."""
        method = opp.get("delivery_method", "hard_bid")
        bids_by_method = ctx.get("bids_by_method", {})
        ctx.get("bid_count", 0)

        method_data = bids_by_method.get(method, {})
        method_bids = method_data.get("total", 0)
        method_wins = method_data.get("won", 0)

        industry_rate = INDUSTRY_WIN_RATES.get(method, DEFAULT_INDUSTRY_WIN_RATE)
        org_rate = method_wins / method_bids if method_bids > 0 else 0.0

        blended = _blend_factor(org_rate, industry_rate, method_bids, min_bids=3, full_bids=15)
        score = _clamp(20 + (blended / 0.30) * 70)

        return FactorScore(
            score=score,
            weight=0.0,
            reasoning=f"{method}: {method_wins}/{method_bids} wins (blended rate {blended:.0%})",
        )

    def _score_competition_level(self, opp: dict, ctx: dict) -> FactorScore:
        """Score based on known number of competitors."""
        competitors = opp.get("competitors", ctx.get("competitors"))

        if competitors is None:
            return FactorScore(score=50.0, weight=0.0, reasoning="Unknown competition level")

        n = len(competitors) if isinstance(competitors, list) else int(competitors)

        # Fewer competitors → higher score
        if n <= 2:
            score = 90.0
            reasoning = f"Low competition ({n} competitors)"
        elif n <= 5:
            score = 70.0
            reasoning = f"Moderate competition ({n} competitors)"
        elif n <= 8:
            score = 45.0
            reasoning = f"High competition ({n} competitors)"
        else:
            score = max(15.0, 30 - (n - 8) * 3)
            reasoning = f"Very high competition ({n} competitors)"

        return FactorScore(score=score, weight=0.0, reasoning=reasoning)

    def _score_estimating_availability(self, opp: dict, ctx: dict) -> FactorScore:
        """Score based on estimating team capacity."""
        capacity = ctx.get("estimating_capacity")

        if capacity is None:
            return FactorScore(score=50.0, weight=0.0, reasoning="No estimating capacity data")

        # capacity is 0.0-1.0 (available fraction)
        capacity = float(capacity)
        if capacity >= 0.5:
            score = 90.0
            reasoning = f"Good estimating capacity ({capacity:.0%} available)"
        elif capacity >= 0.2:
            score = _clamp(50 + (capacity - 0.2) * (40 / 0.3))
            reasoning = f"Limited estimating capacity ({capacity:.0%} available)"
        else:
            score = _clamp(20 + capacity * 150)
            reasoning = f"Very limited estimating capacity ({capacity:.0%} available)"

        return FactorScore(score=score, weight=0.0, reasoning=reasoning)

    def _score_bonding_impact(self, opp: dict, ctx: dict) -> FactorScore:
        """Score impact on bonding capacity."""
        bonding_capacity = ctx.get("bonding_capacity", 0)
        bonding_used = ctx.get("bonding_used", 0)
        est_value = opp.get("estimated_value", 0) or 0

        if bonding_capacity <= 0:
            return FactorScore(score=50.0, weight=0.0, reasoning="No bonding data available")

        bonding_used / bonding_capacity
        projected_util = (bonding_used + est_value) / bonding_capacity

        if projected_util > 0.95:
            score = 10.0
            reasoning = f"Would exceed bonding capacity ({projected_util:.0%})"
        elif projected_util > 0.8:
            score = _clamp(60 - (projected_util - 0.8) * 300)
            reasoning = f"High bonding utilization ({projected_util:.0%})"
        else:
            score = _clamp(70 + (0.8 - projected_util) * 50)
            reasoning = f"Bonding capacity OK ({projected_util:.0%} projected)"

        return FactorScore(score=score, weight=0.0, reasoning=reasoning)

    def _score_strategic_alignment(self, opp: dict, ctx: dict) -> FactorScore:
        """Score alignment with org's strategic priorities."""
        project_type = opp.get("project_type", "")
        strategic_sectors = ctx.get("strategic_sectors", [])

        if not strategic_sectors:
            return FactorScore(score=50.0, weight=0.0, reasoning="No strategic sectors defined")

        if project_type and project_type.lower() in [s.lower() for s in strategic_sectors]:
            return FactorScore(
                score=90.0,
                weight=0.0,
                reasoning=f"{project_type} is a strategic priority",
            )
        return FactorScore(
            score=40.0,
            weight=0.0,
            reasoning=f"{project_type} is not a strategic priority",
        )

    def _score_risk_profile(self, opp: dict, ctx: dict) -> FactorScore:
        """Score project risk based on available indicators."""
        risk_factors = 0
        risk_reasons = []

        est_value = opp.get("estimated_value", 0) or 0
        max_size = ctx.get("max_project_size", 0)
        if max_size > 0 and est_value > max_size * 1.5:
            risk_factors += 2
            risk_reasons.append("significantly larger than typical")

        method = opp.get("delivery_method", "")
        if method == "hard_bid":
            risk_factors += 1
            risk_reasons.append("hard bid (fixed price risk)")

        due_date = opp.get("bid_due_date")
        if due_date and isinstance(due_date, str):
            from datetime import date as date_type

            try:
                dd = date_type.fromisoformat(due_date)
                days_until = (dd - date_type.today()).days
                if days_until < 14:
                    risk_factors += 1
                    risk_reasons.append(f"tight deadline ({days_until} days)")
            except ValueError:
                pass

        # Score: fewer risks → higher score
        score = _clamp(90 - risk_factors * 20)
        reasoning = "; ".join(risk_reasons) if risk_reasons else "Low risk profile"

        return FactorScore(score=score, weight=0.0, reasoning=reasoning)

    def _score_margin_potential(self, opp: dict, ctx: dict) -> FactorScore:
        """Score expected profitability."""
        method = opp.get("delivery_method", "hard_bid")
        est_value = opp.get("estimated_value", 0) or 0

        # Industry average margins by delivery method
        margins = {
            "hard_bid": 0.03,
            "negotiated": 0.06,
            "design_build": 0.08,
            "cmar": 0.05,
            "ipd": 0.07,
        }
        expected_margin = margins.get(method, 0.04)

        # Larger projects tend to have tighter margins
        if est_value > 50_000_000:
            expected_margin *= 0.8
        elif est_value < 1_000_000:
            expected_margin *= 1.2

        # Map margin to score: 3% → 50, 8% → 85, 1% → 25
        score = _clamp(10 + (expected_margin / 0.10) * 90)

        return FactorScore(
            score=score,
            weight=0.0,
            reasoning=f"Expected margin ~{expected_margin:.1%} ({method})",
        )

    # ------------------------------------------------------------------
    # Composite scoring
    # ------------------------------------------------------------------

    def _compute_composite_score(self, factor_scores: dict[str, FactorScore]) -> int:
        """Weighted average of all factor scores."""
        total_weighted = sum(fs.weighted_score for fs in factor_scores.values())
        total_weight = sum(fs.weight for fs in factor_scores.values())
        if total_weight <= 0:
            return 50
        return round(_clamp(total_weighted / total_weight))

    def _generate_recommendation(self, score: int) -> str:
        """Map composite score to recommendation string."""
        if score >= THRESHOLD_STRONG_PURSUE:
            return "strong_pursue"
        if score >= THRESHOLD_PURSUE:
            return "pursue"
        if score >= THRESHOLD_CONDITIONAL:
            return "conditional"
        return "decline"

    def _calibrate_win_probability(self, score: int, delivery_method: str) -> float:
        """Map composite score to calibrated win probability.

        Score 50 → industry base rate
        Score 100 → base * 3 (capped at 0.95)
        Score 0 → base * 0.2
        """
        base = INDUSTRY_WIN_RATES.get(delivery_method, DEFAULT_INDUSTRY_WIN_RATE)

        if score >= 50:
            # Linear from base (50) to base*3 (100)
            multiplier = 1.0 + (score - 50) / 50 * 2.0
        else:
            # Linear from base*0.2 (0) to base (50)
            multiplier = 0.2 + (score / 50) * 0.8

        probability = base * multiplier
        return round(min(0.95, max(0.01, probability)), 3)


# ---------------------------------------------------------------------------
# IG-03: Cross-project learning integration for bid decisions
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


async def get_historical_bid_context(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_type: str | None = None,
) -> dict:
    """Query cross-project analytics for org-wide performance data.

    Calls ``cross_project_analytics.detect_cost_patterns()`` and
    ``analyze_schedule_accuracy()`` to retrieve historical performance
    metrics that inform bid decisions.

    Args:
        db: AsyncSession database handle.
        org_id: Organization ID for tenant-scoped analytics.
        project_type: Optional project type filter (e.g., "commercial").

    Returns:
        Dict with keys:
        - ``typical_cost_variance_pct``: Average cost variance across projects.
        - ``schedule_accuracy_pct``: Percentage of projects finished on time.
        - ``win_rate_by_type``: Win rate data by project type (if available from
          cost patterns).
        - ``cost_patterns``: Top cost deviation patterns (CSI division level).
        - ``on_time_rate``: Fraction of projects completed on/before schedule.

    The returned dict can be merged into the ``org_context`` parameter of
    ``BidDecisionEngine.score_opportunity()`` to adjust the historical_win_rate
    and risk_profile scoring factors.

    Example integration::

        context = await get_historical_bid_context(db, org_id, "commercial")
        org_context["historical_cost_variance"] = context["typical_cost_variance_pct"]
        org_context["historical_on_time_rate"] = context["on_time_rate"]
        result = engine.score_opportunity(opportunity, org_context)
    """
    from app.services.memory.cross_project_analytics import (
        analyze_schedule_accuracy,
        detect_cost_patterns,
    )

    result: dict = {
        "typical_cost_variance_pct": 0.0,
        "schedule_accuracy_pct": 0.0,
        "win_rate_by_type": {},
        "cost_patterns": [],
        "on_time_rate": 0.0,
    }

    # Fetch cost patterns
    try:
        filters = {}
        if project_type:
            filters["project_type"] = project_type
        cost_patterns = await detect_cost_patterns(db, org_id, filters=filters)
        if cost_patterns:
            # Average variance across top patterns
            avg_variance = sum(p.average_variance_pct for p in cost_patterns) / len(cost_patterns)
            result["typical_cost_variance_pct"] = round(avg_variance, 2)
            result["cost_patterns"] = [
                {
                    "csi_division": p.csi_division,
                    "average_variance_pct": p.average_variance_pct,
                    "project_count": p.project_count,
                    "project_type": p.project_type,
                }
                for p in cost_patterns[:10]
            ]
    except Exception as exc:
        logger.warning("Failed to fetch cost patterns for bid context: %s", exc)

    # Fetch schedule accuracy
    try:
        schedule_report = await analyze_schedule_accuracy(db, org_id)
        result["schedule_accuracy_pct"] = round(
            100.0 - abs(schedule_report.average_duration_variance_pct), 2
        )
        result["on_time_rate"] = schedule_report.on_time_rate

        # Extract win rate by project type from schedule data
        if schedule_report.by_project_type:
            result["win_rate_by_type"] = {
                ptype: {
                    "on_time_rate": data.get("on_time_rate", 0.0),
                    "avg_variance_pct": data.get("average_variance_pct", 0.0),
                    "project_count": data.get("count", 0),
                }
                for ptype, data in schedule_report.by_project_type.items()
            }
    except Exception as exc:
        logger.warning("Failed to fetch schedule accuracy for bid context: %s", exc)

    return result


def enrich_org_context_with_history(
    org_context: dict,
    historical_context: dict,
) -> dict:
    """Merge historical bid context into an org_context dict.

    Adjusts the org_context in-place to reflect historical performance
    data from cross-project analytics, so the BidDecisionEngine factors
    it into scoring.

    Args:
        org_context: The org context dict passed to ``score_opportunity()``.
        historical_context: Output from ``get_historical_bid_context()``.

    Returns:
        The updated org_context dict.
    """
    if historical_context.get("typical_cost_variance_pct"):
        org_context["historical_cost_variance"] = historical_context["typical_cost_variance_pct"]

    if historical_context.get("on_time_rate"):
        org_context["historical_on_time_rate"] = historical_context["on_time_rate"]

    if historical_context.get("schedule_accuracy_pct"):
        org_context["historical_schedule_accuracy"] = historical_context["schedule_accuracy_pct"]

    if historical_context.get("cost_patterns"):
        org_context["historical_cost_patterns"] = historical_context["cost_patterns"]

    return org_context
