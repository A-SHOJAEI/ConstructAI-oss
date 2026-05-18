"""Vendor management and evaluation for construction procurement."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default criteria weights for vendor scoring
# ---------------------------------------------------------------------------

DEFAULT_CRITERIA_WEIGHTS: dict[str, float] = {
    "on_time_delivery": 0.20,
    "quality": 0.20,
    "safety": 0.20,
    "financial": 0.15,
    "experience": 0.10,
    "price": 0.15,
}

# Financial stability score mapping
_FINANCIAL_SCORES: dict[str, float] = {
    "strong": 90.0,
    "moderate": 60.0,
    "weak": 25.0,
}

# Recommendation thresholds
_RECOMMENDATION_THRESHOLDS: list[tuple[float, str]] = [
    (85.0, "highly_recommended"),
    (70.0, "recommended"),
    (50.0, "conditional"),
    (0.0, "not_recommended"),
]

# Weight sum tolerance for custom weights validation
_WEIGHT_SUM_TOLERANCE = 0.05


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_on_time(pct: float) -> float:
    """Normalize on-time delivery percentage to 0-100 score.

    Direct mapping since it is already a percentage.
    """
    return max(0.0, min(100.0, pct))


def _normalize_quality(rating: float) -> float:
    """Normalize quality rating (1-5 scale) to 0-100 score."""
    return max(0.0, min(100.0, (rating / 5.0) * 100.0))


def _normalize_safety(emr: float) -> float:
    """Normalize Experience Modification Rate (EMR) to 0-100 score.

    EMR of 1.0 is industry average. Lower is better.
    Score: 100 for EMR=0.5, 80 for EMR=0.8, 60 for EMR=1.0, 0 for EMR>=2.0.
    """
    if emr <= 0:
        return 100.0
    if emr >= 2.0:
        return 0.0
    # Linear inverse mapping: score = 100 - (emr - 0.5) * (100 / 1.5)
    score = 100.0 - ((emr - 0.5) / 1.5) * 100.0
    return max(0.0, min(100.0, score))


def _normalize_dart_rate(dart_rate: float) -> float:
    """Normalize OSHA DART rate to 0-100 score.

    DART (Days Away, Restricted, or Transferred) rate normalization.
    Industry average DART is approximately 2.0; excellent is < 1.0.
    Score = max(0, 100 - dart_rate * 25).
    """
    score = 100.0 - dart_rate * 25.0
    return max(0.0, min(100.0, score))


def _normalize_financial(stability: str) -> float:
    """Normalize financial stability string to 0-100 score."""
    return _FINANCIAL_SCORES.get(stability.lower(), 50.0)


def _normalize_experience(past_projects: int, references: int) -> float:
    """Normalize experience from past projects and references to 0-100 score.

    More projects and references indicate more experience.
    """
    project_score = min(past_projects / 20.0, 1.0) * 70.0
    reference_score = min(references / 10.0, 1.0) * 30.0
    return min(100.0, project_score + reference_score)


def _normalize_price(competitiveness: float) -> float:
    """Normalize price competitiveness (0-1) to 0-100 score.

    1.0 = most competitive (lowest price), 0.0 = least competitive.
    """
    return max(0.0, min(100.0, competitiveness * 100.0))


def _get_recommendation(overall_score: float) -> str:
    """Determine recommendation category from overall score."""
    for threshold, label in _RECOMMENDATION_THRESHOLDS:
        if overall_score >= threshold:
            return label
    return "not_recommended"


def _identify_risk_flags(vendor_data: dict) -> list[str]:
    """Identify risk flags based on vendor data thresholds."""
    flags: list[str] = []

    # Check DART rate first, then EMR
    dart_rate = vendor_data.get("dart_rate")
    emr = vendor_data.get("safety_record", 1.0)
    if dart_rate is not None and dart_rate > 2.0:
        flags.append(
            f"Safety concern: DART rate of {dart_rate:.2f} exceeds industry average of 2.0"
        )
    elif emr > 1.0:
        flags.append(f"Safety concern: EMR of {emr:.2f} exceeds industry average of 1.0")

    on_time = vendor_data.get("on_time_delivery_pct", 100.0)
    if on_time < 80.0:
        flags.append(
            f"Delivery risk: On-time delivery rate of {on_time:.1f}% is below 80% threshold"
        )

    financial = vendor_data.get("financial_stability", "moderate").lower()
    if financial == "weak":
        flags.append("Financial risk: Vendor financial stability rated as weak")

    quality = vendor_data.get("quality_rating", 3.0)
    if quality < 3.0:
        flags.append(
            f"Quality concern: Quality rating of {quality:.1f}/5.0 is below acceptable threshold"
        )

    past_projects = vendor_data.get("past_projects", 0)
    if past_projects < 3:
        flags.append(f"Experience concern: Only {past_projects} past projects on record")

    bonding = vendor_data.get("bonding_capacity", 0.0)
    if bonding > 0 and bonding < 1_000_000:
        flags.append(f"Bonding capacity of ${bonding:,.0f} may be insufficient for large projects")

    return flags


def _validate_custom_weights(weights: dict[str, float]) -> None:
    """Validate that custom weights sum to approximately 1.0."""
    weight_sum = sum(weights.values())
    if abs(weight_sum - 1.0) > _WEIGHT_SUM_TOLERANCE:
        raise ValueError(
            f"Custom weights must sum to ~1.0 (within {_WEIGHT_SUM_TOLERANCE} "
            f"tolerance). Got {weight_sum:.4f}."
        )


# ---------------------------------------------------------------------------
# Performance trending
# ---------------------------------------------------------------------------


def compute_trend(vendor_history: list[dict]) -> dict:
    """Compute performance trends from historical scoring records.

    Parameters
    ----------
    vendor_history:
        List of historical scoring records, each with:
        - date: str (ISO date)
        - scores: dict of criterion -> score (0-100)

    Returns
    -------
    dict with trend direction for each criterion:
        criterion -> {
            "trend": "improving" | "stable" | "declining",
            "slope": float,
            "data_points": int,
        }
    """
    if not vendor_history or len(vendor_history) < 2:
        return {}

    # Sort by date
    sorted_history = sorted(vendor_history, key=lambda h: h.get("date", ""))

    # Collect all criteria across all records
    all_criteria: set[str] = set()
    for record in sorted_history:
        scores = record.get("scores", {})
        all_criteria.update(scores.keys())

    trends: dict[str, dict] = {}

    for criterion in all_criteria:
        # Extract time-series for this criterion
        values = []
        for i, record in enumerate(sorted_history):
            score = record.get("scores", {}).get(criterion)
            if score is not None:
                values.append((i, float(score)))

        if len(values) < 2:
            trends[criterion] = {
                "trend": "stable",
                "slope": 0.0,
                "data_points": len(values),
            }
            continue

        # Simple linear regression (least squares)
        n = len(values)
        sum_x = sum(v[0] for v in values)
        sum_y = sum(v[1] for v in values)
        sum_xy = sum(v[0] * v[1] for v in values)
        sum_x2 = sum(v[0] ** 2 for v in values)

        denominator = n * sum_x2 - sum_x**2
        slope = 0.0 if denominator == 0 else (n * sum_xy - sum_x * sum_y) / denominator

        # Classify trend direction
        # Threshold: slope > 1.0 per period = improving, < -1.0 = declining
        if slope > 1.0:
            direction = "improving"
        elif slope < -1.0:
            direction = "declining"
        else:
            direction = "stable"

        trends[criterion] = {
            "trend": direction,
            "slope": round(slope, 4),
            "data_points": n,
        }

    return trends


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def score_vendor(
    vendor_data: dict,
    criteria_weights: dict | None = None,
    custom_weights: dict[str, float] | None = None,
) -> dict:
    """Score a vendor based on multiple criteria.

    Parameters
    ----------
    vendor_data:
        Dict with vendor information including: name, vendor_id,
        past_projects (int), on_time_delivery_pct (float),
        quality_rating (float, 1-5), safety_record (float, EMR),
        dart_rate (float, OSHA DART rate - used instead of EMR if present),
        financial_stability (str: "strong"|"moderate"|"weak"),
        references (int), bonding_capacity (float),
        price_competitiveness (float, 0-1).
    criteria_weights:
        Optional dict overriding default weights. Keys: on_time_delivery,
        quality, safety, financial, experience, price.
    custom_weights:
        Optional dict of custom weights. If provided, validated to sum
        to ~1.0 (within tolerance) and used instead of defaults.
        Takes precedence over criteria_weights if both provided.

    Returns
    -------
    dict with:
        - vendor_id: str
        - overall_score: float (0-100)
        - criteria_scores: dict of criterion -> {score, weighted_score, weight}
        - recommendation: "highly_recommended"|"recommended"|"conditional"|"not_recommended"
        - risk_flags: list[str]
    """
    # Determine weights: custom_weights > criteria_weights > defaults
    if custom_weights is not None:
        _validate_custom_weights(custom_weights)
        weights = custom_weights
    elif criteria_weights is not None:
        weights = criteria_weights
    else:
        weights = DEFAULT_CRITERIA_WEIGHTS.copy()

    # Safety scoring: prefer DART rate over EMR if available
    dart_rate = vendor_data.get("dart_rate")
    if dart_rate is not None:
        safety_score = _normalize_dart_rate(float(dart_rate))
    else:
        safety_score = _normalize_safety(vendor_data.get("safety_record", 1.0))

    # Normalize all criteria to 0-100
    raw_scores: dict[str, float] = {
        "on_time_delivery": _normalize_on_time(vendor_data.get("on_time_delivery_pct", 80.0)),
        "quality": _normalize_quality(vendor_data.get("quality_rating", 3.0)),
        "safety": safety_score,
        "financial": _normalize_financial(vendor_data.get("financial_stability", "moderate")),
        "experience": _normalize_experience(
            vendor_data.get("past_projects", 0),
            vendor_data.get("references", 0),
        ),
        "price": _normalize_price(vendor_data.get("price_competitiveness", 0.5)),
    }

    # Apply weights and compute overall score
    criteria_scores: dict[str, dict] = {}
    overall_score = 0.0

    for criterion, raw_score in raw_scores.items():
        weight = weights.get(criterion, 0.0)
        weighted_score = raw_score * weight
        overall_score += weighted_score
        criteria_scores[criterion] = {
            "score": round(raw_score, 2),
            "weighted_score": round(weighted_score, 2),
            "weight": weight,
        }

    overall_score = round(overall_score, 2)
    recommendation = _get_recommendation(overall_score)
    risk_flags = _identify_risk_flags(vendor_data)

    vendor_id = vendor_data.get("vendor_id", "unknown")
    vendor_name = vendor_data.get("name", "Unknown Vendor")

    logger.info(
        "Vendor scored: %s (%s) -> %.2f (%s), %d risk flags",
        vendor_name,
        vendor_id,
        overall_score,
        recommendation,
        len(risk_flags),
    )

    return {
        "vendor_id": vendor_id,
        "overall_score": overall_score,
        "criteria_scores": criteria_scores,
        "recommendation": recommendation,
        "risk_flags": risk_flags,
    }


async def evaluate_bid(bids: list[dict], evaluation_criteria: dict | None = None) -> dict:
    """Evaluate and rank competitive bids.

    Parameters
    ----------
    bids:
        List of bid dicts, each with: vendor_id, vendor_name, bid_amount,
        schedule_days, qualifications (dict with optional keys: past_projects,
        quality_rating, safety_record, references).
    evaluation_criteria:
        Optional dict overriding default weights. Keys: price (0-1),
        technical (0-1), schedule (0-1). Must sum to 1.0.

    Returns
    -------
    dict with:
        - ranked_bids: list of {rank, vendor_id, vendor_name, total_score,
          price_score, technical_score, schedule_score}
        - recommendation: str
        - analysis: str
    """
    if not bids:
        return {
            "ranked_bids": [],
            "recommendation": "No bids provided for evaluation.",
            "analysis": "No bids to analyze.",
        }

    criteria = evaluation_criteria or {
        "price": 0.40,
        "technical": 0.35,
        "schedule": 0.25,
    }

    price_weight = criteria.get("price", 0.40)
    technical_weight = criteria.get("technical", 0.35)
    schedule_weight = criteria.get("schedule", 0.25)

    # Find min and max for normalization
    amounts = [float(b.get("bid_amount", 0)) for b in bids]
    schedules = [float(b.get("schedule_days", 0)) for b in bids]

    min_amount = min(amounts) if amounts else 0
    max_amount = max(amounts) if amounts else 0
    min_schedule = min(schedules) if schedules else 0
    max_schedule = max(schedules) if schedules else 0

    amount_range = max_amount - min_amount if max_amount != min_amount else 1
    schedule_range = max_schedule - min_schedule if max_schedule != min_schedule else 1

    ranked: list[dict] = []

    for bid in bids:
        vendor_id = bid.get("vendor_id", "unknown")
        vendor_name = bid.get("vendor_name", "Unknown")
        bid_amount = float(bid.get("bid_amount", 0))
        schedule_days = float(bid.get("schedule_days", 0))
        quals = bid.get("qualifications", {})

        # Price score: lower is better (inverse normalization to 0-100)
        if len(bids) > 1:
            price_score = (1.0 - (bid_amount - min_amount) / amount_range) * 100.0
        else:
            price_score = 80.0

        # Technical score: based on qualifications
        qual_components = []
        if "past_projects" in quals:
            qual_components.append(min(quals["past_projects"] / 20.0, 1.0) * 100.0)
        if "quality_rating" in quals:
            qual_components.append((quals["quality_rating"] / 5.0) * 100.0)
        if "safety_record" in quals:
            emr = quals["safety_record"]
            qual_components.append(_normalize_safety(emr))
        if "references" in quals:
            qual_components.append(min(quals["references"] / 10.0, 1.0) * 100.0)

        technical_score = sum(qual_components) / len(qual_components) if qual_components else 60.0

        # Schedule score: lower is better (inverse normalization to 0-100)
        if len(bids) > 1:
            schedule_score = (1.0 - (schedule_days - min_schedule) / schedule_range) * 100.0
        else:
            schedule_score = 80.0

        total_score = (
            price_score * price_weight
            + technical_score * technical_weight
            + schedule_score * schedule_weight
        )

        ranked.append(
            {
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "total_score": round(total_score, 2),
                "price_score": round(price_score, 2),
                "technical_score": round(technical_score, 2),
                "schedule_score": round(schedule_score, 2),
                "bid_amount": bid_amount,
                "schedule_days": schedule_days,
            }
        )

    # Sort by total score descending
    ranked.sort(key=lambda b: b["total_score"], reverse=True)

    # Add rank
    for i, bid_result in enumerate(ranked):
        bid_result["rank"] = i + 1

    # Generate recommendation and analysis
    winner = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None

    recommendation = (
        f"Recommend awarding to {winner['vendor_name']} with a total score of "
        f"{winner['total_score']:.1f}. Bid amount: ${winner['bid_amount']:,.2f}, "
        f"schedule: {winner['schedule_days']:.0f} days."
    )

    spread = winner["total_score"] - runner_up["total_score"] if runner_up else 0
    analysis_parts = [
        f"Evaluated {len(bids)} bids using weighted criteria: "
        f"price ({price_weight:.0%}), technical ({technical_weight:.0%}), "
        f"schedule ({schedule_weight:.0%}).",
    ]

    if runner_up:
        analysis_parts.append(
            f"Score spread between top two bidders: {spread:.1f} points. "
            f"{'Clear winner.' if spread > 10 else 'Close competition - consider negotiation.'}"
        )

    price_range = max_amount - min_amount
    if price_range > 0:
        analysis_parts.append(
            f"Bid price range: ${min_amount:,.2f} to ${max_amount:,.2f} "
            f"(spread of ${price_range:,.2f}, {price_range / min_amount:.1%} variance)."
        )

    analysis = " ".join(analysis_parts)

    logger.info(
        "Bid evaluation complete: %d bids, winner=%s (score=%.2f)",
        len(bids),
        winner["vendor_name"],
        winner["total_score"],
    )

    return {
        "ranked_bids": ranked,
        "recommendation": recommendation,
        "analysis": analysis,
    }


# ---------------------------------------------------------------------------
# OSHA enforcement integration
# ---------------------------------------------------------------------------

_OSHA_PENALTY_THRESHOLD = 100_000.0


async def enrich_vendor_with_osha_history(
    vendor_data: dict,
    db: AsyncSession,
    since_years: int = 3,
) -> dict:
    """Fetch OSHA history and merge into a *copy* of vendor_data.

    Adds keys prefixed with ``osha_`` so downstream scoring can use them
    without mutating the original dict.
    """
    from app.services.safety.osha_lookup import get_contractor_osha_history

    enriched = vendor_data.copy()
    company_name = vendor_data.get("name", "")
    state = vendor_data.get("state")

    if not company_name:
        return enriched

    history = await get_contractor_osha_history(
        db,
        company_name,
        state=state,
        since_years=since_years,
    )

    enriched["osha_matched_name"] = history["matched_name"]
    enriched["osha_match_score"] = history["match_score"]
    enriched["osha_inspection_count"] = history["inspection_count"]
    enriched["osha_violation_count"] = history["violation_count"]
    enriched["osha_willful_count"] = history["willful_count"]
    enriched["osha_repeat_count"] = history["repeat_count"]
    enriched["osha_total_penalty"] = history["total_penalty"]
    enriched["osha_top_cited_standards"] = history.get("top_cited_standards", [])
    enriched["osha_has_recent_willful_repeat"] = history["has_recent_willful_repeat"]

    return enriched


def _osha_risk_flags(vendor_data: dict) -> list[str]:
    """Generate risk flags from OSHA enforcement data on vendor_data."""
    flags: list[str] = []

    if vendor_data.get("osha_has_recent_willful_repeat"):
        willful = vendor_data.get("osha_willful_count", 0)
        repeat = vendor_data.get("osha_repeat_count", 0)
        flags.append(
            f"OSHA concern: {willful} willful and {repeat} repeat violations in recent history"
        )

    penalty = vendor_data.get("osha_total_penalty", 0.0)
    if penalty > _OSHA_PENALTY_THRESHOLD:
        flags.append(
            f"OSHA concern: Total penalties of ${penalty:,.2f} exceed "
            f"${_OSHA_PENALTY_THRESHOLD:,.0f} threshold"
        )

    return flags


async def score_vendor_with_osha(
    vendor_data: dict,
    db: AsyncSession,
    criteria_weights: dict | None = None,
    custom_weights: dict[str, float] | None = None,
    since_years: int = 3,
) -> dict:
    """Score a vendor with OSHA enforcement history folded in.

    Wraps :func:`score_vendor` — enriches vendor_data with OSHA history,
    appends OSHA-specific risk flags, and attaches the raw ``osha_history``
    dict to the result.
    """
    enriched = await enrich_vendor_with_osha_history(
        vendor_data,
        db,
        since_years=since_years,
    )

    result = await score_vendor(
        enriched,
        criteria_weights=criteria_weights,
        custom_weights=custom_weights,
    )

    # Append OSHA flags (don't replace existing ones)
    osha_flags = _osha_risk_flags(enriched)
    result["risk_flags"].extend(osha_flags)

    # Attach OSHA summary
    result["osha_history"] = {
        "matched_name": enriched.get("osha_matched_name"),
        "match_score": enriched.get("osha_match_score", 0.0),
        "inspection_count": enriched.get("osha_inspection_count", 0),
        "violation_count": enriched.get("osha_violation_count", 0),
        "willful_count": enriched.get("osha_willful_count", 0),
        "repeat_count": enriched.get("osha_repeat_count", 0),
        "total_penalty": enriched.get("osha_total_penalty", 0.0),
        "top_cited_standards": enriched.get("osha_top_cited_standards", []),
        "has_recent_willful_repeat": enriched.get("osha_has_recent_willful_repeat", False),
    }

    return result
