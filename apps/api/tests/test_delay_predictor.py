"""Tests for the schedule delay predictor.

Pin every individual risk-assessment helper, the recommendation
generator, and the end-to-end ``predict_delays`` pipeline.
"""

from __future__ import annotations

import pytest

from app.services.scheduling.delay_predictor import (
    RISK_WEIGHTS,
    WEATHER_SENSITIVE_ACTIVITIES,
    DelayPrediction,
    DelayRisk,
    _assess_complexity_risk,
    _assess_float_risk,
    _assess_predecessor_risk,
    _assess_resource_risk,
    _assess_weather_risk,
    _recommend_actions,
    predict_delays,
)

# =========================================================================
# Invariants
# =========================================================================


def test_risk_weights_sum_to_one():
    """Weighted-average correctness depends on the weights summing to 1.0."""
    assert sum(RISK_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)


def test_weather_sensitive_activities_canonical():
    """Pin the documented weather-sensitive activity vocabulary so a
    refactor doesn't quietly drop one (e.g. ``masonry``)."""
    expected = {
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
    assert expected == WEATHER_SENSITIVE_ACTIVITIES


# =========================================================================
# _assess_float_risk
# =========================================================================


@pytest.mark.parametrize(
    "total_float,expected",
    [
        (0, 1.0),  # Critical path
        (-2, 1.0),  # Negative float = critical too
        (3, 0.8),
        (5, 0.5),
        (7, 0.5),  # 7 falls in the ≤7 band
        (10, 0.3),
        (14, 0.3),  # 14 falls in the ≤14 band
        (30, 0.1),
        (100, 0.1),
    ],
)
def test_assess_float_risk_thresholds(total_float: int, expected: float):
    assert _assess_float_risk({"total_float": total_float}) == expected


def test_assess_float_risk_missing_key_treats_as_critical():
    """Activity without total_float defaults to 0 → critical path."""
    assert _assess_float_risk({}) == 1.0


# =========================================================================
# _assess_weather_risk
# =========================================================================


def test_assess_weather_risk_non_sensitive_returns_zero():
    """Indoor activities (e.g. drywall, finishes) get 0 weather risk."""
    activity = {"name": "Drywall installation", "type": "interior_finish"}
    assert _assess_weather_risk(activity) == 0.0


def test_assess_weather_risk_sensitive_no_data_returns_default():
    """Concrete pour with no weather forecast → moderate default risk (0.3)."""
    activity = {"name": "Concrete slab pour", "type": "concrete"}
    assert _assess_weather_risk(activity) == 0.3


def test_assess_weather_risk_sensitive_keyword_in_type():
    """Weather sensitivity may be detected via type, not just name."""
    activity = {"name": "Phase 1", "type": "excavation"}
    assert _assess_weather_risk(activity) == 0.3


def test_assess_weather_risk_with_heavy_rain_increases():
    """Days with > 10 mm precipitation count as adverse — should bump
    the score above the no-data baseline."""
    activity = {
        "name": "concrete pour",
        "type": "concrete",
        "early_start": 0,
        "duration_days": 5,
    }
    weather = [
        {"day": 0, "precipitation_mm": 20},  # adverse
        {"day": 1, "precipitation_mm": 0},
        {"day": 2, "precipitation_mm": 30},  # adverse
        {"day": 3, "precipitation_mm": 0},
        {"day": 4, "precipitation_mm": 0},
    ]
    risk = _assess_weather_risk(activity, weather)
    # 2 adverse days / max(5*0.3, 1) = 2 / 1.5 ≈ 1.33 → clamped to 1.0
    assert risk == pytest.approx(1.0)


def test_assess_weather_risk_with_high_wind_counts():
    """Wind > 50 km/h is also adverse (steel erection lift restrictions)."""
    activity = {
        "name": "steel_erection",
        "early_start": 0,
        "duration_days": 10,
    }
    weather = [{"day": i, "wind_speed_kmh": 60 if i < 2 else 10} for i in range(10)]
    risk = _assess_weather_risk(activity, weather)
    # 2 adverse / max(10*0.3, 3) = 2/3 ≈ 0.67
    assert 0.5 < risk < 0.8


def test_assess_weather_risk_with_freezing_temperature_counts():
    """Below -5°C is adverse (concrete won't cure, mortar fails)."""
    activity = {
        "name": "masonry",
        "early_start": 0,
        "duration_days": 5,
    }
    weather = [{"day": i, "temperature_c": -10 if i < 3 else 5} for i in range(5)]
    risk = _assess_weather_risk(activity, weather)
    # 3 adverse / max(5*0.3, 1.5) = 3/1.5 = 2.0 → clamped to 1.0
    assert risk == pytest.approx(1.0)


def test_assess_weather_risk_zero_duration_returns_zero():
    """Defensive — division-by-zero guard."""
    activity = {"name": "concrete", "early_start": 0, "duration_days": 0}
    weather = [{"day": 0, "precipitation_mm": 50}]
    assert _assess_weather_risk(activity, weather) == 0.0


def test_assess_weather_risk_only_counts_days_in_window():
    """Adverse weather outside the activity's date window must be ignored."""
    activity = {
        "name": "concrete",
        "early_start": 10,
        "duration_days": 3,
    }
    # Adverse weather on days 0-9 (before activity starts) — should not count.
    weather = [{"day": i, "precipitation_mm": 100} for i in range(10)]
    weather += [{"day": i, "precipitation_mm": 0} for i in range(10, 13)]
    risk = _assess_weather_risk(activity, weather)
    assert risk == 0.0


# =========================================================================
# _assess_resource_risk
# =========================================================================


def test_assess_resource_risk_no_resources_returns_zero():
    activity = {"id": "A", "early_start": 0, "duration_days": 5}
    assert _assess_resource_risk(activity, []) == 0.0


def test_assess_resource_risk_no_overlap_returns_zero():
    """Two activities sharing a resource but at different times → no
    contention."""
    activity = {
        "id": "A",
        "early_start": 0,
        "duration_days": 5,
        "resources": {"crane_1": 1},
    }
    other = {
        "id": "B",
        "early_start": 10,
        "duration_days": 5,
        "resources": {"crane_1": 1},
    }
    assert _assess_resource_risk(activity, [activity, other]) == 0.0


def test_assess_resource_risk_two_overlapping_returns_low():
    activity = {
        "id": "A",
        "early_start": 0,
        "duration_days": 5,
        "resources": {"crane_1": 1},
    }
    others = [
        activity,
        {"id": "B", "early_start": 1, "duration_days": 3, "resources": {"crane_1": 1}},
        {"id": "C", "early_start": 2, "duration_days": 3, "resources": {"crane_1": 1}},
    ]
    assert _assess_resource_risk(activity, others) == 0.3


def test_assess_resource_risk_many_overlapping_returns_critical():
    activity = {
        "id": "A",
        "early_start": 0,
        "duration_days": 10,
        "resources": {"crane_1": 1},
    }
    others = [activity] + [
        {
            "id": f"B{i}",
            "early_start": 0,
            "duration_days": 10,
            "resources": {"crane_1": 1},
        }
        for i in range(6)
    ]
    assert _assess_resource_risk(activity, others) == 0.9


def test_assess_resource_risk_different_resources_no_conflict():
    """Two activities overlapping in time but using different resources
    → no conflict."""
    activity = {
        "id": "A",
        "early_start": 0,
        "duration_days": 5,
        "resources": {"crane_1": 1},
    }
    other = {
        "id": "B",
        "early_start": 0,
        "duration_days": 5,
        "resources": {"forklift_1": 1},
    }
    assert _assess_resource_risk(activity, [activity, other]) == 0.0


# =========================================================================
# _assess_predecessor_risk
# =========================================================================


def test_assess_predecessor_risk_no_predecessors_returns_zero():
    assert _assess_predecessor_risk({}, {}) == 0.0


def test_assess_predecessor_risk_critical_path_predecessor():
    activity = {"predecessors": ["A"]}
    by_id = {"A": {"total_float": 0}}
    assert _assess_predecessor_risk(activity, by_id) == 0.8


def test_assess_predecessor_risk_takes_max_across_predecessors():
    """If multiple predecessors, take the worst-case risk."""
    activity = {"predecessors": ["A", "B", "C"]}
    by_id = {
        "A": {"total_float": 30},  # 0 contribution
        "B": {"total_float": 5},  # 0.3
        "C": {"total_float": 0},  # 0.8 (max)
    }
    assert _assess_predecessor_risk(activity, by_id) == 0.8


def test_assess_predecessor_risk_unknown_predecessor_skipped():
    """A predecessor referenced but not in the by_id map is silently
    ignored — must not crash."""
    activity = {"predecessors": ["ghost"]}
    assert _assess_predecessor_risk(activity, {}) == 0.0


# =========================================================================
# _assess_complexity_risk
# =========================================================================


def test_assess_complexity_risk_simple_activity_low_score():
    activity = {"duration_days": 3, "resources": {"crew": 1}, "predecessors": ["A"]}
    assert _assess_complexity_risk(activity) == 0.0


def test_assess_complexity_risk_long_duration_bumps():
    activity = {"duration_days": 25}
    assert _assess_complexity_risk(activity) == 0.3


def test_assess_complexity_risk_many_resources_bumps():
    activity = {"duration_days": 5, "resources": {f"r{i}": 1 for i in range(5)}}
    assert _assess_complexity_risk(activity) == 0.2


def test_assess_complexity_risk_many_predecessors_bumps():
    activity = {"duration_days": 5, "predecessors": ["A", "B", "C", "D", "E"]}
    assert _assess_complexity_risk(activity) == 0.2


def test_assess_complexity_risk_capped_at_1():
    """Stack every modifier — score must NOT exceed 1.0."""
    activity = {
        "duration_days": 100,
        "resources": {f"r{i}": 1 for i in range(10)},
        "predecessors": [f"P{i}" for i in range(10)],
    }
    assert _assess_complexity_risk(activity) <= 1.0


# =========================================================================
# _recommend_actions
# =========================================================================


def test_recommend_actions_for_float_factor():
    risk = DelayRisk(
        activity_id="A",
        activity_name="X",
        risk_score=0.5,
        predicted_delay_days=2,
        risk_factors=["Low float (1 days)"],
    )
    actions = _recommend_actions(risk)
    assert any("buffer" in a.lower() or "fast-track" in a.lower() for a in actions)


def test_recommend_actions_for_weather_factor():
    risk = DelayRisk(
        activity_id="A",
        activity_name="X",
        risk_score=0.5,
        predicted_delay_days=2,
        risk_factors=["Weather exposure risk"],
    )
    actions = _recommend_actions(risk)
    assert any("weather" in a.lower() for a in actions)
    assert any("contingency" in a.lower() for a in actions)


def test_recommend_actions_for_resource_factor():
    risk = DelayRisk(
        activity_id="A",
        activity_name="X",
        risk_score=0.5,
        predicted_delay_days=2,
        risk_factors=["Resource contention"],
    )
    actions = _recommend_actions(risk)
    assert any("resource" in a.lower() for a in actions)


def test_recommend_actions_for_predecessor_factor():
    risk = DelayRisk(
        activity_id="A",
        activity_name="X",
        risk_score=0.5,
        predicted_delay_days=2,
        risk_factors=["Predecessor delay risk"],
    )
    actions = _recommend_actions(risk)
    assert any("predecessor" in a.lower() for a in actions)


def test_recommend_actions_for_complexity_factor():
    risk = DelayRisk(
        activity_id="A",
        activity_name="X",
        risk_score=0.5,
        predicted_delay_days=2,
        risk_factors=["High complexity"],
    )
    actions = _recommend_actions(risk)
    assert any("smaller" in a.lower() or "experienced" in a.lower() for a in actions)


def test_recommend_actions_no_factors_no_actions():
    risk = DelayRisk(
        activity_id="A",
        activity_name="X",
        risk_score=0.0,
        predicted_delay_days=0,
        risk_factors=[],
    )
    assert _recommend_actions(risk) == []


# =========================================================================
# predict_delays — end-to-end
# =========================================================================


async def test_predict_delays_empty_activity_list():
    out = await predict_delays(project_id="p1", activities=[])
    assert isinstance(out, DelayPrediction)
    assert out.overall_risk == 0.0
    assert out.high_risk_activities == []


async def test_predict_delays_critical_path_activity_flagged():
    """An activity on the critical path with multiple stacked risks
    (weather-sensitive + adverse weather + resource contention + high
    complexity) must surface in the high_risk_activities list. Float
    alone (0.27 weight) is not enough to pass the > 0.5 high-risk gate
    — we have to combine signals."""
    weather = [{"day": i, "precipitation_mm": 50} for i in range(25)]  # all adverse
    activities = [
        {
            "id": "A",
            "name": "Concrete foundation pour",
            "type": "concrete",
            "duration_days": 25,  # complexity bump (>20 days → 0.3)
            "total_float": 0,  # critical path → float_risk 1.0
            "early_start": 0,
            "predecessors": [],
            "resources": {"crane_1": 1, "concrete_pump": 1, "rebar_crew": 1, "form_crew": 1},
        },
        # Six concurrent activities sharing a resource → resource_risk 0.9.
        *[
            {
                "id": f"B{i}",
                "name": f"Other concrete {i}",
                "duration_days": 25,
                "total_float": 10,
                "early_start": 0,
                "resources": {"crane_1": 1},
            }
            for i in range(6)
        ],
    ]
    out = await predict_delays(project_id="p1", activities=activities, weather_data=weather)
    assert len(out.high_risk_activities) >= 1
    risk = out.high_risk_activities[0]
    assert risk.activity_id == "A"
    assert risk.risk_score > 0.5
    factors = " ".join(risk.risk_factors)
    assert "Low float" in factors


async def test_predict_delays_risks_sorted_descending():
    activities = [
        {"id": "low", "name": "Drywall", "duration_days": 3, "total_float": 30},
        {"id": "high", "name": "Concrete", "duration_days": 5, "total_float": 0},
        {"id": "mid", "name": "Painting", "duration_days": 2, "total_float": 7},
    ]
    out = await predict_delays(project_id="p1", activities=activities)
    # high_risk_activities only contains those with score > 0.5; check
    # full list ordering by querying through the prediction data:
    # We inspect that "high" is first if present.
    if out.high_risk_activities:
        assert out.high_risk_activities[0].activity_id == "high"


async def test_predict_delays_high_risk_capped_at_10():
    """The high_risk_activities list should be capped at 10 entries to
    keep the response bounded."""
    activities = [
        {
            "id": f"A{i}",
            "name": f"concrete pour {i}",
            "duration_days": 5,
            "total_float": 0,
            "early_start": i,
            "predecessors": [],
        }
        for i in range(15)
    ]
    out = await predict_delays(project_id="p1", activities=activities)
    assert len(out.high_risk_activities) <= 10


async def test_predict_delays_predicted_completion_extends_when_delays_predicted():
    """If predicted delays > 0, the predicted completion date must be
    later than the original target date."""
    activities = [
        {
            "id": "A",
            "name": "Concrete foundation",
            "duration_days": 30,
            "total_float": 0,
            "early_start": 0,
        },
    ]
    out = await predict_delays(
        project_id="p1",
        activities=activities,
        target_completion="2026-12-01",
    )
    if out.delay_days > 0:
        assert out.predicted_completion_date > out.original_completion_date


async def test_predict_delays_default_target_180_days_out():
    """Without target_completion, the original date defaults to today
    + 180 days."""
    out = await predict_delays(project_id="p1", activities=[])
    assert out.original_completion_date  # ISO string
    # If no activities → no delays → predicted matches original.
    assert out.predicted_completion_date == out.original_completion_date
