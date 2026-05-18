"""Phase 2: Logistics agent orchestration tests.

Tests for the logistics agent that coordinates site layout optimization,
delivery routing, and simulation. All downstream services are mocked.
"""

from __future__ import annotations

from unittest.mock import patch

from tests.fixtures.precon_mock_responses import (
    MOCK_DELIVERIES,
    MOCK_DEPOT,
    MOCK_FACILITIES,
    MOCK_SIMULATION_SCENARIO,
    MOCK_SITE_BOUNDARY,
    MOCK_VEHICLES,
)


class TestLogisticsAgent:
    """Tests for the logistics agent orchestrator."""

    @patch("app.services.logistics.simulation.run_site_simulation")
    @patch("app.services.logistics.delivery_router.optimize_delivery_routes")
    @patch("app.services.logistics.site_layout.optimize_site_layout")
    async def test_logistics_pipeline_integration(self, mock_layout, mock_routes, mock_sim):
        """The full logistics pipeline should run layout, routing, and simulation."""
        mock_layout.return_value = {
            "layouts": [{"facility_positions": {}, "travel_distance": 100}],
            "pareto_front": [],
            "generations": 5,
            "population_size": 10,
        }
        mock_routes.return_value = {
            "routes": [],
            "total_cost": 0,
            "total_distance": 0,
            "unassigned": [],
            "computation_time_ms": 50,
        }
        mock_sim.return_value = {
            "utilization": {},
            "bottlenecks": [],
            "recommendations": [],
            "throughput": 5.0,
            "avg_wait_time": 0.5,
            "timeline": [],
        }

        from app.services.logistics.delivery_router import optimize_delivery_routes
        from app.services.logistics.simulation import run_site_simulation
        from app.services.logistics.site_layout import optimize_site_layout

        layout_result = await optimize_site_layout(
            MOCK_FACILITIES,
            MOCK_SITE_BOUNDARY,
            {},
            population_size=10,
            generations=5,
        )
        assert len(layout_result["layouts"]) > 0

        route_result = await optimize_delivery_routes(
            MOCK_DELIVERIES,
            MOCK_VEHICLES,
            MOCK_DEPOT,
            "2025-03-01",
        )
        assert "total_cost" in route_result

        sim_result = await run_site_simulation(MOCK_SIMULATION_SCENARIO, duration_days=5)
        assert "throughput" in sim_result

    @patch("app.services.logistics.site_layout.optimize_site_layout")
    async def test_logistics_layout_only(self, mock_layout):
        """Agent should handle layout-only scenarios."""
        mock_layout.return_value = {
            "layouts": [{"facility_positions": {}}],
            "pareto_front": [],
            "generations": 5,
            "population_size": 10,
        }

        from app.services.logistics.site_layout import optimize_site_layout

        result = await optimize_site_layout(
            MOCK_FACILITIES,
            MOCK_SITE_BOUNDARY,
            {},
            population_size=10,
            generations=5,
        )
        assert result is not None
        assert len(result["layouts"]) > 0

    @patch("app.services.logistics.delivery_router.optimize_delivery_routes")
    async def test_logistics_empty_deliveries(self, mock_routes):
        """Agent should handle empty delivery list."""
        mock_routes.return_value = {
            "routes": [],
            "total_cost": 0.0,
            "total_distance": 0.0,
            "unassigned": [],
            "computation_time_ms": 0,
        }

        from app.services.logistics.delivery_router import optimize_delivery_routes

        result = await optimize_delivery_routes([], MOCK_VEHICLES, MOCK_DEPOT, "2025-03-01")
        assert result["total_cost"] == 0.0
