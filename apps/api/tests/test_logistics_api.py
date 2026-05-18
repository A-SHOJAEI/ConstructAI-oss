"""Phase 2: Logistics API endpoint tests.

Tests for the logistics REST API endpoints including site layout optimization,
simulation, and layout listing. Service functions are mocked to avoid
external dependencies.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest_asyncio

from app.models.project import Project
from tests.fixtures.precon_mock_responses import (
    MOCK_FACILITIES,
    MOCK_SIMULATION_SCENARIO,
    MOCK_SITE_BOUNDARY,
)


@pytest_asyncio.fixture
async def test_project(db_session, test_org):
    """Create a test project for logistics API tests."""
    project = Project(name="Logistics Test Project", org_id=test_org.id)
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


class TestLogisticsApi:
    """Tests for the logistics API endpoints."""

    @patch("app.services.logistics.site_layout.optimize_site_layout")
    async def test_optimize_site_layout(self, mock_optimize, client, auth_headers, test_project):
        """POST /api/v1/logistics/site-layouts/optimize should run optimization."""
        mock_optimize.return_value = {
            "layouts": [
                {
                    "layout_data": {"facility_positions": {}},
                    "optimization_score": 85.0,
                    "safety_score": 90.0,
                    "efficiency_score": 80.0,
                    "pareto_rank": 1,
                    "generation": 5,
                },
            ],
            "pareto_front": [{"travel_distance": 100, "safety_score": 90, "efficiency_score": 80}],
            "generations": 5,
        }

        response = await client.post(
            "/api/v1/logistics/site-layouts/optimize",
            json={
                "project_id": str(test_project.id),
                "facilities": MOCK_FACILITIES,
                "site_boundary": MOCK_SITE_BOUNDARY,
                "constraints": {},
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "layouts" in data

    @patch("app.services.logistics.simulation.run_site_simulation")
    async def test_run_simulation(self, mock_sim, client, auth_headers, test_project):
        """POST /api/v1/logistics/simulate should run a simulation."""
        mock_sim.return_value = {
            "timeline": [],
            "bottlenecks": [],
            "utilization": {"cranes": {"utilization_pct": 75.0, "idle_pct": 25.0}},
            "recommendations": ["No issues found."],
            "throughput": 5.0,
            "avg_wait_time": 0.5,
        }

        response = await client.post(
            "/api/v1/logistics/simulate",
            json={
                "project_id": str(test_project.id),
                "scenario": MOCK_SIMULATION_SCENARIO,
                "duration_days": 5,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "recommendations" in data

    async def test_list_site_layouts(self, client, auth_headers, test_project):
        """GET /api/v1/logistics/site-layouts should list layouts for a project."""
        response = await client.get(
            f"/api/v1/logistics/site-layouts?project_id={test_project.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "meta" in data

    async def test_get_site_layout_not_found(self, client, auth_headers):
        """GET /api/v1/logistics/site-layouts/:id with invalid ID should return 404."""
        import uuid

        fake_id = uuid.uuid4()
        response = await client.get(
            f"/api/v1/logistics/site-layouts/{fake_id}",
            headers=auth_headers,
        )
        assert response.status_code == 404
