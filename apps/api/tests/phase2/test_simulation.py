"""Phase 2: SimPy discrete-event simulation tests.

Tests for the construction site simulation that models resource contention
and task processing. SimPy runs locally; no external API calls are made.
"""

from __future__ import annotations

import pytest

simpy = pytest.importorskip("simpy", reason="SimPy is required for simulation tests")

from app.services.logistics.simulation import run_site_simulation
from tests.fixtures.precon_mock_responses import MOCK_SIMULATION_SCENARIO


class TestSimulation:
    """Tests for the site simulation service."""

    async def test_run_simulation_returns_results(self):
        """Simulation should return utilization, bottlenecks, and recommendations."""
        result = await run_site_simulation(MOCK_SIMULATION_SCENARIO, duration_days=5)
        assert "utilization" in result
        assert "bottlenecks" in result
        assert "recommendations" in result

    async def test_simulation_utilization_range(self):
        """Utilization percentages should be between 0 and 100."""
        result = await run_site_simulation(MOCK_SIMULATION_SCENARIO, duration_days=5)
        for _resource, stats in result["utilization"].items():
            assert 0 <= stats["utilization_pct"] <= 100

    async def test_simulation_throughput(self):
        """Simulation should report non-negative throughput."""
        result = await run_site_simulation(MOCK_SIMULATION_SCENARIO, duration_days=10)
        assert "throughput" in result
        assert result["throughput"] >= 0

    async def test_simulation_timeline(self):
        """Simulation should produce a timeline of events."""
        result = await run_site_simulation(MOCK_SIMULATION_SCENARIO, duration_days=5)
        assert "timeline" in result
        assert isinstance(result["timeline"], list)

    async def test_simulation_avg_wait_time(self):
        """Simulation should report average wait time."""
        result = await run_site_simulation(MOCK_SIMULATION_SCENARIO, duration_days=5)
        assert "avg_wait_time" in result
        assert result["avg_wait_time"] >= 0

    async def test_simulation_empty_tasks(self):
        """Empty task list should produce empty results."""
        empty_scenario = {"resources": {"cranes": 1}, "tasks": [], "arrival_rate": 0}
        result = await run_site_simulation(empty_scenario, duration_days=1)
        assert result["throughput"] == 0
