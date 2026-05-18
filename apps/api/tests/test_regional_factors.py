"""Tests for regional cost factors: lookup, nearest-metro fallback, math, API format.

Covers:
  - RegionalFactor service: exact city/state lookup, zip lookup, state average,
    nearest-metro haversine fallback, national average fallback
  - Cost application: composite factor and per-component factor math
  - Integration with cost_database.py: get_current_cost and enrich_cost_item
    with location parameter
  - Haversine distance calculation
  - Seed data loading and cache management
  - Migration and model validation
  - API endpoint format (regional factor lookup)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.estimating.regional_factors import (
    AppliedRegionalFactor,
    _haversine,
    apply_factor_to_breakdown,
    apply_factor_to_cost,
    clear_cache,
    find_nearest_metro,
    get_regional_factor,
    lookup_by_city_state,
    lookup_by_state,
    lookup_by_zip,
)


@pytest.fixture(autouse=True)
def _clear_regional_cache():
    """Clear regional factor cache before and after each test."""
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# Haversine distance tests
# ---------------------------------------------------------------------------


class TestHaversine:
    """Test haversine distance calculation."""

    def test_same_point(self):
        """Distance from a point to itself should be 0."""
        assert _haversine(40.7128, -74.0060, 40.7128, -74.0060) == 0.0

    def test_nyc_to_la(self):
        """NYC to LA should be roughly 3,940 km."""
        dist = _haversine(40.7128, -74.0060, 34.0522, -118.2437)
        assert 3900 < dist < 4000

    def test_nyc_to_boston(self):
        """NYC to Boston should be roughly 306 km."""
        dist = _haversine(40.7128, -74.0060, 42.3601, -71.0589)
        assert 290 < dist < 320

    def test_symmetry(self):
        """Distance should be the same in both directions."""
        d1 = _haversine(40.7128, -74.0060, 34.0522, -118.2437)
        d2 = _haversine(34.0522, -118.2437, 40.7128, -74.0060)
        assert abs(d1 - d2) < 0.01


# ---------------------------------------------------------------------------
# Seed data loading
# ---------------------------------------------------------------------------


class TestSeedDataLoading:
    """Test loading regional factors from seed JSON."""

    def test_seed_file_exists(self):
        seed_path = (
            Path(__file__).resolve().parents[1] / "data" / "seed" / "regional_factors_v1.json"
        )
        assert seed_path.exists(), f"Seed file not found: {seed_path}"

    def _load_metros(self) -> list[dict]:
        seed_path = (
            Path(__file__).resolve().parents[1] / "data" / "seed" / "regional_factors_v1.json"
        )
        with open(seed_path) as f:
            raw = json.load(f)
        if isinstance(raw, dict) and "metros" in raw:
            return raw["metros"]
        return raw

    def test_seed_data_has_50_metros(self):
        data = self._load_metros()
        assert len(data) == 50

    def test_seed_data_structure(self):
        data = self._load_metros()
        required_keys = {
            "city",
            "state",
            "state_abbr",
            "zip_prefix",
            "latitude",
            "longitude",
            "material_factor",
            "labor_factor",
            "equipment_factor",
            "composite_factor",
        }
        for entry in data:
            assert required_keys.issubset(entry.keys()), f"Missing keys in {entry.get('city')}"

    def test_factors_in_valid_range(self):
        """All factors should be between 0.5 and 2.0."""
        data = self._load_metros()
        for entry in data:
            for factor_key in (
                "material_factor",
                "labor_factor",
                "equipment_factor",
                "composite_factor",
            ):
                val = entry[factor_key]
                assert 0.5 <= val <= 2.0, f"{entry['city']}.{factor_key} = {val} out of range"

    def test_composite_is_reasonable(self):
        """Composite should be between the min and max of the three factors."""
        data = self._load_metros()
        for entry in data:
            factors = [entry["material_factor"], entry["labor_factor"], entry["equipment_factor"]]
            lo = min(factors) - 0.05
            hi = max(factors) + 0.05
            assert lo <= entry["composite_factor"] <= hi, (
                f"{entry['city']} composite {entry['composite_factor']} "
                f"outside range [{lo:.2f}, {hi:.2f}]"
            )


# ---------------------------------------------------------------------------
# Exact lookup tests
# ---------------------------------------------------------------------------


class TestExactLookup:
    """Test exact city/state and zip lookups."""

    def test_lookup_nyc(self):
        factor = lookup_by_city_state("New York", "NY")
        assert factor is not None
        assert factor.city == "New York"
        assert factor.state_abbr == "NY"
        assert factor.material_factor == 1.25
        assert factor.labor_factor == 1.52

    def test_lookup_case_insensitive(self):
        factor = lookup_by_city_state("new york", "ny")
        assert factor is not None
        assert factor.city == "New York"

    def test_lookup_houston(self):
        factor = lookup_by_city_state("Houston", "TX")
        assert factor is not None
        assert factor.material_factor == 0.95
        assert factor.labor_factor == 0.92

    def test_lookup_nonexistent_city(self):
        factor = lookup_by_city_state("Smallville", "KS")
        assert factor is None

    def test_lookup_by_zip_nyc(self):
        factor = lookup_by_zip("10001")
        assert factor is not None
        assert factor.city == "New York"

    def test_lookup_by_zip_3digit(self):
        factor = lookup_by_zip("100")
        assert factor is not None
        assert factor.city == "New York"

    def test_lookup_by_zip_unknown(self):
        factor = lookup_by_zip("00000")
        assert factor is None

    def test_lookup_sf(self):
        factor = lookup_by_city_state("San Francisco", "CA")
        assert factor is not None
        assert factor.labor_factor == 1.55
        assert factor.material_factor == 1.20

    def test_lookup_honolulu(self):
        factor = lookup_by_city_state("Honolulu", "HI")
        assert factor is not None
        assert factor.material_factor == 1.30
        assert factor.equipment_factor == 1.15


# ---------------------------------------------------------------------------
# State lookup tests
# ---------------------------------------------------------------------------


class TestStateLookup:
    """Test state-level lookups."""

    def test_multiple_metros_in_texas(self):
        metros = lookup_by_state("TX")
        assert len(metros) >= 4  # Houston, Dallas, San Antonio, Austin, Fort Worth, El Paso
        cities = {m.city for m in metros}
        assert "Houston" in cities
        assert "Dallas" in cities

    def test_multiple_metros_in_california(self):
        metros = lookup_by_state("CA")
        assert len(metros) >= 4
        cities = {m.city for m in metros}
        assert "San Francisco" in cities
        assert "Los Angeles" in cities

    def test_empty_state(self):
        metros = lookup_by_state("XX")
        assert metros == []


# ---------------------------------------------------------------------------
# Nearest metro fallback tests
# ---------------------------------------------------------------------------


class TestNearestMetroFallback:
    """Test haversine nearest-metro fallback."""

    def test_near_nyc(self):
        """Point in Manhattan should find NYC."""
        factor, dist = find_nearest_metro(40.7580, -73.9855)
        assert factor is not None
        assert factor.city == "New York"
        assert dist < 10  # Less than 10 km

    def test_near_sf(self):
        """Point in downtown SF should find SF."""
        factor, dist = find_nearest_metro(37.7850, -122.4000)
        assert factor is not None
        assert factor.city == "San Francisco"
        assert dist < 5

    def test_rural_location(self):
        """Rural Montana should find the nearest metro (probably Denver or Minneapolis)."""
        factor, dist = find_nearest_metro(46.8797, -110.3626)
        assert factor is not None
        assert dist > 500  # Definitely far from any major metro

    def test_honolulu_nearest(self):
        """Point near Honolulu should find Honolulu."""
        factor, dist = find_nearest_metro(21.30, -157.85)
        assert factor is not None
        assert factor.city == "Honolulu"
        assert dist < 5


# ---------------------------------------------------------------------------
# Unified get_regional_factor tests
# ---------------------------------------------------------------------------


class TestGetRegionalFactor:
    """Test the unified get_regional_factor function with fallback chain."""

    def test_exact_city_state_match(self):
        result = get_regional_factor(city="New York", state="NY")
        assert isinstance(result, AppliedRegionalFactor)
        assert result.metro == "New York"
        assert result.is_fallback is False
        assert result.warning is None
        assert result.distance_km is None

    def test_zip_match(self):
        result = get_regional_factor(zip_code="94102")
        assert result.metro == "San Francisco"
        assert result.is_fallback is False

    def test_state_average_fallback(self):
        """When city not found but state has metros, should average."""
        result = get_regional_factor(city="Bakersfield", state="CA")
        assert "state average" in result.metro.lower() or result.is_fallback
        assert result.composite_factor > 1.0  # CA is high-cost

    def test_latlon_fallback(self):
        result = get_regional_factor(latitude=40.76, longitude=-73.98)
        assert result.metro == "New York"
        assert result.is_fallback is True
        assert result.distance_km is not None
        assert result.distance_km < 10

    def test_latlon_far_fallback_warning(self):
        """Very remote location should produce a warning."""
        result = get_regional_factor(latitude=46.88, longitude=-110.36)
        assert result.is_fallback is True
        assert result.warning is not None
        assert "km away" in result.warning

    def test_no_location_returns_national(self):
        result = get_regional_factor()
        assert result.metro == "National Average"
        assert result.composite_factor == 1.0
        assert result.is_fallback is True
        assert result.warning is not None

    def test_city_state_takes_priority_over_zip(self):
        """When both city/state and zip are provided, city/state wins."""
        result = get_regional_factor(city="New York", state="NY", zip_code="94102")
        assert result.metro == "New York"

    def test_sf_factor_values(self):
        result = get_regional_factor(city="San Francisco", state="CA")
        assert result.material_factor == 1.20
        assert result.labor_factor == 1.55
        assert result.equipment_factor == 1.05


# ---------------------------------------------------------------------------
# Cost application tests
# ---------------------------------------------------------------------------


class TestCostApplication:
    """Test applying regional factors to costs."""

    def test_apply_composite_factor(self):
        rf = AppliedRegionalFactor(
            metro="New York",
            state_abbr="NY",
            material_factor=1.25,
            labor_factor=1.52,
            equipment_factor=1.05,
            composite_factor=1.38,
            distance_km=None,
            is_fallback=False,
            warning=None,
        )
        result = apply_factor_to_cost(100.0, rf)
        assert result["adjusted_cost"] == 138.0
        assert result["regional_factor"] == 1.38
        assert result["metro"] == "New York"
        assert result["is_fallback"] is False

    def test_apply_breakdown_factors(self):
        rf = AppliedRegionalFactor(
            metro="New York",
            state_abbr="NY",
            material_factor=1.25,
            labor_factor=1.50,
            equipment_factor=1.05,
            composite_factor=1.38,
            distance_km=None,
            is_fallback=False,
            warning=None,
        )
        result = apply_factor_to_breakdown(
            material_cost=100.0,
            labor_cost=80.0,
            equipment_cost=20.0,
            factor=rf,
        )
        assert result["material_cost"] == 125.0  # 100 * 1.25
        assert result["labor_cost"] == 120.0  # 80 * 1.50
        assert result["equipment_cost"] == 21.0  # 20 * 1.05
        assert result["total_adjusted_cost"] == 266.0
        assert result["factors_applied"]["material"] == 1.25
        assert result["factors_applied"]["labor"] == 1.50

    def test_national_average_no_change(self):
        rf = AppliedRegionalFactor(
            metro="National Average",
            state_abbr="US",
            material_factor=1.0,
            labor_factor=1.0,
            equipment_factor=1.0,
            composite_factor=1.0,
            distance_km=None,
            is_fallback=True,
            warning="No location",
        )
        result = apply_factor_to_cost(500.0, rf)
        assert result["adjusted_cost"] == 500.0


# ---------------------------------------------------------------------------
# Integration with cost_database.py
# ---------------------------------------------------------------------------


class TestCostDatabaseIntegration:
    """Test that cost_database functions use regional factors correctly."""

    @pytest.mark.asyncio
    async def test_get_current_cost_with_location(self):
        """get_current_cost should apply regional factor when location given."""
        from app.services.estimating.cost_database import get_current_cost

        location = {"city": "New York", "state": "NY"}

        with patch("app.services.estimating.cost_database.fetch_bls_ppi") as mock_ppi:
            mock_ppi.return_value = {"ppi_factor": 1.0}
            result = await get_current_cost(
                "concrete",
                "Ready-mix concrete",
                "CY",
                location=location,
            )

        assert result["adjusted_cost"] > 0
        assert "regional_factor" in result
        assert result["regional_factor"]["metro"] == "New York"
        assert result["regional_factor"]["composite_factor"] == 1.38

        # Without location, should not have regional_factor key
        with patch("app.services.estimating.cost_database.fetch_bls_ppi") as mock_ppi:
            mock_ppi.return_value = {"ppi_factor": 1.0}
            result_no_loc = await get_current_cost(
                "concrete",
                "Ready-mix concrete",
                "CY",
            )

        assert "regional_factor" not in result_no_loc

    @pytest.mark.asyncio
    async def test_get_current_cost_location_adjusts_price(self):
        """NYC location should increase cost vs national."""
        from app.services.estimating.cost_database import get_current_cost

        with patch("app.services.estimating.cost_database.fetch_bls_ppi") as mock_ppi:
            mock_ppi.return_value = {"ppi_factor": 1.0}

            national = await get_current_cost(
                "concrete",
                "Ready-mix concrete",
                "CY",
            )
            nyc = await get_current_cost(
                "concrete",
                "Ready-mix concrete",
                "CY",
                location={"city": "New York", "state": "NY"},
            )

        assert nyc["adjusted_cost"] > national["adjusted_cost"]

    @pytest.mark.asyncio
    async def test_enrich_cost_item_with_location(self):
        """enrich_cost_item should include regional_factor info."""
        from app.services.estimating.cost_database import (
            BLSDataUnavailableError,
            enrich_cost_item,
        )

        with (
            patch("app.services.estimating.cost_database.fetch_bls_ppi") as mock_ppi,
            patch("app.services.estimating.cost_database.fetch_bls_history") as mock_hist,
        ):
            mock_ppi.return_value = {"ppi_factor": 1.0}
            mock_hist.side_effect = BLSDataUnavailableError("no history")

            result = await enrich_cost_item(
                "structural_steel",
                "TON",
                location={"city": "Houston", "state": "TX"},
            )

        assert "regional_factor" in result
        assert result["regional_factor"]["metro"] == "Houston"
        # Houston is below national average
        assert result["regional_factor"]["composite_factor"] < 1.0

    @pytest.mark.asyncio
    async def test_match_costs_with_location(self):
        """match_costs should pass location through to get_current_cost."""
        from app.services.estimating.cost_database import match_costs

        quantities = [
            {"csi_code": "03 30 00", "description": "Concrete", "quantity": 100},
        ]

        with patch("app.services.estimating.cost_database.fetch_bls_ppi") as mock_ppi:
            mock_ppi.return_value = {"ppi_factor": 1.0}

            national = await match_costs(quantities, region="national")
            nyc = await match_costs(
                quantities,
                region="national",
                location={"city": "New York", "state": "NY"},
            )

        assert nyc[0]["total_cost"] > national[0]["total_cost"]
        assert "regional_factor" in nyc[0]


# ---------------------------------------------------------------------------
# Migration and model tests
# ---------------------------------------------------------------------------


class TestMigrationAndModel:
    """Test that the migration and model are properly defined."""

    def test_migration_file_exists(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "013_regional_cost_factors.py"
        )
        assert migration.exists()

    def test_migration_has_correct_revision(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "013_regional_cost_factors.py"
        )
        content = migration.read_text()
        assert 'revision: str = "013"' in content
        assert 'down_revision: str = "012"' in content

    def test_model_registered(self):
        from app.models import RegionalCostFactor

        assert RegionalCostFactor.__tablename__ == "regional_cost_factors"

    def test_model_has_all_columns(self):
        from app.models.regional_cost_factor import RegionalCostFactor

        expected_columns = {
            "id",
            "city",
            "state",
            "state_abbr",
            "zip_prefix",
            "latitude",
            "longitude",
            "csi_division",
            "material_factor",
            "labor_factor",
            "equipment_factor",
            "composite_factor",
            "effective_date",
            "data_source",
            "metadata_",
            "created_at",
            "updated_at",
        }
        # Check mapped columns (column attrs on the class)
        mapper_columns = {c.key for c in RegionalCostFactor.__table__.columns}
        # metadata_ maps to "metadata" column
        mapper_columns.add("metadata_")
        mapper_columns.discard("metadata")
        for col in expected_columns:
            assert col in mapper_columns or col.rstrip("_") in mapper_columns, (
                f"Missing column: {col}"
            )


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchemas:
    """Test Pydantic schemas for regional factors."""

    def test_location_input_minimal(self):
        from app.schemas.estimating import LocationInput

        loc = LocationInput(city="New York", state="NY")
        d = loc.to_dict()
        assert d == {"city": "New York", "state": "NY"}

    def test_location_input_full(self):
        from app.schemas.estimating import LocationInput

        loc = LocationInput(
            city="SF",
            state="CA",
            zip_code="94102",
            latitude=37.78,
            longitude=-122.42,
        )
        d = loc.to_dict()
        assert len(d) == 5

    def test_location_input_empty(self):
        from app.schemas.estimating import LocationInput

        loc = LocationInput()
        d = loc.to_dict()
        assert d == {}

    def test_regional_factor_response(self):
        from app.schemas.estimating import RegionalFactorResponse

        resp = RegionalFactorResponse(
            metro="New York",
            state_abbr="NY",
            material_factor=1.25,
            labor_factor=1.52,
            equipment_factor=1.05,
            composite_factor=1.38,
            is_fallback=False,
        )
        assert resp.metro == "New York"
        assert resp.warning is None


# ---------------------------------------------------------------------------
# Build script tests
# ---------------------------------------------------------------------------


class TestBuildScript:
    """Test the build_regional_factors.py script."""

    def test_compute_composite(self):
        from scripts.build_regional_factors import compute_composite

        # 40% * 1.5 + 45% * 1.2 + 15% * 1.0 = 0.6 + 0.54 + 0.15 = 1.29
        result = compute_composite(mat=1.2, lab=1.5, eq=1.0)
        assert abs(result - 1.29) < 0.001

    def test_build_factors_returns_50(self):
        from scripts.build_regional_factors import build_factors

        factors = build_factors()
        assert len(factors) == 50

    def test_build_factors_structure(self):
        from scripts.build_regional_factors import build_factors

        factors = build_factors()
        for f in factors:
            assert "city" in f
            assert "state_abbr" in f
            assert "composite_factor" in f
            assert 0.5 < f["composite_factor"] < 2.0

    def test_build_factors_with_oews_override(self):
        from scripts.build_regional_factors import build_factors

        # Override TX labor to 1.5 (normally ~0.9)
        factors = build_factors(oews_overrides={"TX": 1.5})
        tx_metros = [f for f in factors if f["state_abbr"] == "TX"]
        for m in tx_metros:
            assert m["labor_factor"] == 1.5


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_zero_cost_application(self):
        rf = AppliedRegionalFactor(
            metro="NYC",
            state_abbr="NY",
            material_factor=1.25,
            labor_factor=1.52,
            equipment_factor=1.05,
            composite_factor=1.38,
            distance_km=None,
            is_fallback=False,
            warning=None,
        )
        result = apply_factor_to_cost(0.0, rf)
        assert result["adjusted_cost"] == 0.0

    def test_breakdown_with_zeros(self):
        rf = AppliedRegionalFactor(
            metro="NYC",
            state_abbr="NY",
            material_factor=1.25,
            labor_factor=1.52,
            equipment_factor=1.05,
            composite_factor=1.38,
            distance_km=None,
            is_fallback=False,
            warning=None,
        )
        result = apply_factor_to_breakdown(0.0, 0.0, 0.0, rf)
        assert result["total_adjusted_cost"] == 0.0

    def test_sf_vs_memphis(self):
        """SF should cost ~1.6x Memphis (2x per user's statement, factors reflect reality)."""
        sf = get_regional_factor(city="San Francisco", state="CA")
        memphis = get_regional_factor(city="Memphis", state="TN")
        ratio = sf.composite_factor / memphis.composite_factor
        # SF/Memphis ratio should be significantly > 1
        assert ratio > 1.3

    def test_honolulu_highest_material(self):
        """Honolulu should have one of the highest material factors."""
        hi = get_regional_factor(city="Honolulu", state="HI")
        assert hi.material_factor >= 1.30

    def test_anchorage_high_costs(self):
        """Anchorage should have high costs across the board."""
        ak = get_regional_factor(city="Anchorage", state="AK")
        assert ak.material_factor >= 1.30
        assert ak.labor_factor >= 1.25
        assert ak.equipment_factor >= 1.10

    def test_backward_compat_region_string(self):
        """Legacy region strings should still work in cost_database."""
        from app.services.estimating.cost_database import _get_regional_adjustment

        factor, info = _get_regional_adjustment("northeast")
        assert factor == 1.15
        assert info is None  # No regional_factor info for legacy strings

    def test_backward_compat_national(self):
        from app.services.estimating.cost_database import _get_regional_adjustment

        factor, info = _get_regional_adjustment("national")
        assert factor == 1.0
        assert info is None
