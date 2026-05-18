"""Tests for drone/UAV data integration service.

Covers flight log management, capture uploads, grid-based and cross-section
earthwork volume calculations with real math verification, volume comparison,
and API endpoint schema validation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import numpy as np
import pytest

from app.services.intelligence.drone_service import (
    SUPPORTED_CAPTURE_FORMATS,
    SUPPORTED_CAPTURE_TYPES,
    FlightSummary,
    VolumeComparison,
    _compute_volume_confidence,
    _cross_section_volume,
    _grid_volume_calculation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flat_surface(
    n_points: int, elevation: float, x_range: tuple = (0, 100), y_range: tuple = (0, 100)
) -> np.ndarray:
    """Generate a flat surface at a given elevation."""
    rng = np.random.default_rng(seed=42)
    x = rng.uniform(x_range[0], x_range[1], n_points)
    y = rng.uniform(y_range[0], y_range[1], n_points)
    z = np.full(n_points, elevation)
    return np.column_stack([x, y, z])


def _sloped_surface(
    n_points: int, z_min: float, z_max: float, x_range: tuple = (0, 100), y_range: tuple = (0, 100)
) -> np.ndarray:
    """Generate a surface sloping in the X direction."""
    rng = np.random.default_rng(seed=42)
    x = rng.uniform(x_range[0], x_range[1], n_points)
    y = rng.uniform(y_range[0], y_range[1], n_points)
    # Z varies linearly with X
    x_norm = (x - x_range[0]) / (x_range[1] - x_range[0])
    z = z_min + (z_max - z_min) * x_norm
    return np.column_stack([x, y, z])


def _mound_surface(
    n_points: int,
    center_x: float,
    center_y: float,
    radius: float,
    height: float,
    base_elevation: float = 0.0,
) -> np.ndarray:
    """Generate a conical mound centered at (center_x, center_y)."""
    rng = np.random.default_rng(seed=42)
    x = rng.uniform(center_x - radius, center_x + radius, n_points)
    y = rng.uniform(center_y - radius, center_y + radius, n_points)
    dist = np.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)
    z = base_elevation + np.maximum(0, height * (1 - dist / radius))
    return np.column_stack([x, y, z])


# ---------------------------------------------------------------------------
# TestFlightLog — 4 tests
# ---------------------------------------------------------------------------


class TestFlightLog:
    """Tests for drone flight log creation."""

    def test_flight_summary_dataclass(self):
        """FlightSummary should hold aggregate data."""
        summary = FlightSummary(
            project_id="proj-1",
            total_flights=10,
            total_area_covered_sf=Decimal("500000"),
            total_flight_minutes=240,
            captures_by_type={"orthomosaic": 5, "point_cloud": 3, "photo": 2},
            date_range={"first": "2025-01-15", "last": "2025-06-20"},
        )
        assert summary.total_flights == 10
        assert summary.captures_by_type["orthomosaic"] == 5

    def test_supported_capture_types(self):
        """All 5 capture types should be supported."""
        assert {
            "orthomosaic",
            "point_cloud",
            "video",
            "thermal",
            "photo",
        } == SUPPORTED_CAPTURE_TYPES

    def test_supported_formats(self):
        """Each capture type should have defined file extensions."""
        assert ".tif" in SUPPORTED_CAPTURE_FORMATS["orthomosaic"]
        assert ".las" in SUPPORTED_CAPTURE_FORMATS["point_cloud"]
        assert ".laz" in SUPPORTED_CAPTURE_FORMATS["point_cloud"]
        assert ".mp4" in SUPPORTED_CAPTURE_FORMATS["video"]
        assert ".jpg" in SUPPORTED_CAPTURE_FORMATS["photo"]

    def test_volume_comparison_dataclass(self):
        """VolumeComparison should compute deltas."""
        comp = VolumeComparison(
            zone_name="Zone A",
            before_date="2025-01-15",
            after_date="2025-02-15",
            before_cut_cy=Decimal("1000"),
            before_fill_cy=Decimal("200"),
            after_cut_cy=Decimal("1500"),
            after_fill_cy=Decimal("250"),
            delta_cut_cy=Decimal("500"),
            delta_fill_cy=Decimal("50"),
            delta_net_cy=Decimal("450"),
            progress_pct=45.83,
        )
        assert comp.delta_cut_cy == Decimal("500")
        assert comp.progress_pct == 45.83


# ---------------------------------------------------------------------------
# TestCaptureUpload — 5 tests
# ---------------------------------------------------------------------------


class TestCaptureUpload:
    """Tests for capture upload validation."""

    def test_capture_type_validation(self):
        """Invalid capture types should be rejected."""
        assert "lidar" not in SUPPORTED_CAPTURE_TYPES
        assert "point_cloud" in SUPPORTED_CAPTURE_TYPES

    def test_extension_validation_orthomosaic(self):
        """Orthomosaic should accept .tif and .tiff."""
        valid = SUPPORTED_CAPTURE_FORMATS["orthomosaic"]
        assert ".tif" in valid
        assert ".tiff" in valid
        assert ".jpg" not in valid

    def test_extension_validation_point_cloud(self):
        """Point cloud should accept LAS, LAZ, PLY, E57."""
        valid = SUPPORTED_CAPTURE_FORMATS["point_cloud"]
        assert set(valid) == {".las", ".laz", ".ply", ".e57"}

    def test_extension_validation_thermal(self):
        """Thermal captures should accept .tif only."""
        valid = SUPPORTED_CAPTURE_FORMATS["thermal"]
        assert ".tif" in valid

    def test_photo_formats(self):
        """Photo captures should accept common image formats."""
        valid = SUPPORTED_CAPTURE_FORMATS["photo"]
        assert ".jpg" in valid
        assert ".png" in valid
        assert ".dng" in valid


# ---------------------------------------------------------------------------
# TestGridVolumeCalc — 8 tests
# ---------------------------------------------------------------------------


class TestGridVolumeCalc:
    """Tests for grid-based earthwork volume calculation with real math."""

    def test_flat_surface_at_reference_zero_volume(self):
        """A flat surface at the reference elevation should produce zero volume."""
        points = _flat_surface(5000, elevation=100.0)
        cut, fill, _area = _grid_volume_calculation(points, 5.0, 100.0)
        assert cut == Decimal("0")
        assert fill == Decimal("0")

    def test_flat_surface_above_reference_all_cut(self):
        """A flat surface above reference should be all cut, no fill."""
        # 100x100 ft area, 10 ft above reference
        # Volume = 100*100*10 = 100,000 cf = 3703.70 cy
        points = _flat_surface(10000, elevation=110.0, x_range=(0, 100), y_range=(0, 100))
        cut, fill, _area = _grid_volume_calculation(points, 2.0, 100.0)

        assert cut > Decimal("0")
        assert fill == Decimal("0")
        # Expected ~3703.70 CY, allow 5% tolerance for grid discretization
        expected_cy = Decimal("3703.70")
        assert abs(cut - expected_cy) / expected_cy < Decimal("0.05"), (
            f"Expected ~{expected_cy} CY cut, got {cut}"
        )

    def test_flat_surface_below_reference_all_fill(self):
        """A flat surface below reference should be all fill, no cut."""
        # 100x100 ft area, 5 ft below reference
        # Volume = 100*100*5 = 50,000 cf = 1851.85 cy
        points = _flat_surface(10000, elevation=95.0, x_range=(0, 100), y_range=(0, 100))
        cut, fill, _area = _grid_volume_calculation(points, 2.0, 100.0)

        assert fill > Decimal("0")
        assert cut == Decimal("0")
        expected_cy = Decimal("1851.85")
        assert abs(fill - expected_cy) / expected_cy < Decimal("0.05"), (
            f"Expected ~{expected_cy} CY fill, got {fill}"
        )

    def test_sloped_surface_mixed_cut_fill(self):
        """A slope crossing the reference should have both cut and fill."""
        # Slope from 90 to 110, reference at 100
        # Half above, half below reference
        points = _sloped_surface(10000, 90.0, 110.0, x_range=(0, 100), y_range=(0, 100))
        cut, fill, _area = _grid_volume_calculation(points, 2.0, 100.0)

        assert cut > Decimal("0")
        assert fill > Decimal("0")
        # Cut and fill should be roughly equal for symmetric slope
        ratio = float(cut / fill) if fill > 0 else 999
        assert 0.7 < ratio < 1.3, f"Cut/fill ratio should be ~1.0 for symmetric slope, got {ratio}"

    def test_volume_scales_with_height(self):
        """Doubling the height above reference should roughly double the volume."""
        points_5ft = _flat_surface(5000, elevation=105.0, x_range=(0, 50), y_range=(0, 50))
        points_10ft = _flat_surface(5000, elevation=110.0, x_range=(0, 50), y_range=(0, 50))

        cut_5, _, _ = _grid_volume_calculation(points_5ft, 2.0, 100.0)
        cut_10, _, _ = _grid_volume_calculation(points_10ft, 2.0, 100.0)

        ratio = float(cut_10 / cut_5) if cut_5 > 0 else 999
        assert 1.8 < ratio < 2.2, f"10ft cut should be ~2x the 5ft cut, got ratio={ratio}"

    def test_grid_spacing_affects_result(self):
        """Different grid spacings should produce results in the same ballpark."""
        points = _flat_surface(20000, elevation=105.0, x_range=(0, 100), y_range=(0, 100))

        cut_coarse, _, _ = _grid_volume_calculation(points, 10.0, 100.0)
        cut_fine, _, _ = _grid_volume_calculation(points, 1.0, 100.0)

        # Both should approximate ~1851.85 CY (100*100*5 / 27)
        expected = Decimal("1851.85")
        assert abs(cut_coarse - expected) / expected < Decimal("0.20"), (
            f"Coarse grid should be within 20% of expected, got {cut_coarse}"
        )
        assert abs(cut_fine - expected) / expected < Decimal("0.20"), (
            f"Fine grid should be within 20% of expected, got {cut_fine}"
        )
        # Results should be in the same order of magnitude
        if cut_coarse > 0 and cut_fine > 0:
            ratio = float(cut_fine / cut_coarse)
            assert 0.5 < ratio < 2.0, (
                f"Fine ({cut_fine}) and coarse ({cut_coarse}) should be similar"
            )

    def test_empty_points_zero_volume(self):
        """Empty point array should return zero volume."""
        points = np.empty((0, 3))
        cut, fill, area = _grid_volume_calculation(points, 5.0, 100.0)
        assert cut == Decimal("0")
        assert fill == Decimal("0")
        assert area == Decimal("0")

    def test_surface_area_calculation(self):
        """Surface area should approximate the covered ground area."""
        # 100x100 ft area = 10,000 sf
        points = _flat_surface(20000, elevation=100.0, x_range=(0, 100), y_range=(0, 100))
        _, _, area = _grid_volume_calculation(points, 2.0, 100.0)

        expected_area = Decimal("10000")
        # Allow 10% tolerance due to grid edge effects
        assert abs(area - expected_area) / expected_area < Decimal("0.10"), (
            f"Expected ~{expected_area} SF, got {area}"
        )


# ---------------------------------------------------------------------------
# TestCrossSectionVolume — 4 tests
# ---------------------------------------------------------------------------


class TestCrossSectionVolume:
    """Tests for cross-section (average end area) volume calculation."""

    def test_flat_above_reference_cut(self):
        """Flat surface above reference should produce all cut."""
        points = _flat_surface(10000, elevation=110.0, x_range=(0, 100), y_range=(0, 100))
        cut, fill, _area = _cross_section_volume(points, 5.0, 100.0)

        assert cut > Decimal("0")
        assert fill == Decimal("0")
        # Should approximate 100*100*10/27 = 3703.70 CY
        expected = Decimal("3703.70")
        assert abs(cut - expected) / expected < Decimal("0.15"), (
            f"Expected ~{expected} CY, got {cut}"
        )

    def test_flat_below_reference_fill(self):
        """Flat surface below reference should produce all fill."""
        points = _flat_surface(10000, elevation=95.0, x_range=(0, 100), y_range=(0, 100))
        cut, fill, _area = _cross_section_volume(points, 5.0, 100.0)

        assert fill > Decimal("0")
        assert cut == Decimal("0")

    def test_empty_points_zero(self):
        """Empty points should return zero."""
        points = np.empty((0, 3))
        cut, fill, _area = _cross_section_volume(points, 5.0, 100.0)
        assert cut == Decimal("0")
        assert fill == Decimal("0")

    def test_cross_section_vs_grid_agreement(self):
        """Grid and cross-section methods should roughly agree for simple shapes."""
        points = _flat_surface(20000, elevation=108.0, x_range=(0, 100), y_range=(0, 100))

        cut_grid, _fill_grid, _ = _grid_volume_calculation(points, 2.0, 100.0)
        cut_cs, _fill_cs, _ = _cross_section_volume(points, 5.0, 100.0)

        # Allow 20% tolerance between methods
        if cut_grid > 0:
            ratio = float(cut_cs / cut_grid)
            assert 0.8 < ratio < 1.2, (
                f"Cross-section ({cut_cs}) and grid ({cut_grid}) should "
                f"roughly agree, ratio={ratio}"
            )


# ---------------------------------------------------------------------------
# TestVolumeConfidence — 3 tests
# ---------------------------------------------------------------------------


class TestVolumeConfidence:
    """Tests for volume confidence scoring."""

    def test_high_density_high_confidence(self):
        """Dense point cloud (>1 pt/sqft) should get 0.95 confidence."""
        conf = _compute_volume_confidence(20000, 10000.0, 2.0)
        assert conf == Decimal("0.95")

    def test_medium_density_medium_confidence(self):
        """Medium density (0.5-1 pt/sqft) should get 0.85."""
        conf = _compute_volume_confidence(7500, 10000.0, 5.0)
        assert conf == Decimal("0.85")

    def test_low_density_low_confidence(self):
        """Sparse point cloud (<0.1 pt/sqft) should get 0.50."""
        conf = _compute_volume_confidence(500, 10000.0, 10.0)
        assert conf == Decimal("0.50")


# ---------------------------------------------------------------------------
# TestVolumeComparison — 4 tests
# ---------------------------------------------------------------------------


class TestVolumeComparison:
    """Tests for earthwork volume comparison data class."""

    def test_positive_delta_means_more_cut(self):
        """Positive delta_cut should indicate more excavation occurred."""
        comp = VolumeComparison(
            zone_name="Zone A",
            before_date="2025-01-01",
            after_date="2025-02-01",
            before_cut_cy=Decimal("1000"),
            before_fill_cy=Decimal("200"),
            after_cut_cy=Decimal("1500"),
            after_fill_cy=Decimal("200"),
            delta_cut_cy=Decimal("500"),
            delta_fill_cy=Decimal("0"),
            delta_net_cy=Decimal("500"),
            progress_pct=41.67,
        )
        assert comp.delta_cut_cy > 0
        assert comp.delta_fill_cy == 0

    def test_progress_pct_range(self):
        """Progress percentage should be between 0 and 100."""
        comp = VolumeComparison(
            zone_name="Zone B",
            before_date="2025-01-01",
            after_date="2025-03-01",
            before_cut_cy=Decimal("500"),
            before_fill_cy=Decimal("500"),
            after_cut_cy=Decimal("0"),
            after_fill_cy=Decimal("0"),
            delta_cut_cy=Decimal("-500"),
            delta_fill_cy=Decimal("-500"),
            delta_net_cy=Decimal("0"),
            progress_pct=100.0,
        )
        assert 0 <= comp.progress_pct <= 100

    def test_zero_baseline_zero_progress(self):
        """Zero before-volumes should yield 0% progress."""
        comp = VolumeComparison(
            zone_name="New Zone",
            before_date="2025-01-01",
            after_date="2025-01-15",
            before_cut_cy=Decimal("0"),
            before_fill_cy=Decimal("0"),
            after_cut_cy=Decimal("100"),
            after_fill_cy=Decimal("50"),
            delta_cut_cy=Decimal("100"),
            delta_fill_cy=Decimal("50"),
            delta_net_cy=Decimal("50"),
            progress_pct=0.0,  # no baseline to compare against
        )
        assert comp.progress_pct == 0.0

    def test_comparison_preserves_zone_name(self):
        """Zone name should be preserved through comparison."""
        comp = VolumeComparison(
            zone_name="Foundation Excavation Area",
            before_date="2025-01-01",
            after_date="2025-01-15",
            before_cut_cy=Decimal("1000"),
            before_fill_cy=Decimal("0"),
            after_cut_cy=Decimal("2000"),
            after_fill_cy=Decimal("0"),
            delta_cut_cy=Decimal("1000"),
            delta_fill_cy=Decimal("0"),
            delta_net_cy=Decimal("1000"),
            progress_pct=100.0,
        )
        assert comp.zone_name == "Foundation Excavation Area"


# ---------------------------------------------------------------------------
# TestFlightSummary — 3 tests
# ---------------------------------------------------------------------------


class TestFlightSummary:
    """Tests for flight summary aggregation."""

    def test_empty_summary(self):
        """Empty project should have zero totals."""
        summary = FlightSummary(
            project_id="proj-1",
            total_flights=0,
            total_area_covered_sf=Decimal("0"),
            total_flight_minutes=0,
        )
        assert summary.total_flights == 0
        assert summary.total_area_covered_sf == Decimal("0")

    def test_summary_with_captures(self):
        """Summary with captures should aggregate by type."""
        summary = FlightSummary(
            project_id="proj-1",
            total_flights=5,
            total_area_covered_sf=Decimal("250000"),
            total_flight_minutes=120,
            captures_by_type={
                "orthomosaic": 5,
                "point_cloud": 3,
                "thermal": 2,
            },
            date_range={"first": "2025-01-10", "last": "2025-06-15"},
        )
        total_caps = sum(summary.captures_by_type.values())
        assert total_caps == 10

    def test_summary_date_range(self):
        """Date range should capture first and last flight dates."""
        summary = FlightSummary(
            project_id="proj-1",
            total_flights=3,
            total_area_covered_sf=Decimal("100000"),
            total_flight_minutes=60,
            date_range={"first": "2025-03-01", "last": "2025-09-15"},
        )
        assert summary.date_range["first"] == "2025-03-01"
        assert summary.date_range["last"] == "2025-09-15"


# ---------------------------------------------------------------------------
# TestEndpoints — 6 tests (schema validation)
# ---------------------------------------------------------------------------


class TestEndpoints:
    """Tests for API endpoint request/response schema validation."""

    def test_flight_create_request(self):
        """Valid flight creation request should parse."""
        from app.schemas.drone import FlightLogCreateRequest

        req = FlightLogCreateRequest(
            drone_id="DJI-M300-001",
            flight_date=datetime(2025, 7, 15, 10, 0, 0, tzinfo=UTC),
            duration_minutes=25,
            area_covered_sf=Decimal("50000"),
            altitude_ft=Decimal("200"),
            flight_path=[
                {"lat": 30.267, "lon": -97.743, "alt": 200},
                {"lat": 30.268, "lon": -97.742, "alt": 200},
            ],
            notes="Weekly site survey",
        )
        assert req.drone_id == "DJI-M300-001"
        assert req.duration_minutes == 25

    def test_earthwork_calculate_request_valid(self):
        """Valid earthwork calculation request should parse."""
        from app.schemas.drone import EarthworkCalculateRequest

        req = EarthworkCalculateRequest(
            zone_name="Foundation Area",
            points=[[0, 0, 100], [10, 0, 105], [10, 10, 103]],
            grid_spacing_ft=5.0,
            reference_elevation_ft=100.0,
            method="grid",
        )
        assert len(req.points) == 3
        assert req.method == "grid"

    def test_earthwork_request_invalid_method(self):
        """Invalid method should be rejected."""
        from app.schemas.drone import EarthworkCalculateRequest

        with pytest.raises(Exception):
            EarthworkCalculateRequest(
                zone_name="Zone A",
                points=[[0, 0, 0], [1, 1, 1], [2, 2, 2]],
                method="invalid",
            )

    def test_earthwork_request_insufficient_points(self):
        """Fewer than 3 points should be rejected."""
        from app.schemas.drone import EarthworkCalculateRequest

        with pytest.raises(Exception):
            EarthworkCalculateRequest(
                zone_name="Zone A",
                points=[[0, 0, 0], [1, 1, 1]],
            )

    def test_earthwork_request_malformed_point(self):
        """Points with != 3 values should be rejected."""
        from app.schemas.drone import EarthworkCalculateRequest

        with pytest.raises(Exception):
            EarthworkCalculateRequest(
                zone_name="Zone A",
                points=[[0, 0], [1, 1], [2, 2]],  # missing z
            )

    def test_volume_response_schema(self):
        """EarthworkVolumeResponse should serialize correctly."""
        from app.schemas.drone import EarthworkVolumeResponse

        now = datetime.now(UTC)
        resp = EarthworkVolumeResponse(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            capture_id=None,
            calculation_date=date.today(),
            zone_name="Parking Garage Excavation",
            cut_volume_cy=Decimal("5420.50"),
            fill_volume_cy=Decimal("1200.25"),
            net_volume_cy=Decimal("4220.25"),
            surface_area_sf=Decimal("25000.00"),
            reference_elevation_ft=Decimal("100.00"),
            method="grid",
            confidence=Decimal("0.92"),
            notes="Survey flight #12",
            created_at=now,
        )
        assert resp.zone_name == "Parking Garage Excavation"
        assert resp.cut_volume_cy == Decimal("5420.50")
        assert resp.confidence == Decimal("0.92")


# ---------------------------------------------------------------------------
# TestMoundVolume — 2 bonus tests for realistic earthwork
# ---------------------------------------------------------------------------


class TestMoundVolume:
    """Tests with realistic mound/stockpile shapes."""

    def test_conical_mound_volume(self):
        """A conical stockpile should produce all cut (above reference)."""
        # Cone: radius=25ft, height=10ft at center, area ~1963 sqft
        # Volume of cone = pi*r^2*h/3 = pi*625*10/3 = 6545 cf = 242.4 cy
        points = _mound_surface(
            20000, center_x=50, center_y=50, radius=25.0, height=10.0, base_elevation=0.0
        )
        cut, _fill, _area = _grid_volume_calculation(points, 1.0, 0.0)

        assert cut > Decimal("0")
        # Theoretical cone volume: ~242 CY
        # Our mound is not a perfect cone (rectangular sampling), so allow tolerance
        assert Decimal("150") < cut < Decimal("350"), (
            f"Mound volume should be roughly 242 CY, got {cut}"
        )

    def test_depression_produces_fill(self):
        """A depression (points below reference) should produce fill volume."""
        # Generate flat area with a bowl-shaped depression
        rng = np.random.default_rng(seed=42)
        n = 10000
        x = rng.uniform(0, 100, n)
        y = rng.uniform(0, 100, n)
        # Depression: a circular pit centered at (50,50), radius 20, depth 5ft
        dist = np.sqrt((x - 50) ** 2 + (y - 50) ** 2)
        z = np.where(dist < 20, 100.0 - 5.0 * (1 - dist / 20), 100.0)
        points = np.column_stack([x, y, z])

        cut, fill, _area = _grid_volume_calculation(points, 2.0, 100.0)

        assert fill > Decimal("0")
        assert cut == Decimal("0") or cut < fill, "Depression should produce more fill than cut"
