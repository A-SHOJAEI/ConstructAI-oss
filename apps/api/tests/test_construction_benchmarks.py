"""Tests for industry benchmark lookups + value comparison.

Pin the documented benchmark dictionary and the
``check_against_benchmark`` decision tree (target-mode vs range-mode).
"""

from __future__ import annotations

import pytest

from app.services.evaluation.construction_benchmarks import (
    INDUSTRY_BENCHMARKS,
    check_against_benchmark,
    get_benchmark,
)

# =========================================================================
# INDUSTRY_BENCHMARKS — pin canonical project types and metrics
# =========================================================================


def test_industry_benchmarks_canonical_project_types():
    """Pin the 4 documented project types — refactor must not silently
    drop one."""
    expected = {
        "commercial_office",
        "residential_multifamily",
        "industrial",
        "healthcare",
    }
    assert set(INDUSTRY_BENCHMARKS.keys()) == expected


def test_each_project_has_required_metrics():
    """Each project type must carry the 4 documented metrics."""
    required = {
        "cost_per_sf",
        "schedule_months",
        "safety_incident_rate",
        "rework_rate",
    }
    for project_type, metrics in INDUSTRY_BENCHMARKS.items():
        missing = required - set(metrics.keys())
        assert not missing, f"{project_type} missing metrics: {missing}"


def test_healthcare_highest_cost_per_sf():
    """Healthcare has the highest documented cost band — pin so a
    refactor doesn't accidentally swap with another type."""
    healthcare_high = INDUSTRY_BENCHMARKS["healthcare"]["cost_per_sf"]["high"]
    for project_type, metrics in INDUSTRY_BENCHMARKS.items():
        if project_type != "healthcare":
            assert metrics["cost_per_sf"]["high"] <= healthcare_high


def test_safety_targets_below_5():
    """Industry-best safety incident rate is 2-4 per 100 FTE — pin
    that all targets are within sane construction range."""
    for project_type, metrics in INDUSTRY_BENCHMARKS.items():
        target = metrics["safety_incident_rate"]["target"]
        assert 0 < target < 5


# =========================================================================
# get_benchmark
# =========================================================================


def test_get_benchmark_known_project_and_metric():
    out = get_benchmark("healthcare", "cost_per_sf")
    assert out is not None
    assert out["low"] == 300
    assert out["high"] == 800


def test_get_benchmark_unknown_project_returns_none():
    assert get_benchmark("alien_project", "cost_per_sf") is None


def test_get_benchmark_unknown_metric_returns_none():
    """Known project type but unknown metric → None."""
    assert get_benchmark("commercial_office", "alien_metric") is None


def test_get_benchmark_returns_dict():
    out = get_benchmark("commercial_office", "rework_rate")
    assert isinstance(out, dict)


# =========================================================================
# check_against_benchmark — target-mode metrics
# =========================================================================


def test_check_against_benchmark_unknown_returns_unknown_status():
    out = check_against_benchmark("alien_project", "cost_per_sf", 100.0)
    assert out["status"] == "unknown"
    assert "alien_project" in out["message"]


def test_target_mode_value_at_or_below_target_passes():
    """Lower-is-better metric: value ≤ target → pass."""
    # Healthcare safety target is 2.5
    out = check_against_benchmark("healthcare", "safety_incident_rate", 2.0)
    assert out["status"] == "pass"
    assert out["target"] == 2.5
    assert out["value"] == 2.0


def test_target_mode_exact_target_is_pass():
    """At exactly the target value, ≤ comparison passes."""
    out = check_against_benchmark("healthcare", "safety_incident_rate", 2.5)
    assert out["status"] == "pass"


def test_target_mode_above_target_warning():
    out = check_against_benchmark("healthcare", "safety_incident_rate", 3.0)
    assert out["status"] == "warning"
    assert "exceeds target" in out["message"]


def test_target_mode_rework_rate_below_target_passes():
    """Healthcare rework_rate target is 0.03 — at 0.02, pass."""
    out = check_against_benchmark("healthcare", "rework_rate", 0.02)
    assert out["status"] == "pass"


# =========================================================================
# check_against_benchmark — range-mode metrics
# =========================================================================


def test_range_mode_within_range_passes():
    """Commercial office cost_per_sf range is 150-450 — at 250, pass."""
    out = check_against_benchmark("commercial_office", "cost_per_sf", 250.0)
    assert out["status"] == "pass"
    assert out["range"] == [150, 450]


def test_range_mode_at_lower_bound_passes():
    out = check_against_benchmark("commercial_office", "cost_per_sf", 150.0)
    assert out["status"] == "pass"


def test_range_mode_at_upper_bound_passes():
    out = check_against_benchmark("commercial_office", "cost_per_sf", 450.0)
    assert out["status"] == "pass"


def test_range_mode_below_range_warning():
    """100 < low (150) → warning."""
    out = check_against_benchmark("commercial_office", "cost_per_sf", 100.0)
    assert out["status"] == "warning"
    assert "outside range" in out["message"]


def test_range_mode_above_range_warning():
    """500 > high (450) → warning."""
    out = check_against_benchmark("commercial_office", "cost_per_sf", 500.0)
    assert out["status"] == "warning"
    assert "outside range" in out["message"]


def test_range_mode_schedule_months_within_range():
    """Industrial schedule_months range is 6-18."""
    out = check_against_benchmark("industrial", "schedule_months", 12.0)
    assert out["status"] == "pass"


# =========================================================================
# Result shape contracts
# =========================================================================


@pytest.mark.parametrize(
    "project_type,metric,value",
    [
        ("commercial_office", "cost_per_sf", 250.0),  # pass-range
        ("commercial_office", "cost_per_sf", 100.0),  # warning-range
        ("healthcare", "safety_incident_rate", 2.0),  # pass-target
        ("healthcare", "safety_incident_rate", 5.0),  # warning-target
    ],
)
def test_result_includes_value_field(project_type, metric, value):
    """Every non-unknown result includes a "value" field — pin the
    contract."""
    out = check_against_benchmark(project_type, metric, value)
    assert out["status"] != "unknown"
    assert out["value"] == value
