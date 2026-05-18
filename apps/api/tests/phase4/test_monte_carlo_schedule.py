"""Tests for Monte Carlo schedule risk simulation."""

from __future__ import annotations

from app.services.controls.monte_carlo_schedule import (
    run_schedule_risk_simulation,
)
from tests.fixtures.sample_evm_data import (
    SAMPLE_SCHEDULE_ACTIVITIES,
)


class TestMonteCarloSchedule:
    async def test_basic_simulation(self):
        result = await run_schedule_risk_simulation(
            activities=SAMPLE_SCHEDULE_ACTIVITIES,
            num_iterations=1000,
            seed=42,
        )
        assert result["num_iterations"] == 1000
        assert result["p10_duration"] > 0
        assert result["p50_duration"] > 0
        assert result["p80_duration"] > 0
        assert result["p90_duration"] > 0
        assert result["p10_duration"] <= result["p50_duration"]
        assert result["p50_duration"] <= result["p80_duration"]
        assert result["p80_duration"] <= result["p90_duration"]

    async def test_deterministic_with_seed(self):
        r1 = await run_schedule_risk_simulation(
            activities=SAMPLE_SCHEDULE_ACTIVITIES,
            num_iterations=500,
            seed=42,
        )
        r2 = await run_schedule_risk_simulation(
            activities=SAMPLE_SCHEDULE_ACTIVITIES,
            num_iterations=500,
            seed=42,
        )
        assert r1["p50_duration"] == r2["p50_duration"]
        assert r1["mean_duration"] == r2["mean_duration"]

    async def test_critical_risk_drivers(self):
        result = await run_schedule_risk_simulation(
            activities=SAMPLE_SCHEDULE_ACTIVITIES,
            num_iterations=1000,
            seed=42,
        )
        drivers = result["critical_risk_drivers"]
        assert isinstance(drivers, list)
        if drivers:
            assert "activity_id" in drivers[0]
            assert "criticality_pct" in drivers[0]

    async def test_histogram_data(self):
        result = await run_schedule_risk_simulation(
            activities=SAMPLE_SCHEDULE_ACTIVITIES,
            num_iterations=1000,
            seed=42,
        )
        assert len(result["histogram_data"]) == 20
        assert sum(result["histogram_data"]) == 1000

    async def test_empty_activities(self):
        result = await run_schedule_risk_simulation(
            activities=[],
            num_iterations=100,
        )
        assert result["num_iterations"] == 0
        assert result["p50_duration"] == 0

    async def test_single_activity(self):
        result = await run_schedule_risk_simulation(
            activities=[
                {
                    "id": "1",
                    "name": "Only Task",
                    "duration_days": 30,
                    "predecessors": [],
                }
            ],
            num_iterations=500,
            seed=42,
        )
        assert result["p50_duration"] > 0
        assert result["mean_duration"] > 0
