"""Tests for the Critical Path Method (CPM) engine.

Pins the scheduling math: forward / backward pass, FS/SS/FF/SF
relationships, lag, free float, near-critical paths, the work-calendar
arithmetic, and cycle detection. All pure compute — no DB.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.scheduling.cpm_engine import (
    DEFAULT_CALENDAR,
    WorkCalendar,
    calculate_cpm,
    calculate_free_float,
    find_near_critical_paths,
)

# =========================================================================
# WorkCalendar
# =========================================================================


def test_default_calendar_is_mon_fri():
    """Pin the default — Monday through Friday, no holidays."""
    assert DEFAULT_CALENDAR.work_days == {0, 1, 2, 3, 4}
    assert DEFAULT_CALENDAR.holidays == set()


def test_calendar_is_work_day_weekday_and_holiday_logic():
    cal = WorkCalendar(work_days=[0, 1, 2, 3, 4], holidays={"2026-07-04"})
    assert cal.is_work_day(date(2026, 4, 27))  # Mon
    assert cal.is_work_day(date(2026, 5, 1))  # Fri
    assert not cal.is_work_day(date(2026, 5, 2))  # Sat
    assert not cal.is_work_day(date(2026, 5, 3))  # Sun
    assert not cal.is_work_day(date(2026, 7, 4))  # Holiday (Sat anyway)


def test_calendar_add_work_days_skips_weekends():
    cal = WorkCalendar(work_days=[0, 1, 2, 3, 4])
    # Mon Apr 27 + 5 work days = Mon May 4 (skips Sat/Sun)
    out = cal.add_work_days(date(2026, 4, 27), 5)
    assert out == date(2026, 5, 4)


def test_calendar_add_work_days_skips_holiday():
    cal = WorkCalendar(work_days=[0, 1, 2, 3, 4], holidays={"2026-04-29"})
    # Mon + 3 days, Wed is a holiday → skips Wed.
    out = cal.add_work_days(date(2026, 4, 27), 3)
    # Tue, Thu, Fri = 3 work days → Fri May 1
    assert out == date(2026, 5, 1)


def test_calendar_add_work_days_zero_or_negative_returns_start():
    cal = WorkCalendar(work_days=[0, 1, 2, 3, 4])
    assert cal.add_work_days(date(2026, 4, 27), 0) == date(2026, 4, 27)
    assert cal.add_work_days(date(2026, 4, 27), -5) == date(2026, 4, 27)


def test_calendar_add_work_days_no_work_days_raises():
    cal = WorkCalendar(work_days=[])
    with pytest.raises(ValueError, match="no work days"):
        cal.add_work_days(date(2026, 4, 27), 5)


def test_calendar_add_work_days_runaway_blocked():
    """Pathological case: every day is a holiday — add_work_days must
    abort before infinite-looping."""
    holidays = {
        (date(2026, 4, 27) + __import__("datetime").timedelta(days=i)).isoformat()
        for i in range(15_000)
    }
    cal = WorkCalendar(work_days=[0, 1, 2, 3, 4], holidays=holidays)
    with pytest.raises(ValueError, match="exceeded"):
        cal.add_work_days(date(2026, 4, 27), 5)


def test_calendar_work_days_between_counts_inclusive_of_end():
    cal = WorkCalendar(work_days=[0, 1, 2, 3, 4])
    # Mon Apr 27 → Mon May 4 spans 5 work days (Tue, Wed, Thu, Fri, Mon).
    n = cal.work_days_between(date(2026, 4, 27), date(2026, 5, 4))
    assert n == 5


def test_calendar_work_days_between_no_work_days_raises():
    cal = WorkCalendar(work_days=[])
    with pytest.raises(ValueError, match="no work days"):
        cal.work_days_between(date(2026, 4, 27), date(2026, 5, 1))


# =========================================================================
# calculate_cpm — empty input
# =========================================================================


async def test_cpm_empty_returns_zero_duration():
    out = await calculate_cpm([])
    assert out == {
        "activities": [],
        "critical_path": [],
        "project_duration": 0,
        "critical_path_length": 0,
    }


# =========================================================================
# calculate_cpm — simple FS chain
# =========================================================================


async def test_cpm_simple_chain_critical_path_correct():
    """Chain A(2) → B(3) → C(5) → critical path is A,B,C, duration 10."""
    activities = [
        {"id": "A", "name": "A", "duration_days": 2, "predecessors": []},
        {"id": "B", "name": "B", "duration_days": 3, "predecessors": ["A"]},
        {"id": "C", "name": "C", "duration_days": 5, "predecessors": ["B"]},
    ]
    out = await calculate_cpm(activities)
    assert out["project_duration"] == 10
    assert out["critical_path"] == ["A", "B", "C"]
    assert out["critical_path_length"] == 3
    by_id = {a["id"]: a for a in out["activities"]}
    assert by_id["A"]["early_start"] == 0
    assert by_id["A"]["early_finish"] == 2
    assert by_id["B"]["early_start"] == 2
    assert by_id["B"]["early_finish"] == 5
    assert by_id["C"]["early_finish"] == 10
    assert all(a["total_float"] == 0 for a in out["activities"])


async def test_cpm_parallel_paths_only_longest_is_critical():
    """A → B(3), A → C(7), B → D, C → D : C is on critical path, B has float."""
    activities = [
        {"id": "A", "name": "A", "duration_days": 1},
        {"id": "B", "name": "B", "duration_days": 3, "predecessors": ["A"]},
        {"id": "C", "name": "C", "duration_days": 7, "predecessors": ["A"]},
        {"id": "D", "name": "D", "duration_days": 2, "predecessors": ["B", "C"]},
    ]
    out = await calculate_cpm(activities)
    assert out["project_duration"] == 1 + 7 + 2  # A + C + D = 10
    assert "C" in out["critical_path"]
    assert "B" not in out["critical_path"]
    by_id = {a["id"]: a for a in out["activities"]}
    assert by_id["B"]["total_float"] == 4  # 7 - 3


# =========================================================================
# calculate_cpm — relationship types
# =========================================================================


async def test_cpm_finish_to_start_with_lag():
    """B starts 5 days after A finishes (FS, lag=5)."""
    activities = [
        {"id": "A", "name": "A", "duration_days": 4},
        {
            "id": "B",
            "name": "B",
            "duration_days": 3,
            "relationships": [{"predecessor_id": "A", "type": "FS", "lag": 5}],
        },
    ]
    out = await calculate_cpm(activities)
    by_id = {a["id"]: a for a in out["activities"]}
    assert by_id["B"]["early_start"] == 9  # 4 (A finish) + 5 lag
    assert by_id["B"]["early_finish"] == 12


async def test_cpm_start_to_start():
    """B can start as soon as A starts (SS, lag=0) — ES[B] = ES[A]."""
    activities = [
        {"id": "A", "name": "A", "duration_days": 10},
        {
            "id": "B",
            "name": "B",
            "duration_days": 3,
            "relationships": [{"predecessor_id": "A", "type": "SS", "lag": 0}],
        },
    ]
    out = await calculate_cpm(activities)
    by_id = {a["id"]: a for a in out["activities"]}
    assert by_id["B"]["early_start"] == 0
    # Project duration = max of EF — A finishes at 10, B at 3.
    assert out["project_duration"] == 10


async def test_cpm_finish_to_finish_with_lag():
    """B must finish at least 2 days after A finishes (FF, lag=2)."""
    activities = [
        {"id": "A", "name": "A", "duration_days": 5},
        {
            "id": "B",
            "name": "B",
            "duration_days": 3,
            "relationships": [{"predecessor_id": "A", "type": "FF", "lag": 2}],
        },
    ]
    out = await calculate_cpm(activities)
    by_id = {a["id"]: a for a in out["activities"]}
    assert by_id["B"]["early_finish"] == 7  # A finish 5 + lag 2 = 7
    assert by_id["B"]["early_start"] == 4  # 7 - 3


async def test_cpm_invalid_relationship_type_rejected():
    activities = [
        {"id": "A", "name": "A", "duration_days": 1},
        {
            "id": "B",
            "name": "B",
            "duration_days": 1,
            "relationships": [{"predecessor_id": "A", "type": "BAD"}],
        },
    ]
    with pytest.raises(ValueError, match="unsupported relationship type"):
        await calculate_cpm(activities)


# =========================================================================
# calculate_cpm — cycle detection
# =========================================================================


async def test_cpm_cycle_detected():
    """A → B → A is a cycle — must raise, not silently truncate."""
    activities = [
        {"id": "A", "name": "A", "duration_days": 1, "predecessors": ["B"]},
        {"id": "B", "name": "B", "duration_days": 1, "predecessors": ["A"]},
    ]
    with pytest.raises(ValueError, match="dependency cycle"):
        await calculate_cpm(activities)


# =========================================================================
# calculate_cpm — calendar / start-date conversion
# =========================================================================


async def test_cpm_with_project_start_emits_iso_dates():
    activities = [
        {"id": "A", "name": "A", "duration_days": 2},
        {"id": "B", "name": "B", "duration_days": 3, "predecessors": ["A"]},
    ]
    # Mon April 27 2026 — clean weekday start.
    out = await calculate_cpm(activities, project_start=date(2026, 4, 27))
    by_id = {a["id"]: a for a in out["activities"]}
    assert by_id["A"]["start_date"] == "2026-04-27"
    # A is 2 work days → finish Wed Apr 29
    assert by_id["A"]["finish_date"] == "2026-04-29"


async def test_cpm_calendar_id_routing():
    """Activities with a custom calendar_id should use that calendar's
    holidays for date conversion."""
    custom = WorkCalendar(work_days=[0, 1, 2, 3, 4], holidays={"2026-04-28"})
    activities = [
        {
            "id": "A",
            "name": "A",
            "duration_days": 2,
            "calendar_id": "custom",
        },
    ]
    out = await calculate_cpm(
        activities,
        calendars={"custom": custom},
        project_start=date(2026, 4, 27),
    )
    # Mon start, +2 work days but Apr 28 is a holiday → Tue Apr 28 skipped,
    # Wed Apr 29 (1), Thu Apr 30 (2). Finish Apr 30.
    by_id = {a["id"]: a for a in out["activities"]}
    assert by_id["A"]["finish_date"] == "2026-04-30"


# =========================================================================
# calculate_free_float
# =========================================================================


async def test_free_float_for_parallel_paths():
    """A → B(3), A → C(7), B → D, C → D : B has free float = 4."""
    activities = [
        {"id": "A", "name": "A", "duration_days": 1},
        {"id": "B", "name": "B", "duration_days": 3, "predecessors": ["A"]},
        {"id": "C", "name": "C", "duration_days": 7, "predecessors": ["A"]},
        {"id": "D", "name": "D", "duration_days": 2, "predecessors": ["B", "C"]},
    ]
    out = await calculate_free_float(activities)
    by_id = {a["id"]: a for a in out}
    # B finishes at 4, D starts at 8 → free float = 8 - 4 = 4.
    assert by_id["B"]["free_float"] == 4
    # Critical path activities have free float = 0.
    assert by_id["C"]["free_float"] == 0
    # D has no successors → free float = total float = 0 (it's critical).
    assert by_id["D"]["free_float"] == 0


# =========================================================================
# find_near_critical_paths
# =========================================================================


async def test_find_near_critical_path_excludes_critical():
    """The true critical path (float=0) should NOT appear in the
    near-critical results — only paths with 0 < float ≤ threshold."""
    activities = [
        {"id": "A", "name": "A", "duration_days": 1},
        {"id": "B", "name": "B", "duration_days": 3, "predecessors": ["A"]},
        {"id": "C", "name": "C", "duration_days": 4, "predecessors": ["A"]},
        {"id": "D", "name": "D", "duration_days": 2, "predecessors": ["B", "C"]},
    ]
    paths = await find_near_critical_paths(activities, threshold_days=2)
    flat = [aid for path in paths for aid in path]
    # B has float=1 → near-critical; C has float=0 → critical (excluded).
    assert "B" in flat
    assert "C" not in flat


async def test_find_near_critical_no_paths_when_all_critical():
    """Linear chain — every activity is critical → empty list."""
    activities = [
        {"id": "A", "name": "A", "duration_days": 1},
        {"id": "B", "name": "B", "duration_days": 1, "predecessors": ["A"]},
        {"id": "C", "name": "C", "duration_days": 1, "predecessors": ["B"]},
    ]
    paths = await find_near_critical_paths(activities, threshold_days=5)
    assert paths == []
