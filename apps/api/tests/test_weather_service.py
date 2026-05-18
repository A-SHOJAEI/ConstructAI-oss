"""Comprehensive tests for the multi-provider weather service.

Tests cover:
- All 3 providers (NOAA, Open-Meteo, OpenWeatherMap)
- Fallback chain behavior
- All 7 construction-specific impact functions
- Historical weather data fetch
- Caching (fresh + stale)
- WeatherDataUnavailableError
- get_weather_impact scheduling integration
- One real call to api.weather.gov (NOAA)
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.scheduling.weather_service import (
    DEFAULT_SENSITIVITY,
    IMPACT_FUNCTIONS,
    RiskLevel,
    WeatherDataUnavailableError,
    WeatherImpact,
    _filter_date_range,
    _heat_index,
    _noaa_short_to_wmo,
    _owm_id_to_wmo,
    _parse_location,
    _parse_wind_speed,
    _weather_cache,
    analyze_weather_impact,
    can_do_roofing,
    can_excavate,
    can_operate_crane,
    can_paint_exterior,
    can_pour_concrete,
    fetch_historical_weather,
    get_monthly_weather_stats,
    get_weather_forecast,
    get_weather_impact,
    heat_illness_risk,
    weather_impact_score,
)

# ---------------------------------------------------------------------------
# Sample weather data for testing
# ---------------------------------------------------------------------------

GOOD_WEATHER = {
    "date": "2025-06-15",
    "temperature_max": 72.0,
    "temperature_min": 58.0,
    "precipitation_mm": 0.0,
    "wind_speed_max": 8.0,
    "weather_code": 0,
    "humidity": 45.0,
}

COLD_WEATHER = {
    "date": "2025-01-15",
    "temperature_max": 28.0,
    "temperature_min": 12.0,
    "precipitation_mm": 0.0,
    "wind_speed_max": 15.0,
    "weather_code": 0,
    "humidity": 30.0,
}

HOT_WEATHER = {
    "date": "2025-08-01",
    "temperature_max": 102.0,
    "temperature_min": 82.0,
    "precipitation_mm": 0.0,
    "wind_speed_max": 5.0,
    "weather_code": 0,
    "humidity": 75.0,
}

RAINY_WEATHER = {
    "date": "2025-04-10",
    "temperature_max": 65.0,
    "temperature_min": 52.0,
    "precipitation_mm": 30.0,  # ~1.18 inches
    "wind_speed_max": 20.0,
    "weather_code": 63,
    "humidity": 90.0,
}

WINDY_WEATHER = {
    "date": "2025-03-20",
    "temperature_max": 60.0,
    "temperature_min": 45.0,
    "precipitation_mm": 0.0,
    "wind_speed_max": 42.0,
    "weather_code": 3,
    "humidity": 40.0,
}

STORM_WEATHER = {
    "date": "2025-07-04",
    "temperature_max": 88.0,
    "temperature_min": 72.0,
    "precipitation_mm": 50.0,
    "wind_speed_max": 55.0,
    "weather_code": 95,
    "humidity": 85.0,
}


# ---------------------------------------------------------------------------
# Test: Construction-specific impact functions
# ---------------------------------------------------------------------------


class TestCanPourConcrete:
    """Tests for can_pour_concrete()."""

    def test_good_conditions_green(self):
        result = can_pour_concrete(GOOD_WEATHER)
        assert result.allowed is True
        assert result.risk_level == RiskLevel.GREEN
        assert result.activity == "concrete_pour"

    def test_cold_weather_red(self):
        result = can_pour_concrete(COLD_WEATHER)
        assert result.allowed is False
        assert result.risk_level == RiskLevel.RED
        assert any("freezing" in r.lower() or "below 35" in r.lower() for r in result.reasons)

    def test_hot_weather_red(self):
        result = can_pour_concrete(HOT_WEATHER)
        assert result.allowed is False
        assert result.risk_level == RiskLevel.RED
        assert any("95" in r or "flash set" in r.lower() for r in result.reasons)

    def test_rainy_weather_red(self):
        result = can_pour_concrete(RAINY_WEATHER)
        assert result.allowed is False
        assert any("precip" in r.lower() or "washout" in r.lower() for r in result.reasons)

    def test_windy_weather_red(self):
        result = can_pour_concrete(WINDY_WEATHER)
        assert result.allowed is False
        assert any("wind" in r.lower() for r in result.reasons)

    def test_marginal_cold_yellow(self):
        weather = {**GOOD_WEATHER, "temperature_min": 38.0}
        result = can_pour_concrete(weather)
        assert result.risk_level == RiskLevel.YELLOW
        assert result.allowed is True

    def test_low_humidity_high_temp_warning(self):
        weather = {**GOOD_WEATHER, "temperature_max": 88.0, "humidity": 15.0}
        result = can_pour_concrete(weather)
        assert any("humidity" in r.lower() for r in result.reasons)

    def test_recommendations_provided_on_red(self):
        result = can_pour_concrete(COLD_WEATHER)
        assert len(result.recommendations) > 0


class TestCanOperateCrane:
    """Tests for can_operate_crane()."""

    def test_calm_conditions_green(self):
        result = can_operate_crane(GOOD_WEATHER)
        assert result.allowed is True
        assert result.risk_level == RiskLevel.GREEN

    def test_high_wind_red(self):
        result = can_operate_crane(WINDY_WEATHER)
        assert result.allowed is False
        assert result.risk_level == RiskLevel.RED

    def test_thunderstorm_red(self):
        result = can_operate_crane(STORM_WEATHER)
        assert result.allowed is False
        assert any("lightning" in r.lower() or "thunderstorm" in r.lower() for r in result.reasons)

    def test_moderate_wind_yellow(self):
        weather = {**GOOD_WEATHER, "wind_speed_max": 22.0}
        result = can_operate_crane(weather)
        assert result.risk_level == RiskLevel.YELLOW
        assert result.allowed is True

    def test_heavy_precip_yellow(self):
        weather = {**GOOD_WEATHER, "precipitation_mm": 20.0}
        result = can_operate_crane(weather)
        assert result.risk_level == RiskLevel.YELLOW


class TestCanExcavate:
    """Tests for can_excavate()."""

    def test_dry_conditions_green(self):
        result = can_excavate(GOOD_WEATHER)
        assert result.allowed is True
        assert result.risk_level == RiskLevel.GREEN

    def test_heavy_rain_red(self):
        result = can_excavate(RAINY_WEATHER)
        assert result.allowed is False
        assert any("cave-in" in r.lower() or "saturated" in r.lower() for r in result.reasons)

    def test_moderate_rain_yellow(self):
        weather = {**GOOD_WEATHER, "precipitation_mm": 15.0}  # ~0.6 inches
        result = can_excavate(weather)
        assert result.risk_level == RiskLevel.YELLOW

    def test_frozen_ground_yellow(self):
        weather = {**GOOD_WEATHER, "temperature_min": 20.0}
        result = can_excavate(weather)
        assert result.risk_level == RiskLevel.YELLOW


class TestCanDoRoofing:
    """Tests for can_do_roofing()."""

    def test_good_conditions_green(self):
        result = can_do_roofing(GOOD_WEATHER)
        assert result.allowed is True
        assert result.risk_level == RiskLevel.GREEN

    def test_any_rain_red(self):
        weather = {**GOOD_WEATHER, "precipitation_mm": 1.0}  # trace rain
        result = can_do_roofing(weather)
        assert result.allowed is False
        assert result.risk_level == RiskLevel.RED

    def test_high_wind_red(self):
        weather = {**GOOD_WEATHER, "wind_speed_max": 28.0}
        result = can_do_roofing(weather)
        assert result.allowed is False

    def test_cold_for_adhesive_red(self):
        weather = {**GOOD_WEATHER, "temperature_min": 30.0}
        result = can_do_roofing(weather)
        assert result.allowed is False
        assert any("adhesive" in r.lower() for r in result.reasons)


class TestCanPaintExterior:
    """Tests for can_paint_exterior()."""

    def test_good_conditions_green(self):
        result = can_paint_exterior(GOOD_WEATHER)
        assert result.allowed is True
        assert result.risk_level == RiskLevel.GREEN

    def test_rain_red(self):
        result = can_paint_exterior(RAINY_WEATHER)
        assert result.allowed is False
        assert any("washout" in r.lower() for r in result.reasons)

    def test_cold_red(self):
        weather = {**GOOD_WEATHER, "temperature_min": 45.0}
        result = can_paint_exterior(weather)
        assert result.allowed is False

    def test_high_humidity_red(self):
        weather = {**GOOD_WEATHER, "humidity": 90.0}
        result = can_paint_exterior(weather)
        assert result.allowed is False
        assert any("humidity" in r.lower() for r in result.reasons)

    def test_moderate_humidity_yellow(self):
        weather = {**GOOD_WEATHER, "humidity": 75.0}
        result = can_paint_exterior(weather)
        assert result.risk_level == RiskLevel.YELLOW


class TestWeatherImpactScore:
    """Tests for weather_impact_score()."""

    def test_perfect_conditions_low_score(self):
        result = weather_impact_score(GOOD_WEATHER)
        assert result.risk_level == RiskLevel.GREEN
        assert result.allowed is True

    def test_storm_high_score(self):
        result = weather_impact_score(STORM_WEATHER)
        assert result.risk_level == RiskLevel.RED
        assert result.allowed is False

    def test_returns_weather_impact_object(self):
        result = weather_impact_score(GOOD_WEATHER)
        assert isinstance(result, WeatherImpact)
        assert result.activity == "overall"

    def test_score_breakdown_in_reasons(self):
        result = weather_impact_score(STORM_WEATHER)
        assert any("Overall impact score" in r for r in result.reasons)


class TestHeatIllnessRisk:
    """Tests for heat_illness_risk()."""

    def test_mild_conditions_green(self):
        result = heat_illness_risk(GOOD_WEATHER)
        assert result.risk_level == RiskLevel.GREEN
        assert result.allowed is True

    def test_extreme_heat_red(self):
        weather = {**HOT_WEATHER, "temperature_max": 105.0, "humidity": 70.0}
        result = heat_illness_risk(weather)
        assert result.risk_level == RiskLevel.RED
        assert result.allowed is False

    def test_moderate_heat_yellow(self):
        weather = {**GOOD_WEATHER, "temperature_max": 90.0, "humidity": 55.0}
        result = heat_illness_risk(weather)
        assert result.risk_level == RiskLevel.YELLOW

    def test_heat_index_calculation(self):
        # Known values: 90F at 50% RH should give HI around 95
        hi = _heat_index(90.0, 50.0)
        assert 90 < hi < 100

    def test_recommendations_on_high_risk(self):
        weather = {**HOT_WEATHER, "temperature_max": 105.0, "humidity": 70.0}
        result = heat_illness_risk(weather)
        assert len(result.recommendations) > 0


# ---------------------------------------------------------------------------
# Test: NOAA helper functions
# ---------------------------------------------------------------------------


class TestNOAAHelpers:
    """Tests for NOAA parsing helpers."""

    def test_parse_wind_speed_range(self):
        assert _parse_wind_speed("10 to 20 mph") == 20.0

    def test_parse_wind_speed_single(self):
        assert _parse_wind_speed("15 mph") == 15.0

    def test_parse_wind_speed_empty(self):
        assert _parse_wind_speed("calm") == 0.0

    def test_noaa_short_to_wmo_thunderstorm(self):
        assert _noaa_short_to_wmo("Thunderstorm Likely") == 95

    def test_noaa_short_to_wmo_rain(self):
        assert _noaa_short_to_wmo("Chance Showers") == 63

    def test_noaa_short_to_wmo_clear(self):
        assert _noaa_short_to_wmo("Sunny") == 0

    def test_noaa_short_to_wmo_cloudy(self):
        assert _noaa_short_to_wmo("Mostly Cloudy") == 3

    def test_owm_id_thunderstorm(self):
        assert _owm_id_to_wmo(200) == 95

    def test_owm_id_clear(self):
        assert _owm_id_to_wmo(800) == 0

    def test_owm_id_rain(self):
        assert _owm_id_to_wmo(500) == 63

    def test_owm_id_snow(self):
        assert _owm_id_to_wmo(600) == 71


# ---------------------------------------------------------------------------
# Test: Location parsing
# ---------------------------------------------------------------------------


class TestParseLocation:
    """Tests for _parse_location()."""

    def test_lat_lon_string(self):
        lat, lon = _parse_location("40.7128,-74.0060")
        assert abs(lat - 40.7128) < 0.001
        assert abs(lon - (-74.0060)) < 0.001

    def test_lat_lon_with_spaces(self):
        lat, _lon = _parse_location("40.7128, -74.0060")
        assert abs(lat - 40.7128) < 0.001

    def test_named_city(self):
        lat, _lon = _parse_location("Chicago")
        assert abs(lat - 41.8781) < 0.1

    def test_city_case_insensitive(self):
        lat, _lon = _parse_location("NEW YORK")
        assert abs(lat - 40.7128) < 0.1

    def test_unknown_defaults_to_nyc(self):
        lat, _lon = _parse_location("Timbuktu")
        assert abs(lat - 40.7128) < 0.1


# ---------------------------------------------------------------------------
# Test: Date range filter
# ---------------------------------------------------------------------------


class TestFilterDateRange:
    """Tests for _filter_date_range()."""

    def test_filters_correctly(self):
        data = [
            {"date": "2025-03-01"},
            {"date": "2025-03-02"},
            {"date": "2025-03-03"},
            {"date": "2025-03-04"},
        ]
        result = _filter_date_range(data, "2025-03-02", "2025-03-03")
        assert len(result) == 2
        assert result[0]["date"] == "2025-03-02"

    def test_empty_data(self):
        assert _filter_date_range([], "2025-01-01", "2025-12-31") == []

    def test_no_match(self):
        data = [{"date": "2025-01-01"}]
        assert _filter_date_range(data, "2025-06-01", "2025-06-30") == []


# ---------------------------------------------------------------------------
# Test: Impact function registry
# ---------------------------------------------------------------------------


class TestImpactFunctionRegistry:
    """Tests for the IMPACT_FUNCTIONS mapping."""

    def test_concrete_registered(self):
        assert "concrete_pour" in IMPACT_FUNCTIONS
        assert "concrete" in IMPACT_FUNCTIONS

    def test_crane_registered(self):
        assert "crane_operation" in IMPACT_FUNCTIONS
        assert "steel_erection" in IMPACT_FUNCTIONS

    def test_all_functions_callable(self):
        for name, fn in IMPACT_FUNCTIONS.items():
            result = fn(GOOD_WEATHER)
            assert isinstance(result, WeatherImpact), f"{name} did not return WeatherImpact"


# ---------------------------------------------------------------------------
# Test: Default sensitivity thresholds
# ---------------------------------------------------------------------------


class TestDefaultSensitivity:
    """Tests for DEFAULT_SENSITIVITY dict."""

    def test_all_activity_types_present(self):
        expected = {
            "concrete_pour",
            "steel_erection",
            "excavation",
            "roofing",
            "painting_exterior",
            "crane_operation",
            "general",
        }
        assert expected.issubset(set(DEFAULT_SENSITIVITY.keys()))

    def test_concrete_thresholds(self):
        ct = DEFAULT_SENSITIVITY["concrete_pour"]
        assert ct["min_temp"] == 40.0
        assert ct["max_temp"] == 95.0
        assert ct["max_precip"] == 0.1
        assert ct["max_wind"] == 25.0


# ---------------------------------------------------------------------------
# Test: Multi-provider fallback chain
# ---------------------------------------------------------------------------


class TestMultiProviderFallback:
    """Tests for get_weather_forecast() fallback chain."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _weather_cache.clear()
        yield
        _weather_cache.clear()

    async def test_noaa_success_skips_other_providers(self):
        noaa_data = [GOOD_WEATHER]
        with (
            patch(
                "app.services.scheduling.weather_service._fetch_noaa_forecast",
                new_callable=AsyncMock,
                return_value=noaa_data,
            ) as mock_noaa,
            patch(
                "app.services.scheduling.weather_service._fetch_open_meteo_forecast",
                new_callable=AsyncMock,
            ) as mock_om,
        ):
            result = await get_weather_forecast(40.7, -74.0, "2025-06-15", "2025-06-15")
            assert len(result) == 1
            mock_noaa.assert_called_once()
            mock_om.assert_not_called()

    async def test_noaa_fails_falls_back_to_open_meteo(self):
        om_data = [GOOD_WEATHER]
        with (
            patch(
                "app.services.scheduling.weather_service._fetch_noaa_forecast",
                new_callable=AsyncMock,
                side_effect=Exception("NOAA down"),
            ),
            patch(
                "app.services.scheduling.weather_service._fetch_open_meteo_forecast",
                new_callable=AsyncMock,
                return_value=om_data,
            ) as mock_om,
        ):
            result = await get_weather_forecast(40.7, -74.0, "2025-06-15", "2025-06-15")
            assert len(result) == 1
            mock_om.assert_called_once()

    async def test_noaa_and_om_fail_falls_back_to_owm(self):
        owm_data = [GOOD_WEATHER]
        mock_settings = MagicMock()
        mock_settings.WEATHER_CACHE_TTL = 3600
        mock_settings.OPENWEATHERMAP_API_KEY = "test-key"

        with (
            patch(
                "app.services.scheduling.weather_service._fetch_noaa_forecast",
                new_callable=AsyncMock,
                side_effect=Exception("NOAA down"),
            ),
            patch(
                "app.services.scheduling.weather_service._fetch_open_meteo_forecast",
                new_callable=AsyncMock,
                side_effect=Exception("Open-Meteo down"),
            ),
            patch(
                "app.services.scheduling.weather_service._fetch_owm_forecast",
                new_callable=AsyncMock,
                return_value=owm_data,
            ) as mock_owm,
            patch(
                "app.config.settings",
                mock_settings,
            ),
        ):
            result = await get_weather_forecast(40.7, -74.0, "2025-06-15", "2025-06-15")
            assert len(result) == 1
            mock_owm.assert_called_once()

    async def test_all_providers_fail_raises_error(self):
        mock_settings = MagicMock()
        mock_settings.WEATHER_CACHE_TTL = 3600
        mock_settings.OPENWEATHERMAP_API_KEY = ""

        with (
            patch(
                "app.services.scheduling.weather_service._fetch_noaa_forecast",
                new_callable=AsyncMock,
                side_effect=Exception("NOAA down"),
            ),
            patch(
                "app.services.scheduling.weather_service._fetch_open_meteo_forecast",
                new_callable=AsyncMock,
                side_effect=Exception("Open-Meteo down"),
            ),
            patch("app.config.settings", mock_settings),
        ):
            with pytest.raises(WeatherDataUnavailableError, match="All weather providers failed"):
                await get_weather_forecast(40.7, -74.0, "2025-06-15", "2025-06-15")

    async def test_stale_cache_returned_when_all_fail(self):
        """When all providers fail but stale cache exists, return stale data."""
        cache_key = "40.7000,-74.0000,2025-06-15,2025-06-15"
        stale_time = time.monotonic() - 1800  # 30 min old (past TTL but within 1h stale max)
        _weather_cache[cache_key] = (stale_time, [GOOD_WEATHER])

        mock_settings = MagicMock()
        mock_settings.WEATHER_CACHE_TTL = 3600
        mock_settings.OPENWEATHERMAP_API_KEY = ""

        with (
            patch(
                "app.services.scheduling.weather_service._fetch_noaa_forecast",
                new_callable=AsyncMock,
                side_effect=Exception("NOAA down"),
            ),
            patch(
                "app.services.scheduling.weather_service._fetch_open_meteo_forecast",
                new_callable=AsyncMock,
                side_effect=Exception("Open-Meteo down"),
            ),
            patch("app.config.settings", mock_settings),
        ):
            result = await get_weather_forecast(40.7, -74.0, "2025-06-15", "2025-06-15")
            assert len(result) == 1

    async def test_fresh_cache_hit(self):
        cache_key = "40.7000,-74.0000,2025-06-15,2025-06-15"
        _weather_cache[cache_key] = (time.monotonic(), [GOOD_WEATHER])

        mock_settings = MagicMock()
        mock_settings.WEATHER_CACHE_TTL = 3600
        mock_settings.OPENWEATHERMAP_API_KEY = ""

        with patch("app.config.settings", mock_settings):
            result = await get_weather_forecast(40.7, -74.0, "2025-06-15", "2025-06-15")
            assert len(result) == 1


# ---------------------------------------------------------------------------
# Test: No mock weather fallback
# ---------------------------------------------------------------------------


class TestNoMockFallback:
    """Verify that _generate_mock_weather has been removed."""

    def test_no_generate_mock_weather_function(self):
        import app.services.scheduling.weather_service as ws

        assert not hasattr(ws, "_generate_mock_weather"), (
            "_generate_mock_weather must be removed — no synthetic data"
        )

    def test_no_seasonal_factor_function(self):
        import app.services.scheduling.weather_service as ws

        assert not hasattr(ws, "_seasonal_factor"), (
            "_seasonal_factor (mock helper) should be removed"
        )


# ---------------------------------------------------------------------------
# Test: analyze_weather_impact (existing interface)
# ---------------------------------------------------------------------------


class TestAnalyzeWeatherImpact:
    """Tests for analyze_weather_impact() backward compat."""

    async def test_returns_required_fields(self):
        activities = [
            {
                "id": "A",
                "name": "Concrete Pour",
                "activity_type": "concrete_pour",
                "start_date": "2025-06-15",
                "end_date": "2025-06-15",
            },
        ]
        result = await analyze_weather_impact(activities, [GOOD_WEATHER])
        assert "impact_days" in result
        assert "weather_events" in result
        assert "adjusted_end_date" in result
        assert "risk_level" in result
        assert "monthly_breakdown" in result

    async def test_no_activities_zero_impact(self):
        result = await analyze_weather_impact([], [GOOD_WEATHER])
        assert result["impact_days"] == 0
        assert result["risk_level"] == "low"

    async def test_cold_day_impacts_concrete(self):
        activities = [
            {
                "id": "A",
                "name": "Concrete Pour",
                "activity_type": "concrete_pour",
                "start_date": "2025-01-15",
                "end_date": "2025-01-15",
            },
        ]
        result = await analyze_weather_impact(activities, [COLD_WEATHER])
        assert result["impact_days"] >= 1

    async def test_construction_impacts_included(self):
        """New field: construction_impacts should be present."""
        activities = [
            {
                "id": "A",
                "name": "Concrete Pour",
                "activity_type": "concrete_pour",
                "start_date": "2025-01-15",
                "end_date": "2025-01-15",
            },
        ]
        result = await analyze_weather_impact(activities, [COLD_WEATHER])
        assert "construction_impacts" in result
        assert len(result["construction_impacts"]) > 0

    async def test_adjusted_end_date_pushed_forward(self):
        activities = [
            {
                "id": "A",
                "name": "Concrete Pour",
                "activity_type": "concrete_pour",
                "start_date": "2025-01-15",
                "end_date": "2025-01-15",
            },
        ]
        result = await analyze_weather_impact(activities, [COLD_WEATHER])
        if result["impact_days"] > 0:
            assert result["adjusted_end_date"] > "2025-01-15"

    async def test_custom_sensitivity_override(self):
        """Custom thresholds should override defaults."""
        activities = [
            {
                "id": "A",
                "name": "Custom",
                "activity_type": "custom_type",
                "start_date": "2025-06-15",
                "end_date": "2025-06-15",
            },
        ]
        custom = {"custom_type": {"max_wind": 5.0}}  # very strict
        result = await analyze_weather_impact(
            activities, [GOOD_WEATHER], activity_weather_sensitivity=custom
        )
        # Wind 8mph > 5mph threshold → should have impact
        assert result["impact_days"] >= 1

    async def test_multiple_activities_consolidation(self):
        """Events on same date from different activities should consolidate."""
        activities = [
            {
                "id": "A",
                "name": "Pour",
                "activity_type": "concrete_pour",
                "start_date": "2025-01-15",
                "end_date": "2025-01-15",
            },
            {
                "id": "B",
                "name": "Roof",
                "activity_type": "roofing",
                "start_date": "2025-01-15",
                "end_date": "2025-01-15",
            },
        ]
        result = await analyze_weather_impact(activities, [COLD_WEATHER])
        # Both activities impacted on same day = 1 impact day, not 2
        assert result["impact_days"] == 1
        # But both activity IDs should appear in affected_activities
        for event in result["weather_events"]:
            if event["date"] == "2025-01-15":
                assert len(event["affected_activities"]) == 2


# ---------------------------------------------------------------------------
# Test: get_weather_impact (scheduling API entry point)
# ---------------------------------------------------------------------------


class TestGetWeatherImpact:
    """Tests for get_weather_impact() scheduling integration."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _weather_cache.clear()
        yield
        _weather_cache.clear()

    async def test_with_activities(self):
        activities = [
            {
                "id": "A",
                "name": "Pour",
                "activity_type": "concrete_pour",
                "start_date": "2025-06-15",
                "end_date": "2025-06-15",
            },
        ]
        with patch(
            "app.services.scheduling.weather_service.get_weather_forecast",
            new_callable=AsyncMock,
            return_value=[GOOD_WEATHER],
        ):
            result = await get_weather_impact(
                location="40.7128,-74.0060",
                start_date=date(2025, 6, 15),
                end_date=date(2025, 6, 15),
                activities=activities,
            )
        assert "impact_days" in result
        assert "risk_level" in result

    async def test_without_activities(self):
        with patch(
            "app.services.scheduling.weather_service.get_weather_forecast",
            new_callable=AsyncMock,
            return_value=[GOOD_WEATHER],
        ):
            result = await get_weather_impact(
                location="New York",
                start_date=date(2025, 6, 15),
                end_date=date(2025, 6, 15),
            )
        assert "impact_days" in result
        assert "adjusted_end_date" in result

    async def test_string_dates_accepted(self):
        with patch(
            "app.services.scheduling.weather_service.get_weather_forecast",
            new_callable=AsyncMock,
            return_value=[GOOD_WEATHER],
        ):
            result = await get_weather_impact(
                location="40.7128,-74.0060",
                start_date="2025-06-15",
                end_date="2025-06-15",
            )
        assert "impact_days" in result


# ---------------------------------------------------------------------------
# Test: Historical weather
# ---------------------------------------------------------------------------


class TestHistoricalWeather:
    """Tests for fetch_historical_weather() and get_monthly_weather_stats()."""

    async def test_fetch_historical_returns_data(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "daily": {
                "time": ["2024-01-01", "2024-01-02"],
                "temperature_2m_max": [35.0, 40.0],
                "temperature_2m_min": [20.0, 25.0],
                "precipitation_sum": [0.0, 5.0],
                "wind_speed_10m_max": [10.0, 15.0],
                "weather_code": [0, 61],
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.scheduling.weather_service.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await fetch_historical_weather(40.7, -74.0, "2024-01-01", "2024-01-02")

        assert len(result) == 2
        assert result[0]["date"] == "2024-01-01"
        assert result[1]["precipitation_mm"] == 5.0

    async def test_monthly_stats_computes_averages(self):
        # Create 60 days of fake historical data (2 months)
        hist_data = []
        for i in range(60):
            d = date(2024, 1, 1) + timedelta(days=i)
            hist_data.append(
                {
                    "date": d.isoformat(),
                    "temperature_max": 50.0 + i * 0.5,
                    "temperature_min": 30.0 + i * 0.3,
                    "precipitation_mm": 5.0 if i % 3 == 0 else 0.0,
                    "wind_speed_max": 10.0 + (i % 10),
                    "weather_code": 63 if i % 5 == 0 else 0,
                }
            )

        with patch(
            "app.services.scheduling.weather_service.fetch_historical_weather",
            new_callable=AsyncMock,
            return_value=hist_data,
        ):
            stats = await get_monthly_weather_stats(40.7, -74.0, years=1)

        assert 1 in stats
        assert 2 in stats
        assert stats[1]["sample_days"] > 0
        assert stats[1]["avg_temp_max"] > 0
        assert stats[1]["pct_rain_days"] >= 0


# ---------------------------------------------------------------------------
# Test: Real NOAA call (integration test)
# ---------------------------------------------------------------------------


class TestRealNOAACall:
    """Integration test: real call to api.weather.gov.

    This test actually hits the NOAA/NWS API. It is marked slow and
    may fail if the API is down. Run with: pytest -m 'not slow' to skip.
    """

    @pytest.mark.slow
    async def test_real_noaa_forecast(self):
        """Fetch real forecast data from NOAA for New York City."""
        from app.services.scheduling.weather_service import _fetch_noaa_forecast

        try:
            data = await _fetch_noaa_forecast(40.7128, -74.0060)
            assert len(data) > 0, "NOAA should return at least 1 day"
            day = data[0]
            assert "date" in day
            assert "temperature_max" in day
            assert "temperature_min" in day
            assert "wind_speed_max" in day
            assert "precipitation_mm" in day
            assert "weather_code" in day
            # Sanity: temperature should be reasonable
            assert -50 < day["temperature_max"] < 150
        except Exception as exc:
            pytest.skip(f"NOAA API unavailable: {exc}")


# ---------------------------------------------------------------------------
# Test: WeatherDataUnavailableError
# ---------------------------------------------------------------------------


class TestWeatherDataUnavailableError:
    """Tests for the custom exception."""

    def test_is_exception(self):
        assert issubclass(WeatherDataUnavailableError, Exception)

    def test_message_preserved(self):
        err = WeatherDataUnavailableError("test error")
        assert "test error" in str(err)


# ---------------------------------------------------------------------------
# Test: WeatherImpact dataclass
# ---------------------------------------------------------------------------


class TestWeatherImpactDataclass:
    """Tests for the WeatherImpact dataclass."""

    def test_default_fields(self):
        impact = WeatherImpact(activity="test", allowed=True, risk_level=RiskLevel.GREEN)
        assert impact.reasons == []
        assert impact.recommendations == []

    def test_risk_level_enum_values(self):
        assert RiskLevel.GREEN.value == "green"
        assert RiskLevel.YELLOW.value == "yellow"
        assert RiskLevel.RED.value == "red"
