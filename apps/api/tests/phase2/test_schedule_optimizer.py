"""Phase 2: Schedule optimizer tests.

Tests for what-if analysis and resource leveling functionality.
These are TDD stubs — the underlying functions raise NotImplementedError
until Phase 2 implementation is complete.
"""

from __future__ import annotations

import pytest

from app.services.scheduling.schedule_optimizer import level_resources, what_if_analysis
from tests.fixtures.precon_mock_responses import MOCK_SCHEDULE_ACTIVITIES


@pytest.mark.xfail(reason="Phase 2 stubs — not yet implemented", raises=NotImplementedError)
class TestScheduleOptimizer:
    """Tests for the schedule optimization service."""

    async def test_what_if_analysis(self):
        """Increasing a critical activity duration should increase project duration."""
        changes = [{"activity_id": "C", "field": "duration_days", "new_value": 40}]
        result = await what_if_analysis(MOCK_SCHEDULE_ACTIVITIES, changes)
        assert "original_duration" in result
        assert "new_duration" in result
        assert result["original_duration"] == 115
        assert result["new_duration"] > result["original_duration"]

    async def test_what_if_analysis_non_critical(self):
        """Increasing a non-critical activity within float should not change duration."""
        # Activity D has float; small increase should not affect project duration
        changes = [{"activity_id": "D", "field": "duration_days", "new_value": 26}]
        result = await what_if_analysis(MOCK_SCHEDULE_ACTIVITIES, changes)
        assert result["original_duration"] == 115
        # D only has 5 days of float, adding 1 day should still be within float
        assert result["new_duration"] == 115

    async def test_what_if_analysis_duration_change(self):
        """Result should include duration_change field."""
        changes = [{"activity_id": "C", "field": "duration_days", "new_value": 40}]
        result = await what_if_analysis(MOCK_SCHEDULE_ACTIVITIES, changes)
        assert "duration_change" in result
        assert result["duration_change"] == result["new_duration"] - result["original_duration"]

    async def test_what_if_analysis_critical_path_change(self):
        """Large non-critical delay should potentially change the critical path."""
        changes = [{"activity_id": "D", "field": "duration_days", "new_value": 50}]
        result = await what_if_analysis(MOCK_SCHEDULE_ACTIVITIES, changes)
        assert "critical_path_changed" in result

    async def test_resource_leveling(self):
        """Resource leveling should return leveled duration and utilization."""
        activities = [
            {
                "id": "A",
                "name": "Task A",
                "duration_days": 5,
                "predecessors": [],
                "resources": {"crew": 3},
            },
            {
                "id": "B",
                "name": "Task B",
                "duration_days": 5,
                "predecessors": [],
                "resources": {"crew": 3},
            },
        ]
        result = await level_resources(activities, {"crew": 4})
        assert "leveled_duration" in result
        assert "original_duration" in result
        assert "resource_utilization" in result
        # With only 4 crew available and both tasks needing 3, tasks cannot overlap fully
        assert result["leveled_duration"] >= result["original_duration"]

    async def test_resource_leveling_delays(self):
        """Leveling should report applied delays."""
        activities = [
            {
                "id": "A",
                "name": "Task A",
                "duration_days": 5,
                "predecessors": [],
                "resources": {"crew": 3},
            },
            {
                "id": "B",
                "name": "Task B",
                "duration_days": 5,
                "predecessors": [],
                "resources": {"crew": 3},
            },
        ]
        result = await level_resources(activities, {"crew": 4})
        assert "delays_applied" in result
        assert isinstance(result["delays_applied"], list)
