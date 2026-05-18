"""Comprehensive tests for the expanded cost database and BLS integration.

Verifies:
    - All BLS series are fetchable (live API tests)
    - Cost item enrichment returns trend, uncertainty, and adjusted costs
    - No mock/synthetic fallback paths remain
    - Backward compatibility with existing 56+ cost items
    - BLS v2 batch API works correctly
    - Material-specific uncertainty ranges are used
    - Monte Carlo uses material-specific ranges
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

os.environ.setdefault("TESTING", "true")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bls_api_key():
    """Return the BLS_API_KEY from environment, skip if not set."""
    key = os.environ.get("BLS_API_KEY")
    if not key:
        pytest.skip("BLS_API_KEY not set; skipping live API tests")
    return key


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear module-level caches before each test."""
    from app.services.estimating.cost_database import (
        _ppi_cache,
        _ppi_history_cache,
    )

    _ppi_cache.clear()
    _ppi_history_cache.clear()
    yield
    _ppi_cache.clear()
    _ppi_history_cache.clear()


# ---------------------------------------------------------------------------
# 1. BLS Series Catalog
# ---------------------------------------------------------------------------


class TestBLSSeriesCatalog:
    """Tests for the BLS series catalog structure."""

    def test_catalog_has_at_least_20_series(self):
        from app.services.estimating.cost_database import BLS_SERIES_MAP

        assert len(BLS_SERIES_MAP) >= 20, f"Expected at least 20 series, got {len(BLS_SERIES_MAP)}"

    def test_all_series_have_required_fields(self):
        from app.services.estimating.cost_database import BLS_SERIES_MAP

        for sid, meta in BLS_SERIES_MAP.items():
            assert "description" in meta, f"{sid} missing description"
            assert "csi_division" in meta, f"{sid} missing csi_division"
            assert "category" in meta, f"{sid} missing category"
            assert "series_type" in meta, f"{sid} missing series_type"
            assert meta["series_type"] in (
                "ppi",
                "wage",
                "employment",
            ), f"{sid} has invalid series_type: {meta['series_type']}"

    def test_has_wage_series(self):
        from app.services.estimating.cost_database import BLS_SERIES_MAP

        wage_series = [s for s, m in BLS_SERIES_MAP.items() if m["series_type"] == "wage"]
        assert len(wage_series) >= 6, f"Expected 6+ wage series, got {len(wage_series)}"

    def test_has_employment_series(self):
        from app.services.estimating.cost_database import BLS_SERIES_MAP

        emp_series = [s for s, m in BLS_SERIES_MAP.items() if m["series_type"] == "employment"]
        assert len(emp_series) >= 2, f"Expected 2+ employment series, got {len(emp_series)}"

    def test_has_expanded_ppi_series(self):
        from app.services.estimating.cost_database import BLS_SERIES_MAP

        ppi_series = [s for s, m in BLS_SERIES_MAP.items() if m["series_type"] == "ppi"]
        assert len(ppi_series) >= 14, f"Expected 14+ PPI series, got {len(ppi_series)}"

    def test_legacy_series_map_preserved(self):
        """The legacy _BLS_SERIES_MAP must still exist for backward compat."""
        from app.services.estimating.cost_database import _BLS_SERIES_MAP

        assert "concrete" in _BLS_SERIES_MAP
        assert "structural_steel" in _BLS_SERIES_MAP
        assert "lumber" in _BLS_SERIES_MAP
        assert "copper" in _BLS_SERIES_MAP
        assert "asphalt" in _BLS_SERIES_MAP
        assert "default" in _BLS_SERIES_MAP

    def test_all_categories_unique(self):
        from app.services.estimating.cost_database import BLS_SERIES_MAP

        categories = [m["category"] for m in BLS_SERIES_MAP.values()]
        assert len(categories) == len(set(categories)), "Duplicate categories"


# ---------------------------------------------------------------------------
# 2. Live BLS API tests
# ---------------------------------------------------------------------------


class TestBLSAPIConnection:
    @pytest.mark.asyncio
    async def test_fetch_bls_ppi_works(self, bls_api_key):
        from app.services.estimating.cost_database import fetch_bls_ppi

        result = await fetch_bls_ppi("WPUIP2300001")
        assert "ppi_factor" in result
        assert result["ppi_factor"] > 0
        assert "latest_value" in result

    @pytest.mark.asyncio
    async def test_fetch_bls_history_works(self, bls_api_key):
        from app.services.estimating.cost_database import fetch_bls_history

        data = await fetch_bls_history("WPUIP2300001", years=3)
        assert len(data) >= 12, f"Expected 12+ observations, got {len(data)}"
        assert "date" in data[0]
        assert "value" in data[0]
        # Should be sorted chronologically
        dates = [d["date"] for d in data]
        assert dates == sorted(dates)

    @pytest.mark.asyncio
    async def test_batch_fetch_works(self, bls_api_key):
        from app.services.estimating.cost_database import _fetch_bls_batch

        result = await _fetch_bls_batch(
            ["WPUIP2300001", "WPU101", "WPU081"],
            start_year=2023,
        )
        assert len(result) >= 1, "Batch should return at least one series"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "series_id",
        [
            "WPUIP2300001",  # Concrete PPI
            "WPU101",  # Iron and steel
            "WPU081",  # Lumber
            "WPU102502",  # Copper
            "WPU058",  # Asphalt
            "WPU0553",  # Asphalt roofing
            "WPU0913",  # Paint
            "CEU2023610008",  # Heavy/civil wages
            "CEU2023812008",  # Electrical wages
            "CES2000000001",  # Total construction employment
        ],
    )
    async def test_key_series_return_data(self, bls_api_key, series_id):
        from app.services.estimating.cost_database import fetch_bls_ppi

        result = await fetch_bls_ppi(series_id)
        assert result["latest_value"] > 0, f"Series {series_id} returned zero latest_value"


# ---------------------------------------------------------------------------
# 3. No mock/synthetic fallback
# ---------------------------------------------------------------------------


class TestNoMockFallback:
    def test_no_cached_ppi_defaults(self):
        """_CACHED_PPI_DEFAULTS should no longer exist."""
        import app.services.estimating.cost_database as cdb

        assert not hasattr(cdb, "_CACHED_PPI_DEFAULTS"), "_CACHED_PPI_DEFAULTS should be removed"

    @pytest.mark.asyncio
    async def test_fetch_bls_ppi_raises_when_no_cache(self):
        """Without API and without cache, should raise."""
        from app.services.estimating.cost_database import (
            BLSDataUnavailableError,
            fetch_bls_ppi,
        )

        with patch("httpx.AsyncClient.post", side_effect=Exception("network")):
            with pytest.raises(BLSDataUnavailableError):
                await fetch_bls_ppi("NONEXISTENT_SERIES_XYZ")

    @pytest.mark.asyncio
    async def test_fetch_bls_history_raises_when_no_cache(self):
        from app.services.estimating.cost_database import (
            BLSDataUnavailableError,
            fetch_bls_history,
        )

        with patch("httpx.AsyncClient.post", side_effect=Exception("network")):
            with pytest.raises(BLSDataUnavailableError):
                await fetch_bls_history("NONEXISTENT_SERIES_XYZ")


# ---------------------------------------------------------------------------
# 4. Backward compatibility — Reference costs
# ---------------------------------------------------------------------------


class TestReferenceCosts:
    def test_at_least_60_cost_items(self):
        from app.services.estimating.cost_database import REFERENCE_COSTS

        assert len(REFERENCE_COSTS) >= 60, f"Expected 60+ cost items, got {len(REFERENCE_COSTS)}"

    def test_original_items_preserved(self):
        """All 56 original cost items must still exist."""
        from app.services.estimating.cost_database import REFERENCE_COSTS

        original_items = [
            ("demolition", "SF"),
            ("environmental_remediation", "SF"),
            ("concrete", "CY"),
            ("concrete_foundation", "CY"),
            ("concrete_slab", "CY"),
            ("precast", "SF"),
            ("rebar", "TON"),
            ("formwork", "SFCA"),
            ("masonry", "SF"),
            ("masonry_brick", "SF"),
            ("masonry_stone", "SF"),
            ("structural_steel", "TON"),
            ("misc_metals", "TON"),
            ("metal_decking", "SF"),
            ("rough_carpentry", "BF"),
            ("finish_carpentry", "LF"),
            ("millwork", "LF"),
            ("roofing", "SQ"),
            ("waterproofing", "SF"),
            ("insulation", "SF"),
            ("siding", "SF"),
            ("fireproofing", "SF"),
            ("doors_hollow_metal", "EA"),
            ("doors_wood", "EA"),
            ("curtain_wall", "SF"),
            ("windows", "SF"),
            ("drywall", "SF"),
            ("painting", "SF"),
            ("ceramic_tile", "SF"),
            ("carpet", "SF"),
            ("acoustic_ceiling", "SF"),
            ("terrazzo", "SF"),
            ("toilet_accessories", "SET"),
            ("signage", "EA"),
            ("kitchen_equipment", "EA"),
            ("window_treatment", "SF"),
            ("elevator_hydraulic", "EA"),
            ("elevator_traction", "EA"),
            ("fire_sprinkler", "SF"),
            ("plumbing_rough", "SF"),
            ("plumbing_fixture", "EA"),
            ("hvac", "TON"),
            ("ductwork", "LB"),
            ("electrical_rough", "SF"),
            ("electrical_panel", "EA"),
            ("lighting", "SF"),
            ("excavation", "CY"),
            ("backfill", "CY"),
            ("grading", "SY"),
            ("piling", "LF"),
            ("asphalt_paving", "SY"),
            ("concrete_paving", "SY"),
            ("landscaping", "SF"),
            ("water_main", "LF"),
            ("sewer_main", "LF"),
            ("storm_drainage", "LF"),
            ("labor_carpenter", "HR"),
            ("labor_electrician", "HR"),
            ("labor_plumber", "HR"),
            ("labor_ironworker", "HR"),
            ("labor_general", "HR"),
            ("labor_operator", "HR"),
            ("labor_painter", "HR"),
            ("labor_sheet_metal", "HR"),
        ]
        for item in original_items:
            assert item in REFERENCE_COSTS, f"Missing original item: {item}"

    def test_base_costs_are_positive(self):
        from app.services.estimating.cost_database import REFERENCE_COSTS

        for key, ref in REFERENCE_COSTS.items():
            assert ref["base_cost"] > 0, f"{key} has non-positive base_cost"

    def test_region_factors_preserved(self):
        from app.services.estimating.cost_database import REGION_FACTORS

        assert REGION_FACTORS["national"] == 1.0
        assert REGION_FACTORS["northeast"] == 1.15
        assert REGION_FACTORS["southeast"] == 0.90

    def test_csi_category_map_expanded(self):
        from app.services.estimating.cost_database import _CSI_CATEGORY_MAP

        assert len(_CSI_CATEGORY_MAP) >= 35, (
            f"Expected 35+ CSI mappings, got {len(_CSI_CATEGORY_MAP)}"
        )


# ---------------------------------------------------------------------------
# 5. Material-specific uncertainty ranges
# ---------------------------------------------------------------------------


class TestUncertaintyRanges:
    def test_uncertainty_ranges_exist(self):
        from app.services.estimating.cost_database import MATERIAL_UNCERTAINTY_RANGES

        assert len(MATERIAL_UNCERTAINTY_RANGES) >= 25

    def test_lumber_range_wider_than_concrete(self):
        from app.services.estimating.cost_database import get_uncertainty_range

        _lumber_low, lumber_high = get_uncertainty_range("lumber")
        _concrete_low, concrete_high = get_uncertainty_range("concrete")
        assert lumber_high > concrete_high, (
            f"Lumber uncertainty ({lumber_high}) should be wider than concrete ({concrete_high})"
        )

    def test_specific_ranges(self):
        from app.services.estimating.cost_database import get_uncertainty_range

        # Lumber: ±20-35%
        low, high = get_uncertainty_range("lumber")
        assert 0.15 <= low <= 0.25
        assert 0.30 <= high <= 0.40

        # Concrete: ±8-12%
        low, high = get_uncertainty_range("concrete")
        assert 0.05 <= low <= 0.10
        assert 0.10 <= high <= 0.15

        # Steel: ±15-25%
        low, high = get_uncertainty_range("structural_steel")
        assert 0.10 <= low <= 0.20
        assert 0.20 <= high <= 0.30

    def test_default_fallback(self):
        from app.services.estimating.cost_database import get_uncertainty_range

        low, high = get_uncertainty_range("nonexistent_material_xyz")
        assert low == 0.10
        assert high == 0.20

    def test_substring_matching(self):
        from app.services.estimating.cost_database import get_uncertainty_range

        # "labor_carpenter" should match "labor"
        low, high = get_uncertainty_range("labor_carpenter")
        assert low == 0.05
        assert high == 0.12


# ---------------------------------------------------------------------------
# 6. Cost item enrichment
# ---------------------------------------------------------------------------


class TestCostEnrichment:
    @pytest.mark.asyncio
    async def test_enrich_returns_all_fields(self, bls_api_key):
        from app.services.estimating.cost_database import enrich_cost_item

        result = await enrich_cost_item("concrete", "CY")
        assert "unit_cost" in result
        assert "adjusted_cost" in result
        assert "ppi_factor" in result
        assert "trend_12m" in result
        assert "trend_pct_12m" in result
        assert "uncertainty_low" in result
        assert "uncertainty_high" in result
        assert "cost_min" in result
        assert "cost_max" in result
        assert "data_source" in result

    @pytest.mark.asyncio
    async def test_enrich_trend_is_valid(self, bls_api_key):
        from app.services.estimating.cost_database import enrich_cost_item

        result = await enrich_cost_item("structural_steel", "TON")
        assert result["trend_12m"] in ("rising", "falling", "stable")
        assert isinstance(result["trend_pct_12m"], float)

    @pytest.mark.asyncio
    async def test_enrich_cost_bounds(self, bls_api_key):
        from app.services.estimating.cost_database import enrich_cost_item

        result = await enrich_cost_item("rough_carpentry", "BF", region="national")
        # cost_min < adjusted_cost < cost_max
        assert result["cost_min"] < result["adjusted_cost"]
        assert result["cost_max"] > result["adjusted_cost"]

    @pytest.mark.asyncio
    async def test_enrich_unknown_category(self):
        from app.services.estimating.cost_database import enrich_cost_item

        result = await enrich_cost_item("nonexistent_xyz", "EA")
        assert result["unit_cost"] == 0.0
        assert result["data_source"] == "none"

    @pytest.mark.asyncio
    async def test_enrich_with_region(self, bls_api_key):
        from app.services.estimating.cost_database import enrich_cost_item

        nat = await enrich_cost_item("concrete", "CY", region="national")
        ne = await enrich_cost_item("concrete", "CY", region="northeast")
        # Northeast should be higher than national (1.15x)
        assert ne["adjusted_cost"] > nat["adjusted_cost"]


# ---------------------------------------------------------------------------
# 7. Existing public API backward compatibility
# ---------------------------------------------------------------------------


class TestPublicAPICompat:
    @pytest.mark.asyncio
    async def test_get_current_cost_returns_expected_shape(self, bls_api_key):
        from app.services.estimating.cost_database import get_current_cost

        result = await get_current_cost("concrete", "Ready-mix 4000 PSI", "CY")
        assert "unit_cost" in result
        assert "adjusted_cost" in result
        assert "ppi_factor" in result
        assert "data_source" in result
        assert "effective_date" in result
        assert result["unit_cost"] == 185.0

    @pytest.mark.asyncio
    async def test_match_costs_returns_expected_shape(self, bls_api_key):
        from app.services.estimating.cost_database import match_costs

        quantities = [
            {"description": "Concrete slab", "csi_code": "03 30 00", "quantity": 100},
        ]
        result = await match_costs(quantities)
        assert len(result) == 1
        assert "unit_cost" in result[0]
        assert "total_cost" in result[0]
        assert "data_source" in result[0]

    @pytest.mark.asyncio
    async def test_resolve_series_id_works(self):
        from app.services.estimating.cost_database import _resolve_series_id

        assert _resolve_series_id("concrete") == "WPUIP2300001"
        assert _resolve_series_id("structural_steel") == "WPU101"
        assert _resolve_series_id("lumber") == "WPU081"
        assert _resolve_series_id("unknown_xyz") == "WPUIP2300001"  # default

    @pytest.mark.asyncio
    async def test_score_match_csi_prefix(self):
        from app.services.estimating.cost_database import _score_match

        score = _score_match("structural steel W shapes", "05 12 00", ("structural_steel", "TON"))
        assert score >= 100  # CSI match bonus

    @pytest.mark.asyncio
    async def test_score_match_keyword(self):
        from app.services.estimating.cost_database import _score_match

        score = _score_match("concrete slab on grade", "", ("concrete_slab", "CY"))
        assert score >= 50  # category name match


# ---------------------------------------------------------------------------
# 8. Caching behavior
# ---------------------------------------------------------------------------


class TestCaching:
    @pytest.mark.asyncio
    async def test_ppi_cache_hit(self, bls_api_key):
        from app.services.estimating.cost_database import (
            _ppi_cache,
            fetch_bls_ppi,
        )

        result1 = await fetch_bls_ppi("WPUIP2300001")
        assert "WPUIP2300001" in _ppi_cache

        with patch("httpx.AsyncClient.post", side_effect=Exception("blocked")):
            result2 = await fetch_bls_ppi("WPUIP2300001")
        assert result1["ppi_factor"] == result2["ppi_factor"]

    @pytest.mark.asyncio
    async def test_stale_cache_on_failure(self, bls_api_key):
        from app.services.estimating.cost_database import (
            _PPI_CACHE_TTL,
            _ppi_cache,
            fetch_bls_ppi,
        )

        result1 = await fetch_bls_ppi("WPU101")
        # Expire cache
        _ppi_cache["WPU101"] = (_ppi_cache["WPU101"][0], time.time() - _PPI_CACHE_TTL - 1)

        with patch("httpx.AsyncClient.post", side_effect=Exception("down")):
            result2 = await fetch_bls_ppi("WPU101")
        assert result2["ppi_factor"] == result1["ppi_factor"]


# ---------------------------------------------------------------------------
# 9. Monte Carlo uses material-specific ranges
# ---------------------------------------------------------------------------


class TestMonteCarloMaterialRanges:
    @pytest.mark.asyncio
    async def test_monte_carlo_uses_category_ranges(self):
        """Monte Carlo should use wider ranges for lumber than concrete."""
        try:
            from app.services.estimating.monte_carlo import run_monte_carlo
        except ImportError:
            pytest.skip("numpy not installed")

        lumber_items = [
            {"description": "lumber", "category": "lumber", "unit_cost": 100.0, "quantity": 1000},
        ]
        concrete_items = [
            {
                "description": "concrete",
                "category": "concrete",
                "unit_cost": 100.0,
                "quantity": 1000,
            },
        ]

        lumber_result = await run_monte_carlo(lumber_items, seed=42)
        concrete_result = await run_monte_carlo(concrete_items, seed=42)

        # Lumber should have wider spread (higher std_dev relative to mean)
        lumber_cv = float(lumber_result["std_dev"]) / float(lumber_result["mean"])
        concrete_cv = float(concrete_result["std_dev"]) / float(concrete_result["mean"])
        assert lumber_cv > concrete_cv, (
            f"Lumber CV ({lumber_cv:.4f}) should exceed concrete CV ({concrete_cv:.4f})"
        )

    @pytest.mark.asyncio
    async def test_explicit_bounds_override_material_ranges(self):
        """When cost_min/cost_max are explicitly set, they should be used."""
        try:
            from app.services.estimating.monte_carlo import run_monte_carlo
        except ImportError:
            pytest.skip("numpy not installed")

        items = [
            {
                "description": "test",
                "category": "lumber",
                "unit_cost": 100.0,
                "quantity": 1000,
                "cost_min": 99.0,
                "cost_max": 101.0,
            },
        ]
        result = await run_monte_carlo(items, seed=42)
        # With very tight bounds, std_dev should be very small
        assert float(result["std_dev"]) < 1000, "Tight bounds should produce small std_dev"


# ---------------------------------------------------------------------------
# 10. BLSDataUnavailableError
# ---------------------------------------------------------------------------


class TestBLSError:
    def test_error_is_importable(self):
        from app.services.estimating.cost_database import BLSDataUnavailableError

        assert issubclass(BLSDataUnavailableError, Exception)

    def test_error_has_message(self):
        from app.services.estimating.cost_database import BLSDataUnavailableError

        err = BLSDataUnavailableError("test message")
        assert "test message" in str(err)
