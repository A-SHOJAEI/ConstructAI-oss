"""Phase 2: Delivery routing (VRPTW) tests.

Tests for the OR-Tools based vehicle routing with time windows.
The OR-Tools solver runs locally; no external API calls are made.
"""

from __future__ import annotations

import pytest

ortools = pytest.importorskip("ortools", reason="OR-Tools is required for delivery routing tests")

from app.services.logistics.delivery_router import optimize_delivery_routes
from tests.fixtures.precon_mock_responses import (
    MOCK_DELIVERIES,
    MOCK_DEPOT,
    MOCK_VEHICLES,
)


class TestDeliveryRouter:
    """Tests for the delivery routing optimizer."""

    async def test_optimize_routes_returns_routes(self):
        """Should return routes and total cost."""
        result = await optimize_delivery_routes(
            MOCK_DELIVERIES,
            MOCK_VEHICLES,
            MOCK_DEPOT,
            "2025-03-01",
        )
        assert "routes" in result
        assert "total_cost" in result
        assert "total_distance" in result

    async def test_all_deliveries_assigned(self):
        """All deliveries should be assigned to routes or listed as unassigned."""
        result = await optimize_delivery_routes(
            MOCK_DELIVERIES,
            MOCK_VEHICLES,
            MOCK_DEPOT,
            "2025-03-01",
        )
        assigned = set()
        for route in result["routes"]:
            for stop in route.get("stops", []):
                assigned.add(stop["delivery_id"])
        total = len(assigned) + len(result.get("unassigned", []))
        assert total == len(MOCK_DELIVERIES)

    async def test_empty_deliveries(self):
        """Empty delivery list should return no routes and zero cost."""
        result = await optimize_delivery_routes(
            [],
            MOCK_VEHICLES,
            MOCK_DEPOT,
            "2025-03-01",
        )
        assert result["routes"] == []
        assert result["total_cost"] == 0.0

    async def test_route_has_vehicle_id(self):
        """Each route should identify the assigned vehicle."""
        result = await optimize_delivery_routes(
            MOCK_DELIVERIES,
            MOCK_VEHICLES,
            MOCK_DEPOT,
            "2025-03-01",
        )
        for route in result["routes"]:
            assert "vehicle_id" in route
            assert route["vehicle_id"] in ("v1", "v2")

    async def test_computation_time_recorded(self):
        """Result should include computation time in milliseconds."""
        result = await optimize_delivery_routes(
            MOCK_DELIVERIES,
            MOCK_VEHICLES,
            MOCK_DEPOT,
            "2025-03-01",
        )
        assert "computation_time_ms" in result
        assert result["computation_time_ms"] >= 0
