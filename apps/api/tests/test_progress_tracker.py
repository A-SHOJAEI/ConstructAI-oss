"""Tests for progress_tracker pure functions.

The DB-backed entry points (analyze_progress_photo, compare_against_schedule,
auto_update_schedule_progress, etc.) need a real session. The two heavy
pure functions — ``map_detections_to_activities`` and
``estimate_percent_complete`` — contain the actual progress-inference
logic and are testable without any DB.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.vision.progress_tracker import (
    DETECTION_TO_CSI_MAP,
    ActivityMatch,
    estimate_percent_complete,
    map_detections_to_activities,
)

# =========================================================================
# map_detections_to_activities
# =========================================================================


def _det(class_name: str, confidence: float = 0.9) -> dict:
    return {"class_name": class_name, "confidence": confidence}


def _activity(name: str, **extra) -> dict:
    a = {"id": f"act-{name}", "name": name, "activity_code": "", "wbs_code": ""}
    a.update(extra)
    return a


def test_map_returns_empty_when_no_detections_match_csi():
    detections = [_det("backhoe")]  # not in DETECTION_TO_CSI_MAP
    activities = [_activity("Excavation")]
    assert map_detections_to_activities(detections, activities) == []


def test_map_returns_empty_when_no_activity_keywords_match():
    detections = [_det("excavator")]
    activities = [_activity("Drywall installation")]
    assert map_detections_to_activities(detections, activities) == []


def test_map_skips_worker_detections():
    """Person/hard_hat/vest are headcount only — they shouldn't trigger
    activity matches."""
    detections = [_det("person", 0.99), _det("hard_hat", 0.99)]
    activities = [_activity("Excavation")]
    assert map_detections_to_activities(detections, activities) == []


def test_map_excavator_detection_matches_excavation_activity():
    detections = [_det("excavator", 0.85)]
    activities = [_activity("Site Excavation - North")]
    matches = map_detections_to_activities(detections, activities)
    assert len(matches) == 1
    m = matches[0]
    assert m.detection_class == "excavator"
    assert m.csi_division == "31 00 00"
    assert m.match_score > 0.0
    assert m.detection_confidence == 0.85


def test_map_concrete_mixer_matches_concrete_pour():
    detections = [_det("concrete_mixer", 0.9)]
    activities = [_activity("Foundation pour")]
    matches = map_detections_to_activities(detections, activities)
    assert len(matches) == 1
    assert matches[0].csi_division == "03 00 00"


def test_map_crane_matches_each_activity_once_via_first_satisfying_csi():
    """Crane is registered for both structural steel (05 00 00) and
    concrete (03 00 00). The dedup is per ``(activity_id, class_name)``
    so a crane detection produces one match per activity, attributed to
    whichever CSI division satisfies the activity first — not one
    per CSI."""
    detections = [_det("crane", 0.9)]
    activities = [
        _activity("Structural steel erection"),  # matches via 05 00 00
        _activity("Foundation footing pour"),  # matches via 03 00 00
    ]
    matches = map_detections_to_activities(detections, activities)
    by_act = {m.activity_id: m.csi_division for m in matches}
    assert by_act["act-Structural steel erection"] == "05 00 00"
    assert by_act["act-Foundation footing pour"] == "03 00 00"


def test_map_dedups_same_activity_class_pair():
    """If two crane detections fire on the same activity, only one
    match should be recorded — otherwise a single physical crane
    seen twice doubles the score."""
    detections = [_det("crane", 0.8), _det("crane", 0.95)]
    activities = [_activity("Steel beam erection")]
    matches = map_detections_to_activities(detections, activities)
    assert len(matches) == 1


def test_map_sorts_by_score_descending():
    """Higher-quality matches must appear first — UI shows the top
    matches as the "primary" activity."""
    detections = [_det("excavator", 0.99)]
    activities = [
        _activity("Storm sewer trench"),  # generic excavation match
        _activity("Excavation grade backfill"),  # multiple keyword hits
    ]
    matches = map_detections_to_activities(detections, activities)
    # The activity with more keyword matches must be ranked first.
    scores = [m.match_score for m in matches]
    assert scores == sorted(scores, reverse=True)


def test_map_uses_activity_code_and_wbs_for_keyword_matching():
    """A laconic activity name like ``A1`` shouldn't lose matches if
    the wbs_code/activity_code carries the keyword."""
    detections = [_det("excavator", 0.9)]
    activities = [
        _activity("A1", activity_code="EXCAVATE-001", wbs_code=""),
    ]
    matches = map_detections_to_activities(detections, activities)
    assert len(matches) == 1


def test_detection_to_csi_map_static_check():
    """Sanity check on the lookup table — protects against accidental
    deletion of equipment categories on a refactor."""
    for required in ("excavator", "crane", "concrete_mixer", "scaffolding"):
        assert required in DETECTION_TO_CSI_MAP


# =========================================================================
# estimate_percent_complete
# =========================================================================


def _match(activity_id: str, klass: str = "excavator", score: float = 0.5) -> ActivityMatch:
    return ActivityMatch(
        activity_id=activity_id,
        activity_name=activity_id,
        detection_class=klass,
        csi_division="31 00 00",
        match_score=score,
        detection_confidence=score,
    )


def test_estimate_returns_empty_when_no_matches():
    assert estimate_percent_complete([]) == {}


def test_estimate_uses_minimum_5_percent_floor():
    """The minimum estimate is 5% — even a low-confidence match means
    *something* happened, and 0% would be misleading."""
    m = _match("a1", score=0.0)
    out = estimate_percent_complete([m])
    assert out["a1"] >= Decimal("5")


def test_estimate_caps_at_85_percent_without_winding_down():
    """Without a winding-down signal, photo evidence alone caps at 85%
    — final 15% is human-confirmed."""
    matches = [_match("a1", score=1.0)] * 5
    history = [{"activities_progress": {"a1": 90}, "equipment_counts": {}, "worker_counts": {}}]
    out = estimate_percent_complete(matches, history)
    assert out["a1"] <= Decimal("85")


def test_estimate_never_regresses_below_historical():
    """Historical pct is a floor — if yesterday was 40%, today's
    estimate can only go up."""
    m = _match("a1", score=0.4)
    history = [
        {
            "activities_progress": {"a1": 40},
            "equipment_counts": {},
            "worker_counts": {},
        }
    ]
    out = estimate_percent_complete([m], history)
    assert out["a1"] >= Decimal("40")


def test_estimate_multi_class_boost():
    """Multiple distinct equipment types on the same activity raises
    the estimate — concrete_mixer + crane on a foundation pour beats
    just the mixer alone."""
    one = estimate_percent_complete([_match("a1", "excavator", 0.5)])
    two = estimate_percent_complete(
        [_match("a1", "excavator", 0.5), _match("a1", "concrete_mixer", 0.5)]
    )
    assert two["a1"] > one["a1"]


def test_estimate_winding_down_allows_up_to_95():
    """SV-16: when both equipment and worker counts drop vs. the
    previous snapshot AND prior pct >= 70%, the activity is winding
    down — allow up to 95%."""
    matches = [_match("a1", "excavator", 1.0)]
    history = [
        {
            "activities_progress": {"a1": 90},  # already 90%
            "equipment_counts": {"a1": 5},  # was 5
            "worker_counts": {"a1": 10},  # was 10
        }
    ]
    # current: 1 equipment match, 0 worker matches → both decreasing
    out = estimate_percent_complete(matches, history)
    # Historical floor is 90%; standard cap of 85% would have clamped DOWN,
    # but winding-down allows up to 95%.
    assert out["a1"] >= Decimal("90")
    assert out["a1"] <= Decimal("95")


def test_estimate_temporal_correlation_boost():
    """SV-17: if the same equipment was seen in the last 2+ snapshots,
    the estimate gets a 10% multiplicative boost."""
    matches = [_match("a1", "excavator", 0.5)]
    no_history = estimate_percent_complete(matches)
    with_streak = estimate_percent_complete(
        matches,
        [
            {
                "activities_progress": {"a1": 0},
                "equipment_counts": {"a1": 1},
                "worker_counts": {},
                "equipment_types": {"a1": ["excavator"]},
            },
            {
                "activities_progress": {"a1": 0},
                "equipment_counts": {"a1": 1},
                "worker_counts": {},
                "equipment_types": {"a1": ["excavator"]},
            },
        ],
    )
    assert with_streak["a1"] > no_history["a1"]


def test_estimate_quantizes_to_two_decimal_places():
    """Returned values must round to 2 decimals — UI displays
    pct.toFixed(2) and a non-canonical Decimal would print
    differently across rows."""
    m = _match("a1", score=0.333333)
    out = estimate_percent_complete([m])
    # The Decimal must have at most 2 fractional digits:
    s = str(out["a1"])
    if "." in s:
        assert len(s.split(".")[1]) <= 2
