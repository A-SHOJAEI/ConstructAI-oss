"""Phase 2: Weather service and impact analysis tests.

Tests for weather data fetching (multi-provider) and activity impact
analysis against weather thresholds.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.services.scheduling.weather_service import (
    analyze_weather_impact,
    get_weather_forecast,
)

# Use inline weather data instead of removed mock fixtures
SAMPLE_WEATHER_DATA = [
    {
        "date": "2025-03-01",
        "temperature_max": 55,
        "temperature_min": 35,
        "precipitation_mm": 0,
        "wind_speed_max": 15,
        "weather_code": 0,
    },
    {
        "date": "2025-03-02",
        "temperature_max": 48,
        "temperature_min": 30,
        "precipitation_mm": 12,
        "wind_speed_max": 25,
        "weather_code": 61,
    },
    {
        "date": "2025-03-03",
        "temperature_max": 52,
        "temperature_min": 33,
        "precipitation_mm": 0,
        "wind_speed_max": 10,
        "weather_code": 0,
    },
    {
        "date": "2025-03-04",
        "temperature_max": 25,
        "temperature_min": 15,
        "precipitation_mm": 5,
        "wind_speed_max": 35,
        "weather_code": 65,
    },
    {
        "date": "2025-03-05",
        "temperature_max": 60,
        "temperature_min": 42,
        "precipitation_mm": 0,
        "wind_speed_max": 12,
        "weather_code": 0,
    },
]


class TestWeatherService:
    """Tests for the weather service."""

    async def test_get_weather_forecast_returns_data(self):
        """Forecast should return daily weather data via providers."""
        noaa_data = SAMPLE_WEATHER_DATA
        with patch(
            "app.services.scheduling.weather_service._fetch_noaa_forecast",
            new_callable=AsyncMock,
            return_value=noaa_data,
        ):
            result = await get_weather_forecast(40.7128, -74.0060, "2025-03-01", "2025-03-05")
        assert len(result) > 0
        assert all("temperature_max" in d for d in result)
        assert all("precipitation_mm" in d for d in result)
        assert all("wind_speed_max" in d for d in result)

    async def test_get_weather_forecast_day_count(self):
        """Forecast should return one entry per day in the range."""
        noaa_data = SAMPLE_WEATHER_DATA
        with patch(
            "app.services.scheduling.weather_service._fetch_noaa_forecast",
            new_callable=AsyncMock,
            return_value=noaa_data,
        ):
            result = await get_weather_forecast(40.7128, -74.0060, "2025-03-01", "2025-03-05")
        assert len(result) == 5

    async def test_analyze_weather_impact(self):
        """Impact analysis should identify risk level and impact days."""
        activities = [
            {
                "id": "A",
                "name": "Concrete Pour",
                "activity_type": "concrete_pour",
                "start_date": "2025-03-01",
                "end_date": "2025-03-05",
            },
        ]
        result = await analyze_weather_impact(activities, SAMPLE_WEATHER_DATA)
        assert "impact_days" in result
        assert "risk_level" in result
        assert result["risk_level"] in ("low", "medium", "high")

    async def test_weather_impact_cold_day(self):
        """Day with temp=25F should impact concrete pours (min_temp=40F)."""
        cold_weather = [
            {
                "date": "2025-03-04",
                "temperature_max": 25,
                "temperature_min": 15,
                "precipitation_mm": 0,
                "wind_speed_max": 10,
                "weather_code": 0,
            },
        ]
        activities = [
            {
                "id": "A",
                "name": "Concrete Pour",
                "activity_type": "concrete_pour",
                "start_date": "2025-03-04",
                "end_date": "2025-03-04",
            },
        ]
        result = await analyze_weather_impact(activities, cold_weather)
        assert result["impact_days"] >= 1

    async def test_weather_impact_no_activities(self):
        """No activities should yield zero impact days."""
        result = await analyze_weather_impact([], SAMPLE_WEATHER_DATA)
        assert result["impact_days"] == 0
        assert result["risk_level"] == "low"

    async def test_weather_impact_adjusted_end_date(self):
        """Impact days should push the adjusted end date forward."""
        activities = [
            {
                "id": "A",
                "name": "Concrete Pour",
                "activity_type": "concrete_pour",
                "start_date": "2025-03-01",
                "end_date": "2025-03-05",
            },
        ]
        result = await analyze_weather_impact(activities, SAMPLE_WEATHER_DATA)
        assert "adjusted_end_date" in result
        if result["impact_days"] > 0:
            assert result["adjusted_end_date"] > "2025-03-05"
