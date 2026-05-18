"""Tests for the pure ``evaluate_layout`` fitness function.

The DEAP NSGA-II optimizer itself isn't tested (it's stochastic and
needs a full GA run). What matters is that ``evaluate_layout``
correctly scores layouts on the three objectives so the GA's
fitness comparisons are meaningful.

Objectives (all minimized):
  1. travel_distance
  2. safety_risk
  3. crane_inefficiency
"""

from __future__ import annotations

from app.services.logistics.site_layout import evaluate_layout

# =========================================================================
# helpers
# =========================================================================


def _site(width: float = 100, length: float = 100, exclusion_zones=None) -> dict:
    return {
        "width": width,
        "length": length,
        "exclusion_zones": exclusion_zones or [],
    }


def _fac(fac_id: str, fac_type: str, **kwargs) -> dict:
    return {"id": fac_id, "type": fac_type, **kwargs}


# =========================================================================
# Travel distance
# =========================================================================


def test_travel_distance_zero_for_single_facility():
    """No pairs → no travel distance to compute."""
    facilities = [_fac("a", "office", width=10, length=10)]
    travel, _, _ = evaluate_layout(
        positions=[(50.0, 50.0)],
        facilities=facilities,
        site_boundary=_site(),
        constraints={},
    )
    assert travel == 0.0


def test_travel_distance_increases_with_spread():
    """Two facilities far apart → larger travel distance than two
    close together."""
    facilities = [
        _fac("a", "office", width=5, length=5),
        _fac("b", "office", width=5, length=5),
    ]
    close, _, _ = evaluate_layout(
        positions=[(40.0, 50.0), (45.0, 50.0)],
        facilities=facilities,
        site_boundary=_site(),
        constraints={},
    )
    far, _, _ = evaluate_layout(
        positions=[(10.0, 10.0), (90.0, 90.0)],
        facilities=facilities,
        site_boundary=_site(),
        constraints={},
    )
    assert far > close


def test_position_clamped_to_site_boundary():
    """A position outside the site boundary should be clamped — not
    counted at the proposed coordinate. With a 5×5 facility at
    (-100, -100), it ends up at (2.5, 2.5)."""
    facilities = [
        _fac("a", "office", width=5, length=5),
        _fac("b", "office", width=5, length=5),
    ]
    # Put both at extreme negative positions:
    travel, _, _ = evaluate_layout(
        positions=[(-100.0, -100.0), (-100.0, -100.0)],
        facilities=facilities,
        site_boundary=_site(width=100, length=100),
        constraints={},
    )
    # Both should be clamped to ~(2.5, 2.5) → distance ~0
    assert travel < 1.0


# =========================================================================
# Safety risk — minimum distance constraints
# =========================================================================


def test_safety_risk_zero_when_constraints_met():
    facilities = [
        _fac("a", "office", width=5, length=5),
        _fac("b", "fuel_storage", width=5, length=5),
    ]
    # 50m apart with min-distance constraint of 30:
    _, risk, _ = evaluate_layout(
        positions=[(20.0, 50.0), (70.0, 50.0)],
        facilities=facilities,
        site_boundary=_site(),
        constraints={"min_distance_between": {"a-b": 30}},
    )
    # 50m > 30m required → no penalty AND distant from worker (>30m) → 0 risk.
    assert risk == 0.0


def test_safety_risk_penalty_when_min_distance_violated():
    facilities = [
        _fac("a", "office", width=5, length=5),
        _fac("b", "generator", width=5, length=5),
    ]
    # Office and generator are 5m apart with 30m required:
    _, risk, _ = evaluate_layout(
        positions=[(50.0, 50.0), (50.0, 55.0)],
        facilities=facilities,
        site_boundary=_site(),
        constraints={"min_distance_between": {"a-b": 30}},
    )
    # Violation: ~25m short × 10 + hazardous-near-worker penalty (25m short × 5)
    assert risk > 0


def test_safety_risk_exclusion_zone_penalty():
    """A facility placed inside an exclusion zone → +100 penalty per
    facility."""
    facilities = [_fac("a", "office", width=5, length=5)]
    site = _site(
        exclusion_zones=[{"x": 40, "y": 40, "width": 30, "length": 30}],
    )
    _, risk, _ = evaluate_layout(
        positions=[(50.0, 50.0)],  # inside exclusion zone
        facilities=facilities,
        site_boundary=site,
        constraints={},
    )
    assert risk >= 100.0


def test_safety_risk_hazardous_near_worker_penalty():
    """A fuel_storage within 30m of an office should add safety risk."""
    facilities = [
        _fac("office_1", "office", width=5, length=5),
        _fac("fuel_1", "fuel_storage", width=5, length=5),
    ]
    _, risk, _ = evaluate_layout(
        # 10m apart — well within 30m hazard zone
        positions=[(50.0, 50.0), (50.0, 60.0)],
        facilities=facilities,
        site_boundary=_site(),
        constraints={},  # no explicit min_distance set
    )
    assert risk > 0


# =========================================================================
# Crane inefficiency
# =========================================================================


def test_crane_inefficiency_zero_without_crane():
    """No crane → no crane inefficiency to compute."""
    facilities = [_fac("a", "office", width=5, length=5)]
    _, _, crane = evaluate_layout(
        positions=[(50.0, 50.0)],
        facilities=facilities,
        site_boundary=_site(),
        constraints={},
    )
    assert crane == 0.0


def test_crane_inefficiency_penalizes_distant_storage():
    """Material storage 100m from crane (radius 30m) → high inefficiency
    penalty."""
    facilities = [
        # Crane fixed at corner:
        _fac("c1", "crane", fixed=True, x=10, y=10, width=5, length=5),
        # Storage placed far away:
        _fac("s1", "material_storage", width=10, length=10),
    ]
    _, _, crane_far = evaluate_layout(
        positions=[(90.0, 90.0)],
        facilities=facilities,
        site_boundary=_site(),
        constraints={"crane_radius": 30.0},
    )
    _, _, crane_close = evaluate_layout(
        positions=[(20.0, 20.0)],
        facilities=facilities,
        site_boundary=_site(),
        constraints={"crane_radius": 30.0},
    )
    assert crane_far > crane_close


def test_crane_inefficiency_storage_within_radius_low_penalty():
    """Storage within crane radius → small linear penalty proportional
    to distance × 0.1."""
    facilities = [
        _fac("c1", "tower_crane", fixed=True, x=10, y=10, width=5, length=5),
        _fac("s1", "laydown_area", width=10, length=10),
    ]
    _, _, crane = evaluate_layout(
        positions=[(15.0, 15.0)],  # ~7m from crane, well within 50m default
        facilities=facilities,
        site_boundary=_site(),
        constraints={"crane_radius": 50.0},
    )
    # 7m × 0.1 = small but non-zero
    assert 0 < crane < 5.0


def test_evaluate_layout_returns_three_objectives():
    facilities = [
        _fac("a", "office", width=5, length=5),
        _fac("b", "office", width=5, length=5),
    ]
    out = evaluate_layout(
        positions=[(40.0, 50.0), (60.0, 50.0)],
        facilities=facilities,
        site_boundary=_site(),
        constraints={},
    )
    assert len(out) == 3
    travel, risk, crane = out
    # All three should be non-negative (fitness functions are minimized
    # so penalties are positive numbers):
    assert travel >= 0
    assert risk >= 0
    assert crane >= 0


def test_fixed_facility_position_used_not_individual_position():
    """A fixed facility's coordinates come from its dict, not from
    ``positions``. Pin: fixed at (10, 10), additional non-fixed
    facility at index 0 of positions."""
    facilities = [
        _fac("c1", "crane", fixed=True, x=10, y=10, width=5, length=5),
        _fac("s1", "material_storage", width=10, length=10),
    ]
    # Storage at exactly (10, 10) — same position as crane, dist=0.
    _, _, crane = evaluate_layout(
        positions=[(10.0, 10.0)],
        facilities=facilities,
        site_boundary=_site(),
        constraints={"crane_radius": 30.0},
    )
    # Distance 0 → 0 × 0.1 = 0 penalty.
    assert crane == 0.0
