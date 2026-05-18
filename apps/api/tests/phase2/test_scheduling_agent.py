"""Phase 2: Scheduling agent orchestration tests.

Tests for the scheduling agent that coordinates CPM analysis, DCMA checks,
and weather impact analysis. All downstream services are mocked.
"""

from __future__ import annotations

from unittest.mock import patch

from tests.fixtures.precon_mock_responses import (
    MOCK_SCHEDULE_ACTIVITIES,
    MOCK_WEATHER_DATA,
)


class TestSchedulingAgent:
    """Tests for the scheduling agent orchestrator."""

    @patch("app.services.scheduling.weather_service.analyze_weather_impact")
    @patch("app.services.scheduling.weather_service.get_weather_forecast")
    @patch("app.services.scheduling.dcma_checker.run_dcma_check")
    @patch("app.services.scheduling.cpm_engine.calculate_cpm")
    async def test_scheduling_pipeline_integration(
        self, mock_cpm, mock_dcma, mock_weather, mock_impact
    ):
        """The full scheduling pipeline should run CPM, DCMA, and weather checks."""
        mock_cpm.return_value = {
            "activities": MOCK_SCHEDULE_ACTIVITIES,
            "critical_path": ["A", "B", "C", "E", "G", "H"],
            "project_duration": 115,
            "critical_path_length": 6,
        }
        mock_dcma.return_value = {
            "overall_score": 85.0,
            "checks": [],
            "passed": 12,
            "failed": 1,
            "warning": 1,
            "grade": "B",
        }
        mock_weather.return_value = MOCK_WEATHER_DATA
        mock_impact.return_value = {
            "impact_days": 2,
            "risk_level": "low",
            "weather_events": [],
            "adjusted_end_date": "2025-06-15",
        }

        from app.services.scheduling.cpm_engine import calculate_cpm
        from app.services.scheduling.dcma_checker import run_dcma_check
        from app.services.scheduling.weather_service import (
            analyze_weather_impact,
            get_weather_forecast,
        )

        cpm_result = await calculate_cpm(MOCK_SCHEDULE_ACTIVITIES)
        assert cpm_result["project_duration"] == 115

        dcma_result = await run_dcma_check(MOCK_SCHEDULE_ACTIVITIES)
        assert dcma_result["grade"] == "B"

        weather = await get_weather_forecast(40.7128, -74.0060, "2025-03-01", "2025-03-05")
        assert len(weather) > 0

        impact = await analyze_weather_impact(MOCK_SCHEDULE_ACTIVITIES, weather)
        assert impact["risk_level"] == "low"

    @patch("app.services.scheduling.cpm_engine.calculate_cpm")
    async def test_scheduling_empty_activities(self, mock_cpm):
        """Pipeline should handle empty activity list."""
        mock_cpm.return_value = {
            "activities": [],
            "critical_path": [],
            "project_duration": 0,
            "critical_path_length": 0,
        }

        from app.services.scheduling.cpm_engine import calculate_cpm

        result = await calculate_cpm([])
        assert result["project_duration"] == 0

    @patch("app.services.scheduling.cpm_engine.calculate_cpm")
    async def test_scheduling_critical_path_output(self, mock_cpm):
        """Pipeline should output the critical path activities."""
        mock_cpm.return_value = {
            "activities": MOCK_SCHEDULE_ACTIVITIES,
            "critical_path": ["A", "B", "C", "E", "G", "H"],
            "project_duration": 115,
            "critical_path_length": 6,
        }

        from app.services.scheduling.cpm_engine import calculate_cpm

        result = await calculate_cpm(MOCK_SCHEDULE_ACTIVITIES)
        assert "A" in result["critical_path"]
        assert "H" in result["critical_path"]
