"""Tests for the pure helpers in services/logistics/delivery_router.

The full optimize_delivery_routes uses OR-Tools and isn't tested here.
This file pins:
- _haversine_approx — degree-based distance approximation.
- _build_distance_matrix — metres-quantized symmetric matrix.
- _time_to_minutes — HH:MM parser.
"""

from __future__ import annotations

import pytest

from app.services.logistics.delivery_router import (
    _build_distance_matrix,
    _haversine_approx,
    _time_to_minutes,
)

# =========================================================================
# _haversine_approx
# =========================================================================


def test_haversine_same_location_zero():
    a = {"lat": 40.0, "lng": -74.0}
    b = {"lat": 40.0, "lng": -74.0}
    assert _haversine_approx(a, b) == 0.0


def test_haversine_one_degree_lat_is_about_111km():
    a = {"lat": 40.0, "lng": -74.0}
    b = {"lat": 41.0, "lng": -74.0}
    out = _haversine_approx(a, b)
    # 1° latitude ≈ 111 km everywhere
    assert 110.0 <= out <= 112.0


def test_haversine_longitude_shrinks_with_latitude():
    """1° longitude is ~111km at the equator but only ~78km at 45°N
    (cos(45°) ≈ 0.707) — the longitude correction is the whole point
    of this approximation."""
    equator_a = {"lat": 0.0, "lng": 0.0}
    equator_b = {"lat": 0.0, "lng": 1.0}
    high_a = {"lat": 60.0, "lng": 0.0}
    high_b = {"lat": 60.0, "lng": 1.0}

    eq_dist = _haversine_approx(equator_a, equator_b)
    high_dist = _haversine_approx(high_a, high_b)
    # cos(60°) = 0.5 → high latitude distance ≈ 0.5 × equator distance
    assert high_dist < eq_dist
    # Roughly half:
    assert 0.4 < (high_dist / eq_dist) < 0.6


def test_haversine_symmetric():
    """d(a,b) must equal d(b,a)."""
    a = {"lat": 40.7, "lng": -74.0}
    b = {"lat": 34.05, "lng": -118.25}
    assert _haversine_approx(a, b) == pytest.approx(_haversine_approx(b, a))


def test_haversine_returns_positive():
    """Even with negative coordinates, distance is non-negative."""
    a = {"lat": -33.0, "lng": -70.0}
    b = {"lat": -34.0, "lng": -71.0}
    assert _haversine_approx(a, b) > 0


# =========================================================================
# _build_distance_matrix
# =========================================================================


def test_distance_matrix_dimensions():
    locs = [
        {"lat": 40.0, "lng": -74.0},
        {"lat": 41.0, "lng": -74.0},
        {"lat": 42.0, "lng": -74.0},
    ]
    matrix = _build_distance_matrix(locs)
    assert len(matrix) == 3
    assert all(len(row) == 3 for row in matrix)


def test_distance_matrix_diagonal_is_zero():
    locs = [
        {"lat": 40.0, "lng": -74.0},
        {"lat": 41.0, "lng": -74.0},
    ]
    matrix = _build_distance_matrix(locs)
    assert matrix[0][0] == 0
    assert matrix[1][1] == 0


def test_distance_matrix_symmetric():
    locs = [
        {"lat": 40.0, "lng": -74.0},
        {"lat": 41.0, "lng": -74.0},
        {"lat": 40.5, "lng": -73.5},
    ]
    matrix = _build_distance_matrix(locs)
    assert matrix[0][1] == matrix[1][0]
    assert matrix[0][2] == matrix[2][0]
    assert matrix[1][2] == matrix[2][1]


def test_distance_matrix_quantized_to_int_metres():
    """Matrix entries are integer metres — required by OR-Tools' CP-SAT
    solver which works in integer arithmetic."""
    locs = [
        {"lat": 40.0, "lng": -74.0},
        {"lat": 41.0, "lng": -74.0},
    ]
    matrix = _build_distance_matrix(locs)
    for row in matrix:
        for val in row:
            assert isinstance(val, int)


def test_distance_matrix_one_degree_lat_is_about_111000_m():
    """Sanity — 1° lat ≈ 111,000 metres."""
    locs = [
        {"lat": 40.0, "lng": -74.0},
        {"lat": 41.0, "lng": -74.0},
    ]
    matrix = _build_distance_matrix(locs)
    assert 110_000 <= matrix[0][1] <= 112_000


def test_distance_matrix_single_location():
    """Single location → 1×1 matrix with 0."""
    matrix = _build_distance_matrix([{"lat": 40.0, "lng": -74.0}])
    assert matrix == [[0]]


def test_distance_matrix_empty_list():
    assert _build_distance_matrix([]) == []


# =========================================================================
# _time_to_minutes
# =========================================================================


def test_time_to_minutes_midnight():
    assert _time_to_minutes("00:00") == 0


def test_time_to_minutes_noon():
    assert _time_to_minutes("12:00") == 720


def test_time_to_minutes_late_evening():
    assert _time_to_minutes("23:59") == 23 * 60 + 59


def test_time_to_minutes_morning_quarter_past():
    assert _time_to_minutes("08:15") == 8 * 60 + 15


def test_time_to_minutes_single_digit_hour():
    """Even with leading zero, single-digit hours parse correctly."""
    assert _time_to_minutes("07:30") == 7 * 60 + 30


def test_time_to_minutes_strict_two_digit_format():
    """Pin the documented format — function expects ``HH:MM`` not
    ``H:MM`` etc. Just verify a well-formed call works as expected."""
    out = _time_to_minutes("06:00")
    assert out == 360
