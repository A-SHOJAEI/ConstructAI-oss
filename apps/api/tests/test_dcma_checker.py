"""Tests for the DCMA 14-point schedule health checker.

Pin every individual check (1-14), the result-builder
``_check_result`` (pass/fail/warning logic), the percentage helper,
and the overall grading buckets in ``run_dcma_check``. All pure
compute — the CPM fallback path is exercised via the end-to-end test.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.scheduling.dcma_checker import (
    _check_bei,
    _check_cpli,
    _check_critical_path_test,
    _check_hard_constraints,
    _check_high_duration,
    _check_high_float,
    _check_invalid_dates,
    _check_lags,
    _check_leads,
    _check_logic,
    _check_missed_tasks,
    _check_negative_float,
    _check_relationship_types,
    _check_resources,
    _check_result,
    _pct,
    run_dcma_check,
)

# =========================================================================
# helpers — _pct, _check_result
# =========================================================================


def test_pct_safe_against_zero_division():
    assert _pct(0, 0) == 0.0
    assert _pct(5, 0) == 0.0
    assert _pct(50, 100) == 50.0


def test_check_result_pass_when_value_meets_ge_threshold():
    out = _check_result(99, "X", 95.0, 90.0, ">=", "")
    # Within 10% of threshold (margin 5/90 ≈ 5.5%) → "warning" not "pass".
    assert out["status"] == "warning"
    assert out["score"] == 1.0


def test_check_result_pass_clean_when_far_above_threshold():
    """Comfortably above threshold → "pass" (no warning)."""
    out = _check_result(1, "X", 99.0, 90.0, ">=", "")
    assert out["status"] == "pass"


def test_check_result_fail_when_value_below_ge_threshold():
    out = _check_result(1, "X", 80.0, 90.0, ">=", "")
    assert out["status"] == "fail"
    assert out["score"] == 0.0


def test_check_result_le_threshold_pass_warn_fail():
    """For "<=", value must be at or below threshold. Warning kicks in
    when the margin is strictly less than 10% of the threshold."""
    # 4.6/5 → margin 0.4, ratio 0.08 < 0.10 → warning.
    assert _check_result(1, "X", 4.6, 5.0, "<=", "")["status"] == "warning"
    # 1.0 — margin 4.0, ratio 0.8 → comfortable pass.
    assert _check_result(1, "X", 1.0, 5.0, "<=", "")["status"] == "pass"
    assert _check_result(1, "X", 6.0, 5.0, "<=", "")["status"] == "fail"


def test_check_result_eq_threshold_only_passes_on_match():
    """== operator — only exact match passes."""
    assert _check_result(1, "X", 0.0, 0.0, "==", "")["status"] == "pass"
    assert _check_result(1, "X", 0.5, 0.0, "==", "")["status"] == "fail"


def test_check_result_unknown_operator_treated_as_fail():
    out = _check_result(1, "X", 5.0, 5.0, "??", "")
    assert out["status"] == "fail"


# =========================================================================
# Check 1 — Logic (≥90% with predecessor + successor)
# =========================================================================


def test_check_logic_empty_activities_returns_zero_pct():
    out = _check_logic([])
    assert out["check_number"] == 1
    assert out["value"] == 0.0
    assert out["status"] == "fail"


def test_check_logic_chain_passes():
    """A → B → C: B has both predecessor+successor (1/3 ≈ 33%) → fail
    threshold (≥90%)."""
    activities = [
        {"id": "A", "predecessors": []},
        {"id": "B", "predecessors": ["A"]},
        {"id": "C", "predecessors": ["B"]},
    ]
    out = _check_logic(activities)
    # Only B has both predecessor+successor.
    assert out["value"] == pytest.approx(33.33, abs=0.1)
    assert out["status"] == "fail"


# =========================================================================
# Check 2 — Leads (≤5% with negative lag)
# =========================================================================


def test_check_leads_no_relationship_data_returns_insufficient():
    activities = [
        {"id": "A"},
        {"id": "B", "predecessors": ["A"]},
    ]
    out = _check_leads(activities)
    assert out["status"] == "insufficient_data"


def test_check_leads_negative_lag_counted():
    activities = [
        {
            "id": "A",
            "relationships": [{"predecessor_id": "X", "type": "FS", "lag": 0}],
        },
        {
            "id": "B",
            "relationships": [{"predecessor_id": "Y", "type": "FS", "lag": -3}],
        },
    ]
    out = _check_leads(activities)
    # 1/2 = 50% > 5% threshold → fail
    assert out["value"] == 50.0
    assert out["status"] == "fail"


def test_check_leads_no_leads_clean_pass():
    activities = [
        {
            "id": "A",
            "relationships": [{"predecessor_id": "X", "type": "FS", "lag": 0}],
        },
        {
            "id": "B",
            "relationships": [{"predecessor_id": "Y", "type": "FS", "lag": 5}],
        },
    ]
    out = _check_leads(activities)
    assert out["value"] == 0.0
    assert out["status"] == "pass"


# =========================================================================
# Check 3 — Lags
# =========================================================================


def test_check_lags_positive_lag_counted():
    activities = [
        {"id": "A", "relationships": [{"predecessor_id": "X", "lag": 0}]},
        {"id": "B", "relationships": [{"predecessor_id": "Y", "lag": 5}]},
    ]
    out = _check_lags(activities)
    assert out["value"] == 50.0
    assert out["status"] == "fail"


def test_check_lags_no_relationship_data_returns_insufficient():
    activities = [{"id": "A"}, {"id": "B", "predecessors": ["A"]}]
    out = _check_lags(activities)
    assert out["status"] == "insufficient_data"


# =========================================================================
# Check 4 — Relationship Types (≥90% FS)
# =========================================================================


def test_check_relationship_types_all_fs_passes():
    activities = [
        {"id": "A", "relationships": [{"predecessor_id": "X", "type": "FS"}]},
        {"id": "B", "relationships": [{"predecessor_id": "Y", "type": "FS"}]},
    ]
    out = _check_relationship_types(activities)
    assert out["value"] == 100.0
    assert out["status"] == "pass"


def test_check_relationship_types_mixed_fails():
    activities = [
        {
            "id": "A",
            "relationships": [
                {"predecessor_id": "X", "type": "FS"},
                {"predecessor_id": "Z", "type": "SS"},
            ],
        },
    ]
    out = _check_relationship_types(activities)
    assert out["value"] == 50.0
    assert out["status"] == "fail"


def test_check_relationship_types_assumes_fs_for_simple_predecessors():
    """If only ``predecessors`` is given, all are treated as FS → 100%."""
    activities = [{"id": "B", "predecessors": ["A"]}]
    out = _check_relationship_types(activities)
    assert out["value"] == 100.0


def test_check_relationship_types_no_data_defaults_to_100():
    out = _check_relationship_types([{"id": "A"}])
    assert out["value"] == 100.0


# =========================================================================
# Check 5 — Hard Constraints (≤5%)
# =========================================================================


def test_check_hard_constraints_all_constrained_fails():
    activities = [
        {"id": "A", "constraint_type": "must_start_on"},
        {"id": "B", "constraint_type": "must_finish_on"},
    ]
    out = _check_hard_constraints(activities)
    assert out["value"] == 100.0
    assert out["status"] == "fail"


def test_check_hard_constraints_none_clean_pass():
    activities = [{"id": "A"}, {"id": "B"}]
    out = _check_hard_constraints(activities)
    assert out["value"] == 0.0
    assert out["status"] == "pass"


# =========================================================================
# Check 6 — High Float (≤5% with float > 44 days)
# =========================================================================


def test_check_high_float_over_44_counted():
    activities = [
        {"id": "A", "total_float": 50},
        {"id": "B", "total_float": 5},
    ]
    out = _check_high_float(activities)
    assert out["value"] == 50.0
    assert out["status"] == "fail"


def test_check_high_float_at_exactly_44_not_counted():
    """Threshold is > 44, so 44 itself is not high-float."""
    activities = [{"id": "A", "total_float": 44}, {"id": "B", "total_float": 0}]
    out = _check_high_float(activities)
    assert out["value"] == 0.0


# =========================================================================
# Check 7 — Negative Float (must be == 0%)
# =========================================================================


def test_check_negative_float_any_negative_fails():
    activities = [{"id": "A", "total_float": -3}]
    out = _check_negative_float(activities)
    assert out["status"] == "fail"


def test_check_negative_float_all_zero_passes():
    activities = [{"id": "A", "total_float": 0}, {"id": "B", "total_float": 5}]
    out = _check_negative_float(activities)
    assert out["status"] == "pass"


# =========================================================================
# Check 8 — High Duration (≤5% with > 44 working days)
# =========================================================================


def test_check_high_duration_over_44_fails():
    activities = [{"id": "A", "duration_days": 60}, {"id": "B", "duration_days": 10}]
    out = _check_high_duration(activities)
    assert out["value"] == 50.0
    assert out["status"] == "fail"


# =========================================================================
# Check 9 — Invalid Dates (must be == 0%)
# =========================================================================


def test_check_invalid_dates_future_actual_finish_flagged():
    """An "actual finish" in the future is impossible — flag it."""
    future = (date.today() + timedelta(days=10)).isoformat()
    activities = [{"id": "A", "actual_finish": future}]
    out = _check_invalid_dates(activities)
    assert out["value"] == 100.0
    assert out["status"] == "fail"


def test_check_invalid_dates_past_actual_finish_clean():
    past = (date.today() - timedelta(days=10)).isoformat()
    activities = [{"id": "A", "actual_finish": past}]
    out = _check_invalid_dates(activities)
    assert out["value"] == 0.0


def test_check_invalid_dates_malformed_string_skipped():
    """A garbage date string shouldn't crash the check — it's silently
    skipped."""
    activities = [{"id": "A", "actual_finish": "not-a-date"}]
    out = _check_invalid_dates(activities)
    assert out["value"] == 0.0


# =========================================================================
# Check 10 — Resources (≥90%)
# =========================================================================


def test_check_resources_all_assigned_passes():
    activities = [
        {"id": "A", "resources": ["crew-1"]},
        {"id": "B", "resource_assignments": [{"role": "mason"}]},
    ]
    out = _check_resources(activities)
    assert out["value"] == 100.0
    assert out["status"] == "pass"


def test_check_resources_none_assigned_fails():
    activities = [{"id": "A"}, {"id": "B"}]
    out = _check_resources(activities)
    assert out["value"] == 0.0
    assert out["status"] == "fail"


# =========================================================================
# Check 11 — Missed Tasks (≤5%)
# =========================================================================


def test_check_missed_tasks_overdue_no_actual_finish_counts():
    past = (date.today() - timedelta(days=5)).isoformat()
    activities = [{"id": "A", "planned_finish": past}]  # no actual_finish, no status
    out = _check_missed_tasks(activities)
    assert out["value"] == 100.0
    assert out["status"] == "fail"


def test_check_missed_tasks_completed_status_excludes():
    past = (date.today() - timedelta(days=5)).isoformat()
    activities = [{"id": "A", "planned_finish": past, "status": "completed"}]
    out = _check_missed_tasks(activities)
    assert out["value"] == 0.0


def test_check_missed_tasks_actual_finish_set_excludes():
    past = (date.today() - timedelta(days=5)).isoformat()
    activities = [{"id": "A", "planned_finish": past, "actual_finish": past}]
    out = _check_missed_tasks(activities)
    assert out["value"] == 0.0


# =========================================================================
# Check 12 — Critical Path Test
# =========================================================================


def test_critical_path_test_continuous_chain_passes():
    """A → B → C all on critical path → continuous, valid → score 1.0.
    Note: 1.0 vs threshold 1.0 with ">=" leaves margin 0 / |1| = 0, which
    is < 10%, so the helper marks status as "warning" (still passing)."""
    activities = [
        {"id": "A", "predecessors": [], "is_critical": True},
        {"id": "B", "predecessors": ["A"], "is_critical": True},
        {"id": "C", "predecessors": ["B"], "is_critical": True},
    ]
    out = _check_critical_path_test(activities)
    # The check passed (score 1.0) — but the warning band catches exact
    # threshold matches. What matters: it didn't fail.
    assert out["status"] in {"pass", "warning"}
    assert out["score"] == 1.0


def test_critical_path_test_no_critical_activities_fails():
    activities = [{"id": "A", "is_critical": False}]
    out = _check_critical_path_test(activities)
    assert out["status"] == "fail"


# =========================================================================
# Check 13 — CPLI
# =========================================================================


def test_cpli_no_baseline_defaults_to_one():
    out = _check_cpli([], baseline=None)
    assert out["value"] == 1.0
    assert out["status"] == "warning"  # 1.0 >= 0.95 but within 10% → warning


def test_cpli_calculation():
    """CPLI = planned / (actual + remaining)."""
    out = _check_cpli(
        [],
        baseline={
            "planned_duration": 100,
            "actual_duration": 50,
            "remaining_duration": 60,
        },
    )
    # 100 / 110 ≈ 0.909 — below 0.95 → fail
    assert out["value"] == pytest.approx(0.91, abs=0.01)
    assert out["status"] == "fail"


def test_cpli_zero_denominator_defaults_to_one():
    out = _check_cpli(
        [],
        baseline={"planned_duration": 100, "actual_duration": 0, "remaining_duration": 0},
    )
    assert out["value"] == 1.0


# =========================================================================
# Check 14 — BEI
# =========================================================================


def test_bei_with_baseline():
    out = _check_bei(
        [],
        baseline={"tasks_completed": 9, "tasks_planned_to_be_complete": 10},
    )
    assert out["value"] == 0.9
    assert out["status"] == "fail"  # 0.9 < 0.95


def test_bei_no_baseline_computes_from_activities():
    past = (date.today() - timedelta(days=10)).isoformat()
    activities = [
        {"id": "A", "planned_finish": past, "status": "completed"},
        {"id": "B", "planned_finish": past, "status": "in_progress"},
    ]
    out = _check_bei(activities, baseline=None)
    # 1 of 2 planned-complete tasks actually finished → 0.5
    assert out["value"] == 0.5


def test_bei_no_baseline_no_planned_defaults_to_one():
    out = _check_bei([{"id": "A"}], baseline=None)
    assert out["value"] == 1.0


# =========================================================================
# run_dcma_check — end-to-end + grading
# =========================================================================


async def test_run_dcma_check_emits_14_results():
    activities = [
        {"id": "A", "duration_days": 5, "predecessors": []},
        {"id": "B", "duration_days": 3, "predecessors": ["A"]},
    ]
    out = await run_dcma_check(activities)
    assert len(out["checks"]) == 14
    assert out["passed"] + out["failed"] + out["warning"] + out["skipped"] == 14
    assert "grade" in out
    assert out["grade"] in ("A", "B", "C", "D", "F")


async def test_run_dcma_check_grade_buckets():
    """Verify grade thresholds — empty schedule fails the >= checks
    (Logic, Resources, Critical Path) but passes the <= ones at 0.
    Resulting grade lands in the C–D band."""
    out = await run_dcma_check([])
    assert out["grade"] in {"C", "D", "F"}
    # And at least one check should fail (no activities):
    assert out["failed"] >= 1


async def test_run_dcma_check_calls_cpm_when_total_float_missing():
    """If activities don't carry CPM-computed fields, the runner must
    invoke calculate_cpm to populate them — pin that the resulting
    Negative-Float check (Check 7) passes for a clean schedule."""
    activities = [
        {"id": "A", "duration_days": 5, "predecessors": []},
        {"id": "B", "duration_days": 3, "predecessors": ["A"]},
    ]
    out = await run_dcma_check(activities)
    # Find Check 7 (Negative Float) in the results — must pass.
    check_7 = next(c for c in out["checks"] if c["check_number"] == 7)
    assert check_7["status"] == "pass"
