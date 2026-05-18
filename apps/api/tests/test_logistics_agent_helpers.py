"""Tests for the logistics agent helpers (deterministic, no NSGA-II).

Covers _facility_dimensions and _heuristic_placement — both are
pure functions used as fallbacks when the genetic optimizer is
unavailable. The NSGA-II path needs DEAP and is exercised
elsewhere.
"""

from __future__ import annotations

import math

from app.services.agents.logistics_agent import (
    _facility_dimensions,
    _heuristic_placement,
    build_logistics_agent,
)

# =========================================================================
# _facility_dimensions
# =========================================================================


def test_facility_dimensions_explicit_width_depth():
    """[contract] Explicit width+depth fields take precedence over
    size_sf — the caller's specific dimensions are honored."""
    out = _facility_dimensions({"width": 60, "depth": 16})
    assert out == (60.0, 16.0)


def test_facility_dimensions_size_sf_only_returns_square():
    """[fallback] Only size_sf -> derive square root for both
    dimensions. 400 sf -> 20 x 20."""
    out = _facility_dimensions({"size_sf": 400})
    assert out == (20.0, 20.0)


def test_facility_dimensions_default_size_400():
    """Empty dict -> defaults to 400 sf -> 20 x 20."""
    out = _facility_dimensions({})
    assert out == (20.0, 20.0)


def test_facility_dimensions_size_sf_irrational_root():
    """100 sf -> 10x10. Pin: never silently round to integer."""
    out = _facility_dimensions({"size_sf": 100})
    assert out == (10.0, 10.0)


def test_facility_dimensions_width_only_falls_back_to_size_sf():
    """[edge case] Only width (no depth) -> NOT enough explicit data,
    falls back to size_sf path. Pin: don't fabricate a depth from
    width alone."""
    out = _facility_dimensions({"width": 60, "size_sf": 400})
    # Falls back to size_sf path -> sqrt(400) = 20:
    assert out == (20.0, 20.0)


def test_facility_dimensions_returns_floats():
    """[contract] Always floats (not ints) — downstream NSGA-II
    optimizer expects float coordinates."""
    out = _facility_dimensions({"width": 60, "depth": 16})
    assert isinstance(out[0], float)
    assert isinstance(out[1], float)


def test_facility_dimensions_irrational_size_sf():
    """3000 sf -> sqrt = 54.77... — pin that we don't truncate."""
    out = _facility_dimensions({"size_sf": 3000})
    assert math.isclose(out[0], math.sqrt(3000))
    assert out[0] == out[1]  # Square


# =========================================================================
# _heuristic_placement — sequential offset placement
# =========================================================================


_BUILDING = {"x": 50, "y": 50, "width": 200, "depth": 100}
_SITE = {"width_ft": 500, "depth_ft": 400}


def test_heuristic_placement_empty_returns_empty():
    out = _heuristic_placement([], _SITE, _BUILDING)
    assert out == []


def test_heuristic_placement_tower_crane_centered_on_building():
    """[business invariant] Tower crane is placed at the BUILDING
    CENTER (not the site center) — pin so a refactor doesn't move
    it to a random location, which would invalidate the radius
    coverage calculation."""
    out = _heuristic_placement([{"type": "tower_crane", "radius_ft": 200}], _SITE, _BUILDING)
    assert len(out) == 1
    crane = out[0]
    # Building center: 50 + 200/2 = 150, 50 + 100/2 = 100
    assert crane["x"] == 150
    assert crane["y"] == 100
    assert crane["radius_ft"] == 200


def test_heuristic_placement_tower_crane_default_radius():
    """[default] Missing radius_ft -> 200ft default. Pin so a
    refactor doesn't reduce the default (would invalidate coverage
    estimates)."""
    out = _heuristic_placement([{"type": "tower_crane"}], _SITE, _BUILDING)
    assert out[0]["radius_ft"] == 200


def test_heuristic_placement_tower_crane_coverage_92_5():
    """[contract] Hardcoded 92.5% coverage for tower crane —
    represents the typical effective working radius accounting for
    obstructions. Pin so refactor doesn't silently change to a less
    realistic figure."""
    out = _heuristic_placement([{"type": "tower_crane"}], _SITE, _BUILDING)
    assert out[0]["coverage_pct"] == 92.5


def test_heuristic_placement_office_trailer_dimensions():
    """[contract] Office trailers are 60x16 ft (standard construction
    site trailer). Pin so a refactor doesn't change the assumed size."""
    out = _heuristic_placement([{"type": "office_trailer"}], _SITE, _BUILDING)
    trailer = out[0]
    assert trailer["width"] == 60
    assert trailer["depth"] == 16


def test_heuristic_placement_office_trailer_distance_to_building():
    """[business invariant] Office trailer is placed 90ft from the
    building (safety setback)."""
    out = _heuristic_placement([{"type": "office_trailer"}], _SITE, _BUILDING)
    assert out[0]["distance_to_building_ft"] == 90


def test_heuristic_placement_laydown_east_of_building():
    """[contract] Laydown is placed 20ft east of building footprint
    edge (right side)."""
    out = _heuristic_placement([{"type": "laydown_area", "size_sf": 5000}], _SITE, _BUILDING)
    laydown = out[0]
    # Building x=50, width=200 -> right edge at 250, +20 buffer:
    assert laydown["x"] == 50 + 200 + 20
    assert laydown["y"] == 50
    assert laydown["capacity_sf"] == 5000


def test_heuristic_placement_parking_at_site_bottom():
    """Parking goes at depth_ft - 100 (south end of site)."""
    out = _heuristic_placement([{"type": "parking"}], _SITE, _BUILDING)
    parking = out[0]
    # site depth=400, parking_y = 400 - 100 = 300:
    assert parking["y"] == 300
    assert parking["capacity_vehicles"] == 40
    assert parking["width"] == 200
    assert parking["depth"] == 80


def test_heuristic_placement_unknown_type_falls_through():
    """Unknown facility type -> generic placement at sequential offset."""
    out = _heuristic_placement([{"type": "weird_thing", "size_sf": 999}], _SITE, _BUILDING)
    assert len(out) == 1
    assert out[0]["type"] == "weird_thing"
    assert out[0]["size_sf"] == 999


def test_heuristic_placement_multiple_facilities_sequential_offset():
    """[contract] Multiple facilities -> y_cursor advances per
    placement so they don't overlap. Pin so refactor doesn't cluster
    them at origin."""
    out = _heuristic_placement(
        [
            {"type": "office_trailer", "count": 1},
            {"type": "office_trailer", "count": 2},
        ],
        _SITE,
        _BUILDING,
    )
    # First trailer at y_cursor=10, second offset by 20*1 = 20 -> 30:
    assert out[0]["y"] == 10
    assert out[1]["y"] == 30


def test_heuristic_placement_tower_crane_placed_alongside_others():
    """Tower crane uses building-center coords, doesn't disturb the
    y_cursor for subsequent items."""
    out = _heuristic_placement(
        [
            {"type": "tower_crane"},
            {"type": "office_trailer"},
        ],
        _SITE,
        _BUILDING,
    )
    # Crane at building center, trailer at (10, 10):
    assert out[0]["type"] == "tower_crane"
    assert out[1]["type"] == "office_trailer"
    assert out[1]["x"] == 10
    assert out[1]["y"] == 10


# =========================================================================
# build_logistics_agent — graph topology
# =========================================================================


def test_build_logistics_agent_returns_compiled_graph():
    graph = build_logistics_agent()
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    # 3 documented nodes:
    assert {"optimize_layout", "plan_deliveries", "simulate"} <= nodes


def test_build_logistics_agent_sequential_flow():
    """[contract] optimize_layout -> plan_deliveries -> simulate.
    Order matters: deliveries depend on layout, simulation depends
    on both."""
    graph = build_logistics_agent()
    g = graph.get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    assert ("optimize_layout", "plan_deliveries") in edges
    assert ("plan_deliveries", "simulate") in edges
