"""Phase 2: DCMA 14-Point schedule health assessment tests.

Tests for the DCMA schedule checker that evaluates schedule quality
against industry-standard metrics.
"""

from __future__ import annotations

from app.services.scheduling.dcma_checker import run_dcma_check
from tests.fixtures.precon_mock_responses import MOCK_SCHEDULE_ACTIVITIES


class TestDCMAChecker:
    """Tests for the DCMA 14-point schedule health checker."""

    async def test_dcma_check_returns_14_checks(self):
        """DCMA assessment should return exactly 14 checks."""
        result = await run_dcma_check(MOCK_SCHEDULE_ACTIVITIES)
        assert len(result["checks"]) == 14

    async def test_dcma_score_range(self):
        """Overall score should be between 0 and 100."""
        result = await run_dcma_check(MOCK_SCHEDULE_ACTIVITIES)
        assert 0 <= result["overall_score"] <= 100

    async def test_dcma_grade_assignment(self):
        """Grade should be one of A, B, C, D, F."""
        result = await run_dcma_check(MOCK_SCHEDULE_ACTIVITIES)
        assert result["grade"] in ("A", "B", "C", "D", "F")

    async def test_dcma_check_counts(self):
        """Passed + failed + warning + skipped should equal 14."""
        result = await run_dcma_check(MOCK_SCHEDULE_ACTIVITIES)
        total = result["passed"] + result["failed"] + result["warning"] + result.get("skipped", 0)
        assert total == 14

    async def test_dcma_check_structure(self):
        """Each check should have required fields."""
        result = await run_dcma_check(MOCK_SCHEDULE_ACTIVITIES)
        for check in result["checks"]:
            assert "check_number" in check
            assert "check_name" in check
            assert "status" in check
            assert check["status"] in ("pass", "fail", "warning", "insufficient_data")
            assert "value" in check
            assert "threshold" in check

    async def test_dcma_with_baseline(self):
        """DCMA should accept optional baseline data for CPLI and BEI."""
        baseline = {
            "planned_duration": 115,
            "actual_duration": 50,
            "remaining_duration": 70,
            "tasks_completed": 3,
            "tasks_planned_to_be_complete": 4,
        }
        result = await run_dcma_check(MOCK_SCHEDULE_ACTIVITIES, baseline=baseline)
        assert len(result["checks"]) == 14
        # CPLI check should use baseline data
        cpli_check = next(c for c in result["checks"] if "CPLI" in c["check_name"])
        assert cpli_check is not None
