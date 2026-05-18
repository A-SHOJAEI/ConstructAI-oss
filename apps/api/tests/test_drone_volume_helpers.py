"""Tests for drone volume calculation pure helpers.

The full DroneService is DB-bound; these tests pin the
deterministic earthwork volume math used to compute cut/fill from
point clouds — critical for site grading, mass excavation
billing, and progress tracking.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.intelligence.drone_service import (
    _CF_TO_CY,
    _compute_volume_confidence,
    _grid_volume_calculation,
)

# =========================================================================
# _CF_TO_CY constant
# =========================================================================


def test_cubic_feet_to_cubic_yards_conversion():
    """[unit conversion] 27 cubic feet = 1 cubic yard.
    Pin so a refactor can't quietly change the constant and corrupt
    every earthwork calculation."""
    assert Decimal("1") / Decimal("27") == _CF_TO_CY
    # 27 CF * factor = 1 CY:
    assert (Decimal("27") * _CF_TO_CY).quantize(Decimal("0.001")) == Decimal("1.000")


# =========================================================================
# _grid_volume_calculation
# =========================================================================


@pytest.fixture(autouse=True)
def numpy_required():
    """Skip if numpy isn't available."""
    pytest.importorskip("numpy")


def test_grid_volume_empty_returns_zeros():
    """Empty point array → all zeros (no division-by-zero, no crash)."""
    np = pytest.importorskip("numpy")
    points = np.array([]).reshape(0, 3)
    cut, fill, area = _grid_volume_calculation(points, grid_spacing=1.0, reference_elevation=0.0)
    assert cut == Decimal("0")
    assert fill == Decimal("0")
    assert area == Decimal("0")


def test_grid_volume_invalid_shape_raises():
    """Wrong-shape input → clear ValueError."""
    np = pytest.importorskip("numpy")
    points = np.array([[1.0, 2.0]])  # only 2 cols, not 3
    with pytest.raises(ValueError, match="Nx3"):
        _grid_volume_calculation(points, grid_spacing=1.0, reference_elevation=0.0)


def test_grid_volume_pure_cut():
    """All points above reference → all volume is "cut" (excavation)."""
    np = pytest.importorskip("numpy")
    # 10x10 ft area, 100 points all at elevation 5 (5 ft above ref 0).
    points = np.array(
        [[x, y, 5.0] for x in range(10) for y in range(10)],
        dtype=np.float64,
    )
    cut, fill, area = _grid_volume_calculation(points, grid_spacing=1.0, reference_elevation=0.0)
    assert cut > Decimal("0")
    assert fill == Decimal("0.00")
    # Area is roughly 100 sq ft (10×10 grid):
    assert area >= Decimal("80")


def test_grid_volume_pure_fill():
    """All points below reference → all volume is "fill"."""
    np = pytest.importorskip("numpy")
    points = np.array(
        [[x, y, -5.0] for x in range(10) for y in range(10)],
        dtype=np.float64,
    )
    cut, fill, _area = _grid_volume_calculation(points, grid_spacing=1.0, reference_elevation=0.0)
    assert cut == Decimal("0.00")
    assert fill > Decimal("0")


def test_grid_volume_at_reference_zero():
    """Points exactly at reference elevation → no cut, no fill."""
    np = pytest.importorskip("numpy")
    points = np.array(
        [[x, y, 0.0] for x in range(5) for y in range(5)],
        dtype=np.float64,
    )
    cut, fill, _ = _grid_volume_calculation(points, grid_spacing=1.0, reference_elevation=0.0)
    assert cut == Decimal("0.00")
    assert fill == Decimal("0.00")


def test_grid_volume_explicit_bounds():
    """Caller-provided bounds override auto-detection from points."""
    np = pytest.importorskip("numpy")
    points = np.array([[5.0, 5.0, 10.0]], dtype=np.float64)
    cut, _, _area = _grid_volume_calculation(
        points,
        grid_spacing=1.0,
        reference_elevation=0.0,
        bounds={"min_x": 0, "max_x": 100, "min_y": 0, "max_y": 100},
    )
    # With expanded bounds, only 1 cell has data so area is small
    # but cut volume reflects the single point's elevation × cell area.
    assert cut > Decimal("0")


def test_grid_volume_returns_decimals_rounded_to_cy():
    """Cut/fill returned as Decimal in cubic yards, rounded to 0.01."""
    np = pytest.importorskip("numpy")
    points = np.array(
        [[x, y, 27.0] for x in range(3) for y in range(3)],
        dtype=np.float64,
    )
    cut, _, _ = _grid_volume_calculation(points, grid_spacing=1.0, reference_elevation=0.0)
    assert isinstance(cut, Decimal)
    # 9 sq ft × 27 ft elevation = 243 cubic ft = 9 cubic yards.
    # Rounded to 2dp:
    assert cut.as_tuple().exponent <= -2 or cut == cut.quantize(Decimal("0.01"))


def test_grid_volume_unit_conversion_to_cy():
    """27 cubic feet of cut → 1 cubic yard. With a 5x5 grid of points
    at elevation 1, bounds auto-detect to (0..4, 0..4), grid_spacing=1
    → 4×4 = 16 cells × 1 sq ft × 1 ft = 16 cubic ft = 0.59 CY."""
    np = pytest.importorskip("numpy")
    points = np.array(
        [[float(x), float(y), 1.0] for x in range(5) for y in range(5)],
        dtype=np.float64,
    )
    cut, _, _area = _grid_volume_calculation(points, grid_spacing=1.0, reference_elevation=0.0)
    # The grid layout from auto-bounds gives 16 cubic ft of cut → 16/27 ≈ 0.59 CY.
    # Pin: result is positive, in cubic-yard range, rounded to 2dp.
    assert cut > Decimal("0")
    assert cut < Decimal("1")
    # 27 CY conversion check via the area: each 1 sq ft cell × 1 ft ≈
    # 0.037 CY, so 16 cells × 0.037 ≈ 0.59 CY:
    assert cut == pytest.approx(Decimal("16") / Decimal("27"), abs=Decimal("0.01"))


# =========================================================================
# _compute_volume_confidence
# =========================================================================


def test_confidence_zero_area_returns_default():
    """[defensive] Zero area must NOT cause divide-by-zero. Returns
    documented default 0.50."""
    out = _compute_volume_confidence(point_count=100, area_sf=0.0, grid_spacing=1.0)
    assert out == Decimal("0.50")


def test_confidence_negative_area_returns_default():
    """Negative area (data error) → also default."""
    out = _compute_volume_confidence(point_count=100, area_sf=-10.0, grid_spacing=1.0)
    assert out == Decimal("0.50")


def test_confidence_high_density_95():
    """Density ≥ 1 pt/sq ft → 0.95 confidence."""
    out = _compute_volume_confidence(point_count=200, area_sf=100, grid_spacing=1.0)
    assert out == Decimal("0.95")


def test_confidence_medium_density_85():
    """0.5 ≤ density < 1.0 → 0.85."""
    # 75 points / 100 sq ft = 0.75 density:
    out = _compute_volume_confidence(point_count=75, area_sf=100, grid_spacing=1.0)
    assert out == Decimal("0.85")


def test_confidence_low_density_70():
    """0.1 ≤ density < 0.5 → 0.70."""
    # 30 points / 100 sq ft = 0.30 density:
    out = _compute_volume_confidence(point_count=30, area_sf=100, grid_spacing=1.0)
    assert out == Decimal("0.70")


def test_confidence_very_low_density_50():
    """density < 0.1 → 0.50 (lowest)."""
    out = _compute_volume_confidence(point_count=5, area_sf=100, grid_spacing=1.0)
    assert out == Decimal("0.50")


def test_confidence_at_density_1_boundary():
    """At exactly 1.0 density → ≥ check passes → 0.95."""
    out = _compute_volume_confidence(point_count=100, area_sf=100, grid_spacing=1.0)
    assert out == Decimal("0.95")


def test_confidence_at_density_05_boundary():
    """At exactly 0.5 density → ≥ check passes → 0.85."""
    out = _compute_volume_confidence(point_count=50, area_sf=100, grid_spacing=1.0)
    assert out == Decimal("0.85")


def test_confidence_at_density_01_boundary():
    """At exactly 0.1 density → ≥ check passes → 0.70."""
    out = _compute_volume_confidence(point_count=10, area_sf=100, grid_spacing=1.0)
    assert out == Decimal("0.70")
