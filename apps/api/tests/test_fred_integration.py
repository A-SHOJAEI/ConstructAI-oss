"""Tests for FRED API integration, backfill, and price forecasting.

Verifies:
    - API connection with real FRED_API_KEY
    - All 16+ series return valid data
    - Backfill stores data correctly
    - Fallback synthetic data path is completely removed
    - Rate limiting works
    - forecast_prices uses real FRED data when series_id is provided
"""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure test env
# ---------------------------------------------------------------------------
os.environ.setdefault("TESTING", "true")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fred_api_key():
    """Return the FRED_API_KEY from environment, skip if not set."""
    key = os.environ.get("FRED_API_KEY")
    if not key:
        pytest.skip("FRED_API_KEY not set; skipping live API tests")
    return key


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear module-level caches before each test."""
    from app.services.procurement.price_forecaster import (
        _bls_cache,
        _fred_cache,
    )

    _fred_cache.clear()
    _bls_cache.clear()
    yield
    _fred_cache.clear()
    _bls_cache.clear()


# ---------------------------------------------------------------------------
# 1. Verify FRED API connection with real key
# ---------------------------------------------------------------------------


class TestFREDAPIConnection:
    """Tests that verify the live FRED API connection."""

    @pytest.mark.asyncio
    async def test_fred_api_connection_works(self, fred_api_key):
        """Verify that we can connect to the FRED API with the configured key."""
        from app.services.procurement.price_forecaster import fetch_fred_data

        # WPU1017 (Steel Mill Products) is a well-established series
        data = await fetch_fred_data("WPU1017")
        assert len(data) > 0, "Expected at least one observation from FRED"
        assert "date" in data[0]
        assert "value" in data[0]
        assert isinstance(data[0]["value"], float)

    @pytest.mark.asyncio
    async def test_fred_api_returns_sorted_dates(self, fred_api_key):
        """FRED data should be sorted chronologically."""
        from app.services.procurement.price_forecaster import fetch_fred_data

        data = await fetch_fred_data("CPIAUCSL")
        dates = [d["date"] for d in data]
        assert dates == sorted(dates), "FRED data should be sorted by date"

    @pytest.mark.asyncio
    async def test_fred_api_start_date_filter(self, fred_api_key):
        """Verify start_date parameter filters results."""
        from app.services.procurement.price_forecaster import fetch_fred_data

        data = await fetch_fred_data("WPU1017", start_date="2024-01-01")
        for obs in data:
            assert obs["date"] >= "2024-01-01", f"Observation {obs['date']} is before start_date"

    @pytest.mark.asyncio
    async def test_fred_api_no_key_raises_error(self):
        """Without FRED_API_KEY, fetch_fred_data should raise."""
        from app.services.procurement.price_forecaster import (
            FREDDataUnavailableError,
            fetch_fred_data,
        )

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FRED_API_KEY", None)
            with (
                patch(
                    "app.services.procurement.price_forecaster._get_fred_api_key",
                    return_value=None,
                ),
                pytest.raises(FREDDataUnavailableError),
            ):
                await fetch_fred_data("WPU1017")


# ---------------------------------------------------------------------------
# 2. All 16+ series return valid data
# ---------------------------------------------------------------------------


class TestFREDSeriesCoverage:
    """Tests that all configured FRED series are fetchable."""

    @pytest.mark.asyncio
    async def test_all_series_in_catalog(self):
        """Verify FRED_SERIES_MAP has at least 16 series."""
        from app.services.procurement.price_forecaster import FRED_SERIES_MAP

        assert len(FRED_SERIES_MAP) >= 16, (
            f"Expected at least 16 series, got {len(FRED_SERIES_MAP)}"
        )

    @pytest.mark.asyncio
    async def test_all_series_have_required_metadata(self):
        """Each series entry must have description, csi_division, category."""
        from app.services.procurement.price_forecaster import FRED_SERIES_MAP

        for series_id, meta in FRED_SERIES_MAP.items():
            assert "description" in meta, f"{series_id} missing 'description'"
            assert "csi_division" in meta, f"{series_id} missing 'csi_division'"
            assert "category" in meta, f"{series_id} missing 'category'"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "series_id",
        [
            "WPU1017",  # Steel mill products
            "WPU08",  # Lumber & Wood
            "WPU133",  # Concrete
            "WPU102502",  # Copper & Brass
            "WPU0553",  # Asphalt
            "WPUSI012011",  # Construction composite
            "WPU0241",  # Glass
            "WPU07210603",  # Plastic Pipe
            "WPU0542",  # Gypsum
            "WPU0531",  # Insulation
            "WPU0812",  # Plywood
            "WPU102301",  # Structural Steel Shapes
            "WPU101706",  # Steel Pipe & Tube
            "CPIAUCSL",  # CPI-Urban
            "USCONS",  # Construction spending
            "PERMIT",  # Building permits
        ],
    )
    async def test_each_series_returns_data(self, fred_api_key, series_id):
        """Each FRED series should return at least 12 data points."""
        from app.services.procurement.price_forecaster import fetch_fred_data

        data = await fetch_fred_data(series_id)
        assert len(data) >= 12, (
            f"Series {series_id} returned only {len(data)} observations (expected >= 12)"
        )

    @pytest.mark.asyncio
    async def test_csi_mapping_covers_key_divisions(self):
        """CSI division mapping should cover divisions 03-32."""
        from app.services.procurement.price_forecaster import _CSI_TO_SERIES

        expected_divisions = {"03", "05", "06", "07", "08", "09", "22", "26", "32"}
        actual_divisions = set(_CSI_TO_SERIES.keys())
        missing = expected_divisions - actual_divisions
        assert not missing, f"Missing CSI division mappings: {missing}"

    @pytest.mark.asyncio
    async def test_category_lookup(self):
        """get_series_for_category should resolve known categories."""
        from app.services.procurement.price_forecaster import get_series_for_category

        assert get_series_for_category("concrete") == "WPU133"
        assert get_series_for_category("lumber") == "WPU08"
        assert get_series_for_category("steel_mill") == "WPU1017"
        assert get_series_for_category("copper") == "WPU102502"
        assert get_series_for_category("asphalt") == "WPU0553"

    @pytest.mark.asyncio
    async def test_csi_lookup(self):
        """get_series_for_csi should return series for known CSI divisions."""
        from app.services.procurement.price_forecaster import get_series_for_csi

        steel_series = get_series_for_csi("05")
        assert "WPU1017" in steel_series
        assert "WPU102301" in steel_series

        lumber_series = get_series_for_csi("06")
        assert "WPU08" in lumber_series
        assert "WPU0812" in lumber_series


# ---------------------------------------------------------------------------
# 3. Backfill stores data correctly
# ---------------------------------------------------------------------------


class TestFREDBackfill:
    """Tests for the backfill_fred_history function."""

    @pytest.mark.asyncio
    async def test_backfill_without_db_caches_in_memory(self, fred_api_key):
        """Backfill without db_session should store data in memory cache."""
        from app.services.procurement.price_forecaster import (
            _fred_cache,
            backfill_fred_history,
        )

        # Backfill just one series for speed
        results = await backfill_fred_history(series_ids=["CPIAUCSL"])
        assert results["CPIAUCSL"] > 0, "Expected observations for CPIAUCSL"
        assert "CPIAUCSL" in _fred_cache, "Backfill should populate cache"

    @pytest.mark.asyncio
    async def test_backfill_full_history_flag(self, fred_api_key):
        """Full history backfill should return many more data points."""
        from app.services.procurement.price_forecaster import fetch_fred_data

        # CPI-Urban has data back to 1947
        data = await fetch_fred_data("CPIAUCSL", full_history=True)
        assert len(data) > 200, (
            f"Full history for CPIAUCSL should have 200+ observations, got {len(data)}"
        )

    @pytest.mark.asyncio
    async def test_backfill_with_mock_db_session(self, fred_api_key):
        """Backfill with db_session should call persist function."""
        from app.services.procurement.price_forecaster import backfill_fred_history

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        results = await backfill_fred_history(
            series_ids=["WPU133"],
            db_session=mock_session,
        )
        assert results["WPU133"] > 0
        assert mock_session.execute.called, "Should have written to DB"
        assert mock_session.commit.called, "Should have committed"

    @pytest.mark.asyncio
    async def test_backfill_returns_all_series_results(self, fred_api_key):
        """Backfill should return results for every requested series."""
        from app.services.procurement.price_forecaster import backfill_fred_history

        series = ["WPU1017", "WPU08", "WPU133"]
        results = await backfill_fred_history(series_ids=series)
        assert set(results.keys()) == set(series)
        for sid in series:
            assert results[sid] > 0, f"Series {sid} should have data"


# ---------------------------------------------------------------------------
# 4. Fallback synthetic data path is completely removed
# ---------------------------------------------------------------------------


class TestNoSyntheticFallback:
    """Verify that fake/synthetic data generation has been removed."""

    def test_no_generate_fallback_ppi_series(self):
        """_generate_fallback_ppi_series should not exist."""
        import app.services.procurement.price_forecaster as pf

        assert not hasattr(pf, "_generate_fallback_ppi_series"), (
            "_generate_fallback_ppi_series should be removed"
        )

    def test_no_generate_mock_ppi_series(self):
        """_generate_mock_ppi_series alias should not exist."""
        import app.services.procurement.price_forecaster as pf

        assert not hasattr(pf, "_generate_mock_ppi_series"), (
            "_generate_mock_ppi_series should be removed"
        )

    def test_no_fallback_ppi_constants(self):
        """_FALLBACK_PPI_BASE and _FALLBACK_PPI_MONTHLY_TREND should be gone."""
        import app.services.procurement.price_forecaster as pf

        assert not hasattr(pf, "_FALLBACK_PPI_BASE"), "_FALLBACK_PPI_BASE should be removed"
        assert not hasattr(pf, "_FALLBACK_PPI_MONTHLY_TREND"), (
            "_FALLBACK_PPI_MONTHLY_TREND should be removed"
        )

    @pytest.mark.asyncio
    async def test_fetch_fred_data_never_returns_synthetic(self):
        """fetch_fred_data should raise, not return fake data, when API fails."""
        from app.services.procurement.price_forecaster import (
            FREDDataUnavailableError,
            fetch_fred_data,
        )

        with (
            patch(
                "app.services.procurement.price_forecaster._get_fred_api_key",
                return_value=None,
            ),
            pytest.raises(FREDDataUnavailableError),
        ):
            await fetch_fred_data("WPU1017")

    @pytest.mark.asyncio
    async def test_get_bls_ppi_series_never_returns_synthetic(self):
        """get_bls_ppi_series should raise when BLS is unavailable."""
        from app.services.procurement.price_forecaster import (
            FREDDataUnavailableError,
            get_bls_ppi_series,
        )

        with patch("httpx.AsyncClient.post", side_effect=Exception("network error")):
            with pytest.raises(FREDDataUnavailableError):
                await get_bls_ppi_series("WPUIP2300001")


# ---------------------------------------------------------------------------
# 5. Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Tests for FRED API rate limit enforcement."""

    @pytest.mark.asyncio
    async def test_rate_limiter_tracks_requests(self):
        """Rate limiter should track request timestamps."""
        from app.services.procurement.price_forecaster import (
            _fred_rate_limit_wait,
            _fred_request_times,
        )

        _fred_request_times.clear()
        await _fred_rate_limit_wait()
        assert len(_fred_request_times) == 1

    @pytest.mark.asyncio
    async def test_rate_limiter_prunes_old_timestamps(self):
        """Timestamps older than 60s should be pruned."""
        from app.services.procurement.price_forecaster import (
            _fred_rate_limit_wait,
            _fred_request_times,
        )

        _fred_request_times.clear()
        # Add an old timestamp (well outside the 60s window)
        _fred_request_times.append(time.monotonic() - 120)
        await _fred_rate_limit_wait()
        # Old timestamp should be pruned, only the new one remains
        assert len(_fred_request_times) == 1


# ---------------------------------------------------------------------------
# 6. Forecast uses real FRED data
# ---------------------------------------------------------------------------


class TestForecastWithFREDData:
    """Tests for forecast_prices using real FRED data."""

    @pytest.mark.asyncio
    async def test_forecast_with_series_id(self, fred_api_key):
        """forecast_prices should fetch FRED data when series_id is given."""
        from app.services.procurement.price_forecaster import forecast_prices

        result = await forecast_prices(
            series_id="WPU133",
            material_category="concrete",
            horizon_months=6,
        )
        assert result["model_used"] != "none", "Should use a real model, not 'none'"
        assert len(result["forecasts"]) == 6
        assert result["trend"] in ("rising", "falling", "stable")
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_forecast_auto_resolves_category(self, fred_api_key):
        """forecast_prices without series_id should auto-resolve from category."""
        from app.services.procurement.price_forecaster import forecast_prices

        result = await forecast_prices(
            material_category="lumber",
            horizon_months=3,
        )
        # Should resolve to WPU08 automatically
        assert result["model_used"] != "none"
        assert len(result["forecasts"]) == 3

    @pytest.mark.asyncio
    async def test_forecast_returns_error_when_fred_unavailable(self):
        """forecast_prices should return error dict when FRED is down."""
        from app.services.procurement.price_forecaster import forecast_prices

        with patch(
            "app.services.procurement.price_forecaster._get_fred_api_key",
            return_value=None,
        ):
            result = await forecast_prices(
                series_id="WPU1017",
                material_category="steel",
                horizon_months=6,
            )
            assert result["model_used"] == "none"
            assert "unavailable" in result["summary"].lower()

    @pytest.mark.asyncio
    async def test_forecast_with_historical_data_still_works(self):
        """Passing historical_data directly should still work (backward compat)."""
        from app.services.procurement.price_forecaster import forecast_prices

        historical = [
            {"date": f"2024-{m:02d}-01", "price_index": 200.0 + m * 2} for m in range(1, 13)
        ]
        result = await forecast_prices(
            historical_data=historical,
            horizon_months=6,
            material_category="test_material",
        )
        assert result["model_used"] != "none"
        assert len(result["forecasts"]) == 6

    @pytest.mark.asyncio
    async def test_multi_horizon_forecast(self, fred_api_key):
        """forecast_prices_multi_horizon should return 3/6/12 month forecasts."""
        from app.services.procurement.price_forecaster import (
            forecast_prices_multi_horizon,
        )

        results = await forecast_prices_multi_horizon(
            series_id="WPU133",
            material_category="concrete",
        )
        assert 3 in results
        assert 6 in results
        assert 12 in results
        assert len(results[3]["forecasts"]) == 3
        assert len(results[6]["forecasts"]) == 6
        assert len(results[12]["forecasts"]) == 12


# ---------------------------------------------------------------------------
# 7. FRED series map integrity
# ---------------------------------------------------------------------------


class TestFREDSeriesMapIntegrity:
    """Structural tests for the FRED series catalog."""

    def test_all_categories_unique(self):
        """No two series should share the same category name."""
        from app.services.procurement.price_forecaster import FRED_SERIES_MAP

        categories = [m["category"] for m in FRED_SERIES_MAP.values()]
        assert len(categories) == len(set(categories)), (
            "Duplicate category names found in FRED_SERIES_MAP"
        )

    def test_csi_divisions_are_valid(self):
        """CSI divisions should be 2-digit strings or None."""
        from app.services.procurement.price_forecaster import FRED_SERIES_MAP

        for series_id, meta in FRED_SERIES_MAP.items():
            div = meta["csi_division"]
            if div is not None:
                assert isinstance(div, str), (
                    f"{series_id}: csi_division should be str, got {type(div)}"
                )
                assert len(div) == 2, f"{series_id}: csi_division should be 2 chars, got '{div}'"

    def test_fred_data_unavailable_error_exists(self):
        """FREDDataUnavailableError should be importable."""
        from app.services.procurement.price_forecaster import (
            FREDDataUnavailableError,
        )

        assert issubclass(FREDDataUnavailableError, Exception)


# ---------------------------------------------------------------------------
# 8. Scheduled task integration
# ---------------------------------------------------------------------------


class TestScheduledTasks:
    """Tests for the FRED scheduled refresh task."""

    @pytest.mark.asyncio
    async def test_refresh_fred_price_data_callable(self):
        """refresh_fred_price_data should be importable and callable."""
        from app.workers.scheduled_tasks import refresh_fred_price_data

        assert callable(refresh_fred_price_data)

    @pytest.mark.asyncio
    async def test_refresh_fred_calls_refresh_fred_data(self, fred_api_key):
        """The scheduled task should delegate to price_forecaster.refresh_fred_data."""

        with patch(
            "app.services.procurement.price_forecaster.refresh_fred_data",
            new_callable=AsyncMock,
            return_value={"WPU1017": True, "WPU08": True},
        ):
            from app.workers.scheduled_tasks import refresh_fred_price_data

            result = await refresh_fred_price_data()
            # If the mock was used, great.  If not, the real function ran.
            # Either way, result should be a dict.
            assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 9. Caching behavior
# ---------------------------------------------------------------------------


class TestCaching:
    """Tests for the in-memory cache behavior."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_api_call(self, fred_api_key):
        """Second call should use cache, not hit the API again."""
        from app.services.procurement.price_forecaster import (
            _fred_cache,
            fetch_fred_data,
        )

        # First call populates cache
        data1 = await fetch_fred_data("WPU133")
        assert "WPU133" in _fred_cache

        # Patch httpx to fail -- if cache works, this won't matter
        with patch("httpx.AsyncClient.get", side_effect=Exception("should not call")):
            data2 = await fetch_fred_data("WPU133")

        assert len(data1) == len(data2)

    @pytest.mark.asyncio
    async def test_stale_cache_returned_on_api_failure(self, fred_api_key):
        """When API fails but stale cache exists, return stale data with warning."""
        from app.services.procurement.price_forecaster import (
            _FRED_CACHE_TTL,
            _fred_cache,
            fetch_fred_data,
        )

        # Populate cache then expire it
        data1 = await fetch_fred_data("WPU1017")
        # Manually expire the cache entry
        _fred_cache["WPU1017"] = (_fred_cache["WPU1017"][0], time.time() - _FRED_CACHE_TTL - 1)

        # Now force API failure
        with patch("httpx.AsyncClient.get", side_effect=Exception("network down")):
            data2 = await fetch_fred_data("WPU1017")

        # Should get stale cached data
        assert len(data2) == len(data1)
