"""Tests for the PredictiveRiskEngine pure scoring methods.

The engine has 5 hazard-category scorers (fall / struck-by /
electrical / excavation / heat) plus the underlying helpers
``_clamp``, ``_score_label``, ``_heat_index``. None of them touch a
DB — pin the scoring math, the OSHA-Focus-Four labels, and the
mitigation suggestions so a refactor can't quietly drop a hazard
factor.
"""

from __future__ import annotations

import pytest

from app.services.safety.predictive_risk import (
    PredictiveRiskEngine,
    _clamp,
    _heat_index,
    _score_label,
)

# =========================================================================
# helpers
# =========================================================================


@pytest.mark.parametrize(
    "score,label",
    [
        (95, "critical"),
        (80, "critical"),
        (75, "high"),
        (60, "high"),
        (50, "elevated"),
        (40, "elevated"),
        (30, "moderate"),
        (20, "moderate"),
        (10, "low"),
        (0, "low"),
    ],
)
def test_score_label_thresholds(score: int, label: str):
    assert _score_label(score) == label


def test_clamp_within_range():
    assert _clamp(42.4) == 42  # rounds
    assert _clamp(42.6) == 43


def test_clamp_below_floor():
    assert _clamp(-50.0) == 0


def test_clamp_above_ceiling():
    assert _clamp(150.0) == 100


def test_clamp_with_custom_bounds():
    assert _clamp(75.0, lo=0, hi=50) == 50


def test_heat_index_below_threshold_returns_temp():
    """NWS formula only applies above 80°F; below that, heat index =
    air temp (no humidity correction)."""
    assert _heat_index(temp_f=75.0, rh=80.0) == 75.0


def test_heat_index_above_threshold_factors_humidity():
    """At 95°F + 50% RH the heat index is well above 95°F."""
    hi = _heat_index(temp_f=95.0, rh=50.0)
    assert hi > 95.0


def test_heat_index_dry_air_lower_than_humid():
    """Same temperature, dry air feels cooler — pin the monotonicity."""
    dry = _heat_index(temp_f=95.0, rh=20.0)
    humid = _heat_index(temp_f=95.0, rh=80.0)
    assert humid > dry


# =========================================================================
# PredictiveRiskEngine._score_*
# =========================================================================


@pytest.fixture
def engine() -> PredictiveRiskEngine:
    return PredictiveRiskEngine()


def _features(**overrides) -> dict:
    """Build a feature dict with neutral defaults."""
    base = {
        "wind_speed": 5.0,
        "precipitation_mm": 0.0,
        "project_age_months": 12.0,
        "temp_max": 70.0,
        "humidity": 50.0,
        "crew_count": 20,
    }
    base.update(overrides)
    return base


def _osha() -> dict:
    """Empty OSHA aggregate — no regional history."""
    return {"category_rates": {}}


# ---- fall risk ----------------------------------------------------------


def test_fall_risk_baseline_low(engine: PredictiveRiskEngine):
    out = engine._score_fall_risk(_features(), _osha())
    assert out.name == "fall_risk"
    # Just baseline (10) → "low"
    assert out.score < 20
    assert out.label == "low"


def test_fall_risk_height_activity_elevates(engine: PredictiveRiskEngine):
    out = engine._score_fall_risk(_features(has_roof=True), _osha())
    assert out.score >= 35  # 10 baseline + 25 for height work
    assert any("height" in f.lower() for f in out.factors)
    assert out.mitigations  # at least one fall-protection mitigation


def test_fall_risk_high_wind_pushes_to_high_or_critical(
    engine: PredictiveRiskEngine,
):
    out = engine._score_fall_risk(
        _features(has_steel=True, wind_speed=40),
        _osha(),
    )
    assert out.score >= 60  # 10 + 25 height + 25 wind
    assert any("wind" in f.lower() for f in out.factors)


def test_fall_risk_first_month_adds_orientation_mitigation(
    engine: PredictiveRiskEngine,
):
    out = engine._score_fall_risk(_features(project_age_months=0.5), _osha())
    assert any("first month" in f.lower() for f in out.factors)
    assert any("orientation" in m.lower() for m in out.mitigations)


def test_fall_risk_regional_osha_history_increases_score(
    engine: PredictiveRiskEngine,
):
    """Region with high historical fall-violation rate elevates the
    baseline regardless of today's activities."""
    osha = {"category_rates": {"fall_risk": 0.6}}
    out = engine._score_fall_risk(_features(), osha)
    assert out.score >= 25  # 10 baseline + 15 for >0.5 rate


def test_fall_risk_score_clamped_at_100(engine: PredictiveRiskEngine):
    """Stack every modifier — score must NOT exceed 100."""
    osha = {"category_rates": {"fall_risk": 0.9}}
    out = engine._score_fall_risk(
        _features(
            has_steel=True,
            has_roof=True,
            has_scaffold=True,
            wind_speed=40,
            precipitation_mm=10,
            project_age_months=0.0,
        ),
        osha,
    )
    assert out.score <= 100


# ---- struck-by risk -----------------------------------------------------


def test_struck_by_risk_returns_named_category(engine: PredictiveRiskEngine):
    out = engine._score_struck_by_risk(_features(), _osha())
    assert out.name == "struck_by_risk"


# ---- electrical risk ----------------------------------------------------


def test_electrical_risk_returns_named_category(engine: PredictiveRiskEngine):
    out = engine._score_electrical_risk(_features(), _osha())
    assert out.name == "electrical_risk"


# ---- excavation risk ----------------------------------------------------


def test_excavation_risk_returns_named_category(engine: PredictiveRiskEngine):
    out = engine._score_excavation_risk(_features(), _osha())
    assert out.name == "excavation_risk"


def test_excavation_with_recent_rain_elevates_score(
    engine: PredictiveRiskEngine,
):
    """Wet soil increases trench-collapse risk — feature flag should
    register in the score factors. Note: the engine matches keyword
    prefixes (``has_excavat`` matches ``excavation`` and ``excavator``)."""
    out = engine._score_excavation_risk(
        _features(has_excavat=True, precipitation_mm=20),  # 5-char prefix
        _osha(),
    )
    # Baseline 5 + 25 (excavation work) + 30 (heavy rain) = 60.
    assert out.score >= 50
    assert any("rainfall" in f.lower() or "rain" in f.lower() for f in out.factors)


# ---- heat risk ----------------------------------------------------------


def test_heat_risk_low_heat_index_returns_low(engine: PredictiveRiskEngine):
    """The engine reads ``heat_index`` directly from features; below
    80°F the score stays at baseline."""
    out = engine._score_heat_risk(_features(heat_index=72), _osha())
    assert out.label == "low"


def test_heat_risk_dangerous_heat_index_elevates(engine: PredictiveRiskEngine):
    """Heat index ≥ 103°F is OSHA "high risk" — score jumps by 35."""
    out = engine._score_heat_risk(
        _features(heat_index=105, has_concrete=True),
        _osha(),
    )
    # Baseline 5 + 35 (dangerous) + 10 (outdoor concrete amplifier) = 50.
    assert out.score >= 35
    assert out.label in ("elevated", "high", "critical")
    assert any("103" in f or "heat" in f.lower() for f in out.factors)


def test_heat_risk_extreme_heat_index_recommends_work_stoppage(
    engine: PredictiveRiskEngine,
):
    """Heat index ≥ 115°F → 50-point bump + stoppage recommendation."""
    out = engine._score_heat_risk(_features(heat_index=120), _osha())
    assert out.score >= 50
    assert any("stoppage" in m.lower() or "halting" in m.lower() for m in out.mitigations)


def test_heat_risk_freezing_temp_adds_cold_stress_factor(
    engine: PredictiveRiskEngine,
):
    """Heat-risk scorer also covers cold-stress (the name is a misnomer)."""
    out = engine._score_heat_risk(_features(heat_index=20, temp_high=10), _osha())
    assert any("freezing" in f.lower() or "cold" in f.lower() for f in out.factors)


# ---- score_label / clamp consistency -----------------------------------


def test_clamp_is_inclusive_of_threshold():
    """Boundary check: exactly 80 → critical."""
    assert _score_label(_clamp(80.0)) == "critical"
    assert _score_label(_clamp(60.0)) == "high"
    assert _score_label(_clamp(40.0)) == "elevated"
    assert _score_label(_clamp(20.0)) == "moderate"
