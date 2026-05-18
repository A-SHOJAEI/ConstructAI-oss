"""Tests for the resource conflict detection and leveling module.

Covers conflict detection, leveling correctness, multi-resource scenarios,
no-conflict pass-through, empty input, single activity, and the max
iterations safety guard.
"""

from __future__ import annotations

from app.services.scheduling.resource_leveler import (
    check_resource_conflicts,
    level_resources,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_overlapping_activities() -> list[dict]:
    """Two activities that overlap on day 0-4, both needing crane=2."""
    return [
        {
            "id": "A",
            "name": "Crane Work A",
            "early_start": 0,
            "early_finish": 5,
            "duration_days": 5,
            "total_float": 0,
            "resources": {"crane": 2},
        },
        {
            "id": "B",
            "name": "Crane Work B",
            "early_start": 2,
            "early_finish": 6,
            "duration_days": 4,
            "total_float": 5,
            "resources": {"crane": 2},
        },
    ]


# ========================================================================
# Tests
# ========================================================================


class TestCheckResourceConflicts:
    """Tests for the check_resource_conflicts function."""

    def test_detects_single_resource_conflict(self):
        activities = _make_overlapping_activities()
        max_resources = {"crane": 3}

        conflicts = check_resource_conflicts(activities, max_resources)

        assert len(conflicts) >= 1
        c = conflicts[0]
        assert c.resource_type == "crane"
        assert c.demand > 3  # 2+2=4 > 3
        assert "A" in c.conflicting_activity_ids
        assert "B" in c.conflicting_activity_ids

    def test_no_conflict_when_capacity_sufficient(self):
        activities = _make_overlapping_activities()
        max_resources = {"crane": 4}  # 2+2=4 <= 4

        conflicts = check_resource_conflicts(activities, max_resources)
        assert len(conflicts) == 0

    def test_multi_resource_conflict(self):
        activities = [
            {
                "id": "A",
                "early_start": 0,
                "duration_days": 3,
                "resources": {"crane": 1, "labor": 10},
            },
            {
                "id": "B",
                "early_start": 1,
                "duration_days": 3,
                "resources": {"crane": 1, "labor": 8},
            },
        ]
        max_resources = {"crane": 2, "labor": 12}

        conflicts = check_resource_conflicts(activities, max_resources)

        # Crane: 1+1=2 <= 2 (OK), Labor: 10+8=18 > 12 (conflict)
        assert len(conflicts) == 1
        assert conflicts[0].resource_type == "labor"

    def test_empty_activities(self):
        conflicts = check_resource_conflicts([], {"crane": 2})
        assert len(conflicts) == 0

    def test_no_max_resources(self):
        activities = _make_overlapping_activities()
        conflicts = check_resource_conflicts(activities, None)
        assert len(conflicts) == 0

    def test_non_overlapping_activities(self):
        activities = [
            {
                "id": "A",
                "early_start": 0,
                "duration_days": 5,
                "resources": {"crane": 3},
            },
            {
                "id": "B",
                "early_start": 10,
                "duration_days": 5,
                "resources": {"crane": 3},
            },
        ]
        max_resources = {"crane": 3}

        conflicts = check_resource_conflicts(activities, max_resources)
        assert len(conflicts) == 0


class TestLevelResources:
    """Tests for the level_resources function."""

    def test_leveling_delays_non_critical(self):
        activities = [
            {
                "id": "A",
                "early_start": 0,
                "early_finish": 5,
                "duration_days": 5,
                "total_float": 0,
                "resources": {"labor": 10},
            },
            {
                "id": "B",
                "early_start": 0,
                "early_finish": 4,
                "duration_days": 4,
                "total_float": 8,
                "resources": {"labor": 8},
            },
        ]
        max_resources = {"labor": 12}

        leveled = level_resources(activities, max_resources)
        idx = {str(a["id"]): a for a in leveled}

        # A is critical (float=0), should stay at 0
        assert idx["A"]["early_start"] == 0

        # B has float, should be delayed to avoid overlap
        assert idx["B"]["early_start"] > 0

        # Verify no conflicts remain
        conflicts = check_resource_conflicts(leveled, max_resources)
        assert len(conflicts) == 0

    def test_leveling_single_activity(self):
        activities = [
            {
                "id": "A",
                "early_start": 0,
                "early_finish": 5,
                "duration_days": 5,
                "total_float": 0,
                "resources": {"crane": 1},
            }
        ]
        max_resources = {"crane": 1}

        leveled = level_resources(activities, max_resources)
        assert len(leveled) == 1
        assert leveled[0]["early_start"] == 0

    def test_leveling_empty_input(self):
        leveled = level_resources([], {"crane": 2})
        assert leveled == []

    def test_leveling_no_resources(self):
        activities = [
            {
                "id": "A",
                "early_start": 0,
                "early_finish": 5,
                "duration_days": 5,
                "total_float": 0,
                "resources": {},
            }
        ]
        max_resources = {"crane": 1}

        leveled = level_resources(activities, max_resources)
        assert len(leveled) == 1
        assert leveled[0]["early_start"] == 0

    def test_leveling_preserves_all_activities(self):
        """Leveling should not add or remove activities."""
        activities = _make_overlapping_activities()
        max_resources = {"crane": 2}

        leveled = level_resources(activities, max_resources)
        assert len(leveled) == len(activities)

        original_ids = {str(a["id"]) for a in activities}
        leveled_ids = {str(a["id"]) for a in leveled}
        assert original_ids == leveled_ids

    def test_max_iterations_guard(self):
        """When all conflicting activities have float=0, leveling should stop."""
        activities = [
            {
                "id": "A",
                "early_start": 0,
                "early_finish": 5,
                "duration_days": 5,
                "total_float": 0,
                "resources": {"crane": 2},
            },
            {
                "id": "B",
                "early_start": 0,
                "early_finish": 5,
                "duration_days": 5,
                "total_float": 0,
                "resources": {"crane": 2},
            },
        ]
        max_resources = {"crane": 3}

        # Both activities have float=0, so leveling cannot delay either.
        # Should terminate quickly (no infinite loop).
        leveled = level_resources(activities, max_resources)
        assert len(leveled) == 2

    def test_conflict_period_structure(self):
        """ResourceConflict should have correct start/end period."""
        activities = [
            {
                "id": "A",
                "early_start": 3,
                "duration_days": 4,
                "resources": {"crane": 2},
            },
            {
                "id": "B",
                "early_start": 5,
                "duration_days": 4,
                "resources": {"crane": 2},
            },
        ]
        max_resources = {"crane": 3}

        conflicts = check_resource_conflicts(activities, max_resources)

        assert len(conflicts) >= 1
        c = conflicts[0]
        # Overlap is days 5, 6 (A: 3-6, B: 5-8)
        assert c.period_start == 5
        assert c.period_end == 6
        assert c.demand == 4
        assert c.capacity == 3
