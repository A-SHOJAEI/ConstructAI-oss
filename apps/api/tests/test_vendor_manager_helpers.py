"""Tests for vendor evaluation helpers (services/procurement/vendor_manager).

Pin every normalization function, the recommendation thresholds,
and the risk-flag detection that drives vendor scorecards.
"""

from __future__ import annotations

import pytest

from app.services.procurement.vendor_manager import (
    DEFAULT_CRITERIA_WEIGHTS,
    _get_recommendation,
    _identify_risk_flags,
    _normalize_dart_rate,
    _normalize_experience,
    _normalize_financial,
    _normalize_on_time,
    _normalize_price,
    _normalize_quality,
    _normalize_safety,
    _validate_custom_weights,
)

# =========================================================================
# DEFAULT_CRITERIA_WEIGHTS
# =========================================================================


def test_default_weights_sum_to_one():
    """Pin: default weights must sum to ~1.0 (weighted-average
    correctness depends on it)."""
    assert sum(DEFAULT_CRITERIA_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)


def test_default_weights_canonical_criteria():
    """Pin documented criteria — refactor must not silently drop one."""
    expected = {
        "on_time_delivery",
        "quality",
        "safety",
        "financial",
        "experience",
        "price",
    }
    assert set(DEFAULT_CRITERIA_WEIGHTS.keys()) == expected


# =========================================================================
# _normalize_on_time
# =========================================================================


def test_normalize_on_time_passthrough():
    assert _normalize_on_time(85.0) == 85.0


def test_normalize_on_time_clamped_high():
    """Above 100 clamps to 100."""
    assert _normalize_on_time(120.0) == 100.0


def test_normalize_on_time_clamped_low():
    """Negative clamps to 0."""
    assert _normalize_on_time(-5.0) == 0.0


# =========================================================================
# _normalize_quality
# =========================================================================


def test_normalize_quality_5_star_is_100():
    assert _normalize_quality(5.0) == 100.0


def test_normalize_quality_3_star_is_60():
    assert _normalize_quality(3.0) == 60.0


def test_normalize_quality_zero_is_zero():
    assert _normalize_quality(0.0) == 0.0


def test_normalize_quality_clamped_above_5():
    """6/5 ratings (data error) clamp to 100."""
    assert _normalize_quality(6.0) == 100.0


# =========================================================================
# _normalize_safety (EMR — lower is better)
# =========================================================================


def test_normalize_safety_industry_average_emr_1():
    """EMR 1.0 = industry average → score ~67."""
    assert 60 <= _normalize_safety(1.0) <= 70


def test_normalize_safety_excellent_emr_below_05():
    """EMR ≤ 0.5 → near 100 (excellent safety record)."""
    assert _normalize_safety(0.5) == 100.0


def test_normalize_safety_terrible_emr_above_2():
    """EMR ≥ 2.0 → 0 (worst possible)."""
    assert _normalize_safety(2.0) == 0.0
    assert _normalize_safety(3.0) == 0.0


def test_normalize_safety_zero_or_negative_emr_returns_100():
    """No claims history → assume best score."""
    assert _normalize_safety(0.0) == 100.0
    assert _normalize_safety(-0.5) == 100.0


# =========================================================================
# _normalize_dart_rate (OSHA DART — lower is better)
# =========================================================================


def test_normalize_dart_rate_zero_is_100():
    assert _normalize_dart_rate(0.0) == 100.0


def test_normalize_dart_rate_industry_avg_2_is_50():
    """Industry average DART ~2 → score 50."""
    assert _normalize_dart_rate(2.0) == 50.0


def test_normalize_dart_rate_above_4_is_zero():
    """DART > 4 → 0 (clamped)."""
    assert _normalize_dart_rate(5.0) == 0.0
    assert _normalize_dart_rate(10.0) == 0.0


# =========================================================================
# _normalize_financial
# =========================================================================


def test_normalize_financial_strong():
    assert _normalize_financial("strong") == 90.0


def test_normalize_financial_moderate():
    assert _normalize_financial("moderate") == 60.0


def test_normalize_financial_weak():
    assert _normalize_financial("weak") == 25.0


def test_normalize_financial_unknown_defaults_50():
    """Unknown stability rating → neutral 50."""
    assert _normalize_financial("undisclosed") == 50.0


def test_normalize_financial_case_insensitive():
    assert _normalize_financial("STRONG") == 90.0


# =========================================================================
# _normalize_experience
# =========================================================================


def test_normalize_experience_zero():
    assert _normalize_experience(past_projects=0, references=0) == 0.0


def test_normalize_experience_full_score_at_20_projects_10_refs():
    """20+ projects → 70 score, 10+ refs → 30 score, total 100."""
    assert _normalize_experience(past_projects=20, references=10) == 100.0


def test_normalize_experience_only_projects():
    """20 projects with no refs → 70 score."""
    assert _normalize_experience(past_projects=20, references=0) == 70.0


def test_normalize_experience_only_references():
    """No projects, 10 refs → 30."""
    assert _normalize_experience(past_projects=0, references=10) == 30.0


def test_normalize_experience_capped_at_100():
    """50 projects + 50 refs — must NOT exceed 100."""
    assert _normalize_experience(past_projects=50, references=50) == 100.0


# =========================================================================
# _normalize_price
# =========================================================================


def test_normalize_price_competitive():
    assert _normalize_price(1.0) == 100.0
    assert _normalize_price(0.5) == 50.0
    assert _normalize_price(0.0) == 0.0


def test_normalize_price_clamped():
    assert _normalize_price(1.5) == 100.0
    assert _normalize_price(-0.1) == 0.0


# =========================================================================
# _get_recommendation
# =========================================================================


def test_recommendation_highly_recommended():
    assert _get_recommendation(85.0) == "highly_recommended"
    assert _get_recommendation(95.0) == "highly_recommended"


def test_recommendation_recommended():
    """70-85 → recommended."""
    assert _get_recommendation(70.0) == "recommended"
    assert _get_recommendation(80.0) == "recommended"


def test_recommendation_conditional():
    """50-70 → conditional."""
    assert _get_recommendation(50.0) == "conditional"
    assert _get_recommendation(65.0) == "conditional"


def test_recommendation_not_recommended():
    """< 50 → not_recommended."""
    assert _get_recommendation(0.0) == "not_recommended"
    assert _get_recommendation(45.0) == "not_recommended"


# =========================================================================
# _identify_risk_flags
# =========================================================================


def test_risk_flags_high_dart_rate_flagged():
    flags = _identify_risk_flags({"dart_rate": 3.0})
    assert any("DART rate" in f for f in flags)


def test_risk_flags_high_emr_flagged():
    flags = _identify_risk_flags({"safety_record": 1.5})
    assert any("EMR" in f for f in flags)


def test_risk_flags_dart_takes_precedence_over_emr():
    """When both DART and EMR are present, DART is flagged (more
    specific incident metric)."""
    flags = _identify_risk_flags({"dart_rate": 3.0, "safety_record": 1.5})
    # DART flag present:
    assert any("DART" in f for f in flags)
    # EMR not double-flagged when DART is the primary signal:
    emr_flags = [f for f in flags if "EMR" in f]
    assert len(emr_flags) == 0


def test_risk_flags_low_on_time_flagged():
    flags = _identify_risk_flags({"on_time_delivery_pct": 70.0})
    assert any("on-time" in f.lower() for f in flags)


def test_risk_flags_weak_financial_flagged():
    flags = _identify_risk_flags({"financial_stability": "weak"})
    assert any("financial" in f.lower() for f in flags)


def test_risk_flags_low_quality_flagged():
    flags = _identify_risk_flags({"quality_rating": 2.5})
    assert any("quality" in f.lower() for f in flags)


def test_risk_flags_few_projects_flagged():
    flags = _identify_risk_flags({"past_projects": 1})
    assert any("past projects" in f.lower() for f in flags)


def test_risk_flags_low_bonding_flagged():
    flags = _identify_risk_flags({"bonding_capacity": 500_000})
    assert any("bonding" in f.lower() for f in flags)


def test_risk_flags_clean_vendor_no_flags():
    """Pristine vendor — no risk flags."""
    flags = _identify_risk_flags(
        {
            "dart_rate": 0.5,
            "safety_record": 0.7,
            "on_time_delivery_pct": 95.0,
            "financial_stability": "strong",
            "quality_rating": 4.5,
            "past_projects": 25,
            "bonding_capacity": 5_000_000,
        }
    )
    assert flags == []


# =========================================================================
# _validate_custom_weights
# =========================================================================


def test_validate_custom_weights_sum_one_passes():
    """Valid weights — must not raise."""
    _validate_custom_weights({"a": 0.5, "b": 0.3, "c": 0.2})


def test_validate_custom_weights_sum_within_tolerance():
    """Weights summing to 0.97 (within 0.05 tolerance) → pass."""
    _validate_custom_weights({"a": 0.5, "b": 0.3, "c": 0.17})


def test_validate_custom_weights_outside_tolerance_rejected():
    """Sum 0.5 (way off 1.0) → must raise."""
    with pytest.raises(ValueError, match="weights must sum"):
        _validate_custom_weights({"a": 0.3, "b": 0.2})


def test_validate_custom_weights_over_one_rejected():
    """Sum 1.5 → also rejected."""
    with pytest.raises(ValueError, match="weights must sum"):
        _validate_custom_weights({"a": 0.5, "b": 0.5, "c": 0.5})


def test_validate_custom_weights_empty_rejected():
    """Empty dict sums to 0 → rejected."""
    with pytest.raises(ValueError, match="weights must sum"):
        _validate_custom_weights({})
