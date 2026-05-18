"""Construction-specific benchmarks for agent evaluation."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Construction industry benchmark data
INDUSTRY_BENCHMARKS: dict[str, dict[str, dict[str, Any]]] = {
    "commercial_office": {
        "cost_per_sf": {"low": 150, "high": 450, "unit": "$/sf"},
        "schedule_months": {"low": 12, "high": 36},
        "safety_incident_rate": {"target": 3.0},
        "rework_rate": {"target": 0.05},
    },
    "residential_multifamily": {
        "cost_per_sf": {"low": 100, "high": 300, "unit": "$/sf"},
        "schedule_months": {"low": 8, "high": 24},
        "safety_incident_rate": {"target": 3.5},
        "rework_rate": {"target": 0.04},
    },
    "industrial": {
        "cost_per_sf": {"low": 80, "high": 250, "unit": "$/sf"},
        "schedule_months": {"low": 6, "high": 18},
        "safety_incident_rate": {"target": 4.0},
        "rework_rate": {"target": 0.06},
    },
    "healthcare": {
        "cost_per_sf": {"low": 300, "high": 800, "unit": "$/sf"},
        "schedule_months": {"low": 18, "high": 48},
        "safety_incident_rate": {"target": 2.5},
        "rework_rate": {"target": 0.03},
    },
}


def get_benchmark(
    project_type: str,
    metric: str,
) -> dict | None:
    """Get industry benchmark for project type and metric."""
    benchmarks = INDUSTRY_BENCHMARKS.get(project_type)
    if not benchmarks:
        return None
    return benchmarks.get(metric)


def check_against_benchmark(
    project_type: str,
    metric: str,
    value: float,
) -> dict:
    """Check a value against industry benchmarks.

    Returns status (pass/warning/fail) and details.
    """
    benchmark = get_benchmark(project_type, metric)
    if not benchmark:
        return {
            "status": "unknown",
            "message": (f"No benchmark for {project_type}/{metric}"),
        }

    if "target" in benchmark:
        if value <= benchmark["target"]:
            return {
                "status": "pass",
                "value": value,
                "target": benchmark["target"],
            }
        return {
            "status": "warning",
            "value": value,
            "target": benchmark["target"],
            "message": (f"{metric}={value} exceeds target {benchmark['target']}"),
        }

    low = benchmark.get("low", 0)
    high = benchmark.get("high", float("inf"))
    if low <= value <= high:
        return {
            "status": "pass",
            "value": value,
            "range": [low, high],
        }
    return {
        "status": "warning",
        "value": value,
        "range": [low, high],
        "message": (f"{metric}={value} outside range [{low}, {high}]"),
    }
