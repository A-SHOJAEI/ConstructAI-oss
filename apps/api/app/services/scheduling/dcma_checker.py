"""DCMA 14-Point Schedule Health Assessment.

Implements the Defense Contract Management Agency schedule health checks
used to evaluate the quality and reliability of construction schedules.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from app.services.scheduling.cpm_engine import calculate_cpm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual check implementations
# ---------------------------------------------------------------------------


def _pct(count: int, total: int) -> float:
    """Return percentage (0-100), safe against zero division."""
    return (count / total * 100.0) if total > 0 else 0.0


def _check_result(
    check_number: int,
    check_name: str,
    value: float,
    threshold: float,
    operator: str,
    description: str,
) -> dict:
    """Build a standard check result dict.

    operator: ">=" means value must be >= threshold to pass,
              "<=" means value must be <= threshold to pass,
              "==" means value must equal threshold to pass.
    """
    if operator == ">=":
        passed = value >= threshold
    elif operator == "<=":
        passed = value <= threshold
    elif operator == "==":
        passed = value == threshold
    else:
        passed = False

    # Warning zone: within 10% of failing (relative to threshold distance)
    warning = False
    if passed and operator in (">=", "<="):
        margin = abs(value - threshold)
        warning = margin / abs(threshold) < 0.10 if threshold != 0 else margin < 1.0

    if not passed:
        status = "fail"
    elif warning:
        status = "warning"
    else:
        status = "pass"

    return {
        "check_number": check_number,
        "check_name": check_name,
        "status": status,
        "score": 1.0 if passed else 0.0,
        "value": round(value, 2),
        "threshold": threshold,
        "description": description,
    }


def _check_logic(activities: list[dict]) -> dict:
    """Check 1: % of activities with both predecessor and successor."""
    total = len(activities)
    if total == 0:
        return _check_result(1, "Logic", 0.0, 90.0, ">=", "No activities to evaluate.")

    # Build successor map
    has_predecessor: set[str] = set()
    has_successor: set[str] = set()
    for act in activities:
        aid = str(act["id"])
        preds = [str(p) for p in act.get("predecessors", [])]
        if preds:
            has_predecessor.add(aid)
        for pred_id in preds:
            has_successor.add(pred_id)

    with_both = has_predecessor & has_successor
    pct = _pct(len(with_both), total)
    return _check_result(
        1,
        "Logic",
        pct,
        90.0,
        ">=",
        f"{len(with_both)}/{total} activities have both predecessor and successor.",
    )


def _check_leads(activities: list[dict]) -> dict:
    """Check 2: % of relationships with leads (negative lag)."""
    total_rels = 0
    lead_count = 0
    has_relationship_data = False
    for act in activities:
        rels = act.get("relationships", [])
        for rel in rels:
            has_relationship_data = True
            total_rels += 1
            lag = rel.get("lag", 0)
            if lag < 0:
                lead_count += 1

    # When no relationship data exists, count from predecessors but mark as
    # insufficient -- we cannot determine lead/lag without relationship details.
    if not has_relationship_data:
        for act in activities:
            total_rels += len(act.get("predecessors", []))
        if total_rels > 0:
            return {
                "check_number": 2,
                "check_name": "Leads",
                "status": "insufficient_data",
                "score": 0.0,
                "value": 0.0,
                "threshold": 5.0,
                "description": (
                    f"Relationship detail (lag values) not available; "
                    f"{total_rels} predecessor links found but lead/lag "
                    f"cannot be assessed. Provide relationship data for "
                    f"accurate evaluation."
                ),
            }

    pct = _pct(lead_count, total_rels) if total_rels > 0 else 0.0
    return _check_result(
        2,
        "Leads",
        pct,
        5.0,
        "<=",
        f"{lead_count}/{total_rels} relationships have leads.",
    )


def _check_lags(activities: list[dict]) -> dict:
    """Check 3: % of relationships with lags."""
    total_rels = 0
    lag_count = 0
    has_relationship_data = False
    for act in activities:
        rels = act.get("relationships", [])
        for rel in rels:
            has_relationship_data = True
            total_rels += 1
            lag = rel.get("lag", 0)
            if lag > 0:
                lag_count += 1

    # When no relationship data exists, count from predecessors but mark as
    # insufficient -- we cannot determine lead/lag without relationship details.
    if not has_relationship_data:
        for act in activities:
            total_rels += len(act.get("predecessors", []))
        if total_rels > 0:
            return {
                "check_number": 3,
                "check_name": "Lags",
                "status": "insufficient_data",
                "score": 0.0,
                "value": 0.0,
                "threshold": 5.0,
                "description": (
                    f"Relationship detail (lag values) not available; "
                    f"{total_rels} predecessor links found but lead/lag "
                    f"cannot be assessed. Provide relationship data for "
                    f"accurate evaluation."
                ),
            }

    pct = _pct(lag_count, total_rels) if total_rels > 0 else 0.0
    return _check_result(
        3,
        "Lags",
        pct,
        5.0,
        "<=",
        f"{lag_count}/{total_rels} relationships have lags.",
    )


def _check_relationship_types(activities: list[dict]) -> dict:
    """Check 4: % of Finish-to-Start relationships."""
    total_rels = 0
    fs_count = 0
    for act in activities:
        rels = act.get("relationships", [])
        for rel in rels:
            total_rels += 1
            rel_type = rel.get("type", "FS").upper()
            if rel_type == "FS":
                fs_count += 1

    # If no explicit relationships, assume all predecessor links are FS
    if total_rels == 0:
        for act in activities:
            pred_count = len(act.get("predecessors", []))
            total_rels += pred_count
            fs_count += pred_count

    pct = _pct(fs_count, total_rels) if total_rels > 0 else 100.0
    return _check_result(
        4,
        "Relationship Types",
        pct,
        90.0,
        ">=",
        f"{fs_count}/{total_rels} relationships are Finish-to-Start.",
    )


def _check_hard_constraints(activities: list[dict]) -> dict:
    """Check 5: % of activities with hard constraints."""
    total = len(activities)
    constrained = sum(
        1 for act in activities if act.get("constraint_type") in ("must_start_on", "must_finish_on")
    )
    pct = _pct(constrained, total)
    return _check_result(
        5,
        "Hard Constraints",
        pct,
        5.0,
        "<=",
        f"{constrained}/{total} activities have hard constraints.",
    )


def _check_high_float(activities: list[dict]) -> dict:
    """Check 6: % of activities with total float > 44 days."""
    total = len(activities)
    high = sum(1 for act in activities if act.get("total_float", 0) > 44)
    pct = _pct(high, total)
    return _check_result(
        6,
        "High Float",
        pct,
        5.0,
        "<=",
        f"{high}/{total} activities have total float > 44 days.",
    )


def _check_negative_float(activities: list[dict]) -> dict:
    """Check 7: % of activities with negative float."""
    total = len(activities)
    negative = sum(1 for act in activities if act.get("total_float", 0) < 0)
    pct = _pct(negative, total)
    return _check_result(
        7,
        "Negative Float",
        pct,
        0.0,
        "==",
        f"{negative}/{total} activities have negative float.",
    )


def _check_high_duration(activities: list[dict]) -> dict:
    """Check 8: % of activities with duration > 44 working days."""
    total = len(activities)
    high = sum(1 for act in activities if act.get("duration_days", 0) > 44)
    pct = _pct(high, total)
    return _check_result(
        8,
        "High Duration",
        pct,
        5.0,
        "<=",
        f"{high}/{total} activities have duration > 44 working days.",
    )


def _check_invalid_dates(activities: list[dict]) -> dict:
    """Check 9: % of activities with actual dates in the future."""
    today = date.today()
    total = len(activities)
    invalid = 0

    for act in activities:
        actual_start = act.get("actual_start")
        actual_finish = act.get("actual_finish")
        for dt_val in (actual_start, actual_finish):
            if dt_val is not None:
                if isinstance(dt_val, str):
                    try:
                        dt_val = datetime.strptime(dt_val, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                if isinstance(dt_val, date) and dt_val > today:
                    invalid += 1
                    break

    pct = _pct(invalid, total)
    return _check_result(
        9,
        "Invalid Dates",
        pct,
        0.0,
        "==",
        f"{invalid}/{total} activities have actual dates in the future.",
    )


def _check_resources(activities: list[dict]) -> dict:
    """Check 10: % of activities with resources assigned."""
    total = len(activities)
    with_resources = sum(
        1 for act in activities if act.get("resources") or act.get("resource_assignments")
    )
    pct = _pct(with_resources, total)
    return _check_result(
        10,
        "Resources",
        pct,
        90.0,
        ">=",
        f"{with_resources}/{total} activities have resources assigned.",
    )


def _check_missed_tasks(activities: list[dict]) -> dict:
    """Check 11: % of tasks that should be complete but are not."""
    today = date.today()
    total = len(activities)
    missed = 0

    for act in activities:
        planned_finish = act.get("planned_finish") or act.get("baseline_finish")
        if planned_finish is not None:
            if isinstance(planned_finish, str):
                try:
                    planned_finish = datetime.strptime(planned_finish, "%Y-%m-%d").date()
                except ValueError:
                    continue
            if isinstance(planned_finish, date) and planned_finish < today:
                status = act.get("status", "")
                actual_finish = act.get("actual_finish")
                if actual_finish is None and status not in ("complete", "completed"):
                    missed += 1

    pct = _pct(missed, total)
    return _check_result(
        11,
        "Missed Tasks",
        pct,
        5.0,
        "<=",
        f"{missed}/{total} tasks should be complete but are not.",
    )


def _check_critical_path_test(activities: list[dict]) -> dict:
    """Check 12: Does the critical path make logical sense (is continuous)."""
    critical = [act for act in activities if act.get("is_critical")]

    if not critical:
        return _check_result(
            12,
            "Critical Path Test",
            0.0,
            1.0,
            ">=",
            "No critical path identified.",
        )

    # Verify the critical path forms a connected chain
    critical_ids = {str(act["id"]) for act in critical}
    has_start = False
    has_end = False

    for act in critical:
        preds = [str(p) for p in act.get("predecessors", [])]
        critical_preds = [p for p in preds if p in critical_ids]

        # Build successor info
        aid = str(act["id"])
        succs_in_critical = any(
            aid in [str(p) for p in a.get("predecessors", [])]
            for a in critical
            if str(a["id"]) != aid
        )

        if not critical_preds:
            has_start = True
        if not succs_in_critical:
            has_end = True

    is_valid = has_start and has_end and len(critical) >= 1
    return _check_result(
        12,
        "Critical Path Test",
        1.0 if is_valid else 0.0,
        1.0,
        ">=",
        "Critical path is continuous."
        if is_valid
        else "Critical path is not continuous or missing.",
    )


def _check_cpli(activities: list[dict], baseline: dict | None) -> dict:
    """Check 13: Critical Path Length Index.

    CPLI = planned_duration / (actual_duration + remaining_duration).
    """
    if baseline is None:
        return _check_result(
            13,
            "Critical Path Length Index (CPLI)",
            1.0,
            0.95,
            ">=",
            "No baseline provided; defaulting CPLI to 1.0.",
        )

    planned = baseline.get("planned_duration", 0)
    actual = baseline.get("actual_duration", 0)
    remaining = baseline.get("remaining_duration", 0)

    denominator = actual + remaining
    cpli = planned / denominator if denominator > 0 else 1.0
    return _check_result(
        13,
        "Critical Path Length Index (CPLI)",
        cpli,
        0.95,
        ">=",
        f"CPLI = {planned} / ({actual} + {remaining}) = {cpli:.3f}.",
    )


def _check_bei(activities: list[dict], baseline: dict | None) -> dict:
    """Check 14: Baseline Execution Index.

    BEI = tasks_completed / tasks_planned_to_be_complete.
    """
    if baseline is None:
        # Calculate from activity data directly
        today = date.today()
        planned_complete = 0
        actually_complete = 0

        for act in activities:
            planned_finish = act.get("planned_finish") or act.get("baseline_finish")
            if planned_finish is not None:
                if isinstance(planned_finish, str):
                    try:
                        planned_finish = datetime.strptime(planned_finish, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                if isinstance(planned_finish, date) and planned_finish <= today:
                    planned_complete += 1
                    actual_finish = act.get("actual_finish")
                    status = act.get("status", "")
                    if actual_finish is not None or status in (
                        "complete",
                        "completed",
                    ):
                        actually_complete += 1

        bei = actually_complete / planned_complete if planned_complete > 0 else 1.0
    else:
        tasks_completed = baseline.get("tasks_completed", 0)
        tasks_planned = baseline.get("tasks_planned_to_be_complete", 0)
        bei = tasks_completed / tasks_planned if tasks_planned > 0 else 1.0

    return _check_result(
        14,
        "Baseline Execution Index (BEI)",
        bei,
        0.95,
        ">=",
        f"BEI = {bei:.3f}.",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_dcma_check(activities: list[dict], baseline: dict | None = None) -> dict:
    """Run DCMA 14-point schedule health assessment.

    Parameters
    ----------
    activities:
        Schedule activities. For best results, each activity should include
        CPM-computed fields (total_float, is_critical, etc.).  If these are
        missing the function will attempt to compute them via ``calculate_cpm``.
    baseline:
        Optional baseline data with keys like ``planned_duration``,
        ``actual_duration``, ``remaining_duration``, ``tasks_completed``,
        ``tasks_planned_to_be_complete``.

    Returns
    -------
    dict with overall_score, checks, passed, failed, warning, grade.
    """
    # Ensure CPM data is present
    if activities and "total_float" not in activities[0]:
        cpm_result = await calculate_cpm(activities)
        activities = cpm_result["activities"]

    checks: list[dict] = [
        _check_logic(activities),
        _check_leads(activities),
        _check_lags(activities),
        _check_relationship_types(activities),
        _check_hard_constraints(activities),
        _check_high_float(activities),
        _check_negative_float(activities),
        _check_high_duration(activities),
        _check_invalid_dates(activities),
        _check_resources(activities),
        _check_missed_tasks(activities),
        _check_critical_path_test(activities),
        _check_cpli(activities, baseline),
        _check_bei(activities, baseline),
    ]

    passed = sum(1 for c in checks if c["status"] == "pass")
    failed = sum(1 for c in checks if c["status"] == "fail")
    warning = sum(1 for c in checks if c["status"] == "warning")
    skipped = sum(1 for c in checks if c["status"] == "insufficient_data")
    assessable = 14 - skipped
    overall_score = round(passed * (100.0 / assessable), 2) if assessable > 0 else 0.0

    if overall_score >= 90:
        grade = "A"
    elif overall_score >= 80:
        grade = "B"
    elif overall_score >= 70:
        grade = "C"
    elif overall_score >= 60:
        grade = "D"
    else:
        grade = "F"

    logger.info(
        "DCMA check complete: score=%.1f, grade=%s, passed=%d, failed=%d, warning=%d",
        overall_score,
        grade,
        passed,
        failed,
        warning,
    )

    return {
        "overall_score": overall_score,
        "checks": checks,
        "passed": passed,
        "failed": failed,
        "warning": warning,
        "skipped": skipped,
        "grade": grade,
    }
