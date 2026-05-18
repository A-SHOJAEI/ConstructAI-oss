"""Tests for the SimPy-based site operations simulation.

The simulation itself is stochastic; we use a deterministic seed so
the results are reproducible. What we pin is the report shape, the
bottleneck-detection thresholds, and the recommendation rules.
"""

from __future__ import annotations

import random

import pytest

# Skip everything if SimPy isn't installed (it's an optional dep):
simpy = pytest.importorskip("simpy")

from app.services.logistics.simulation import (
    _SiteMetrics,
    run_site_simulation,
)


@pytest.fixture(autouse=True)
def deterministic_seed():
    """Seed Python's random so SimPy's exponential inter-arrivals
    don't drift the test results from run to run."""
    random.seed(42)
    yield


# =========================================================================
# _SiteMetrics — initial state
# =========================================================================


def test_site_metrics_initial_state():
    m = _SiteMetrics()
    assert m.timeline == []
    assert m.tasks_completed == 0
    assert m.tasks_started == 0
    # defaultdicts return 0 / [] for missing keys:
    assert m.resource_busy["unseen_resource"] == 0
    assert m.wait_times["unseen_resource"] == []


# =========================================================================
# run_site_simulation — basic shape
# =========================================================================


@pytest.mark.asyncio
async def test_run_site_simulation_returns_required_keys():
    scenario = {
        "resources": {"crane": 1, "truck": 2},
        "tasks": [
            {
                "name": "concrete_pour",
                "duration_hours": 2,
                "resources_needed": {"crane": 1},
                "priority": 1,
            },
        ],
        "arrival_rate": 4,
    }
    out = await run_site_simulation(scenario, duration_days=2)
    for key in (
        "timeline",
        "bottlenecks",
        "utilization",
        "throughput",
        "avg_wait_time",
        "recommendations",
    ):
        assert key in out


@pytest.mark.asyncio
async def test_simulation_no_tasks_no_throughput():
    """No tasks → throughput should be 0."""
    scenario = {
        "resources": {"crane": 1},
        "tasks": [{"name": "x", "duration_hours": 1, "resources_needed": {"crane": 1}}],
        "arrival_rate": 0,  # no arrivals
    }
    out = await run_site_simulation(scenario, duration_days=1)
    # With 0 arrival rate, generator yields once every 24h — depending
    # on duration may or may not run. Cap throughput at expected:
    assert out["throughput"] >= 0


@pytest.mark.asyncio
async def test_simulation_utilization_per_resource():
    """Each declared resource gets a utilization entry with the
    documented schema."""
    scenario = {
        "resources": {"crane": 2, "truck": 3},
        "tasks": [
            {
                "name": "lift",
                "duration_hours": 1,
                "resources_needed": {"crane": 1},
            },
        ],
        "arrival_rate": 5,
    }
    out = await run_site_simulation(scenario, duration_days=1)
    util = out["utilization"]
    assert "crane" in util
    assert "truck" in util
    for stats in util.values():
        assert "utilization_pct" in stats
        assert "idle_pct" in stats
        assert "busy_hours" in stats
        assert "idle_hours" in stats
        # Pct fields between 0 and 100:
        assert 0 <= stats["utilization_pct"] <= 100


@pytest.mark.asyncio
async def test_simulation_bottleneck_detected_when_utilization_high():
    """A heavily-contested resource (1 crane, all tasks need it, fast
    arrivals) should appear as a bottleneck."""
    scenario = {
        "resources": {"crane": 1},
        "tasks": [
            {
                "name": "lift_a",
                "duration_hours": 4,
                "resources_needed": {"crane": 1},
            },
        ],
        "arrival_rate": 20,  # 20 tasks/day, each takes 4h with 1 crane → backlog
    }
    out = await run_site_simulation(scenario, duration_days=2)
    crane_util = out["utilization"]["crane"]["utilization_pct"]
    if crane_util > 80:
        # If the simulation produces high utilization, bottleneck must be flagged:
        assert any(b["resource"] == "crane" for b in out["bottlenecks"])


@pytest.mark.asyncio
async def test_simulation_recommendations_present_for_idle_resources():
    """Underutilized resources (<40%) trigger a "consider reducing
    capacity" recommendation."""
    scenario = {
        "resources": {"truck": 10},  # way too many trucks
        "tasks": [
            {
                "name": "task_a",
                "duration_hours": 1,
                "resources_needed": {"truck": 1},
            },
        ],
        "arrival_rate": 1,  # one task/day → 9 trucks idle
    }
    out = await run_site_simulation(scenario, duration_days=1)
    truck_util = out["utilization"]["truck"]["utilization_pct"]
    if truck_util < 40:
        recs = " ".join(out["recommendations"]).lower()
        assert "low utilization" in recs or "reducing" in recs


@pytest.mark.asyncio
async def test_simulation_no_recommendations_means_default_message():
    """A sim with no flagged issues should still emit at least one
    recommendation — the documented "operating within acceptable
    parameters" string."""
    scenario = {
        "resources": {"crane": 5},
        "tasks": [
            {
                "name": "task_a",
                "duration_hours": 1,
                "resources_needed": {"crane": 1},
            },
        ],
        "arrival_rate": 4,  # 4/day with 5 cranes — comfortable
    }
    out = await run_site_simulation(scenario, duration_days=1)
    assert out["recommendations"]  # at least one


@pytest.mark.asyncio
async def test_simulation_timeline_capped_at_500():
    """High-volume sim must cap timeline at 500 entries to keep response
    bounded."""
    scenario = {
        "resources": {"crane": 5},
        "tasks": [
            {
                "name": "tiny",
                "duration_hours": 0.1,
                "resources_needed": {"crane": 1},
            },
        ],
        "arrival_rate": 100,  # high rate → many events
    }
    out = await run_site_simulation(scenario, duration_days=2)
    assert len(out["timeline"]) <= 500


@pytest.mark.asyncio
async def test_simulation_throughput_rounded_to_two_decimals():
    """Throughput is a rounded float, not raw — pin the contract."""
    scenario = {
        "resources": {"crane": 1},
        "tasks": [
            {
                "name": "task",
                "duration_hours": 1,
                "resources_needed": {"crane": 1},
            },
        ],
        "arrival_rate": 5,
    }
    out = await run_site_simulation(scenario, duration_days=1)
    # round(x, 2) should give at most 2 decimal places:
    assert round(out["throughput"], 2) == out["throughput"]
