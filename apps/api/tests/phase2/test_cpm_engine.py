"""Phase 2: Critical Path Method engine tests.

Tests for CPM scheduling calculations including forward/backward pass,
float analysis, and critical path identification.
"""

from __future__ import annotations

from app.services.scheduling.cpm_engine import (
    calculate_cpm,
    calculate_free_float,
    find_near_critical_paths,
)
from tests.fixtures.precon_mock_responses import MOCK_SCHEDULE_ACTIVITIES


class TestCPMEngine:
    """Tests for the CPM scheduling engine."""

    async def test_calculate_cpm_basic(self):
        """CPM should return critical path and project duration."""
        result = await calculate_cpm(MOCK_SCHEDULE_ACTIVITIES)
        assert "critical_path" in result
        assert "project_duration" in result
        assert result["project_duration"] > 0

    async def test_critical_path_is_longest(self):
        """Project duration should be 115 days (A->B->C->E->G->H)."""
        result = await calculate_cpm(MOCK_SCHEDULE_ACTIVITIES)
        # Critical path: A(10)->B(20)->C(30)->E(20)->G(25)->H(10) = 115 days
        assert result["project_duration"] == 115

    async def test_critical_activities_have_zero_float(self):
        """All critical activities should have zero total float."""
        result = await calculate_cpm(MOCK_SCHEDULE_ACTIVITIES)
        for activity in result["activities"]:
            if activity["is_critical"]:
                assert activity["total_float"] == 0

    async def test_non_critical_activities_have_positive_float(self):
        """Non-critical activities should have positive total float."""
        result = await calculate_cpm(MOCK_SCHEDULE_ACTIVITIES)
        non_critical = [a for a in result["activities"] if not a["is_critical"]]
        assert len(non_critical) > 0
        assert all(a["total_float"] > 0 for a in non_critical)

    async def test_single_activity(self):
        """Single activity should be the entire critical path."""
        result = await calculate_cpm(
            [
                {"id": "A", "name": "Only Task", "duration_days": 5, "predecessors": []},
            ]
        )
        assert result["project_duration"] == 5
        assert result["critical_path"] == ["A"]

    async def test_empty_activities(self):
        """Empty activity list should return zero duration."""
        result = await calculate_cpm([])
        assert result["project_duration"] == 0
        assert result["critical_path"] == []

    async def test_free_float_calculation(self):
        """Free float should be computed for each activity."""
        result = await calculate_free_float(MOCK_SCHEDULE_ACTIVITIES)
        assert len(result) == len(MOCK_SCHEDULE_ACTIVITIES)
        assert all("free_float" in a for a in result)
        # Critical activities should have free float of 0
        for a in result:
            if a["is_critical"]:
                assert a["free_float"] == 0

    async def test_near_critical_paths(self):
        """Should find paths with total float below threshold."""
        result = await find_near_critical_paths(MOCK_SCHEDULE_ACTIVITIES, threshold_days=15)
        assert isinstance(result, list)
        # Each path should be a list of activity IDs
        for path in result:
            assert isinstance(path, list)
            assert all(isinstance(aid, str) for aid in path)
