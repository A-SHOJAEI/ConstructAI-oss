"""Phase 2: Estimating API endpoint tests.

Tests for the estimating REST API endpoints including create, list, and
get operations on cost estimates.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from app.models.project import Project


@pytest_asyncio.fixture
async def test_project(db_session, test_org):
    """Create a test project for estimating API tests."""
    project = Project(name="Estimating Test Project", org_id=test_org.id)
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


class TestEstimatingApi:
    """Tests for the estimating API endpoints."""

    async def test_create_estimate(self, client, auth_headers, test_project):
        """POST /api/v1/estimating/estimates should create a new estimate."""
        response = await client.post(
            "/api/v1/estimating/estimates",
            json={
                "project_id": str(test_project.id),
                "name": "Test Estimate",
                "estimate_type": "conceptual",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["name"] == "Test Estimate"
        assert data["estimate_type"] == "conceptual"
        assert data["status"] == "draft"

    async def test_list_estimates(self, client, auth_headers, test_project):
        """GET /api/v1/estimating/estimates should list project estimates."""
        # Create one first
        await client.post(
            "/api/v1/estimating/estimates",
            json={
                "project_id": str(test_project.id),
                "name": "List Test",
                "estimate_type": "conceptual",
            },
            headers=auth_headers,
        )
        response = await client.get(
            f"/api/v1/estimating/estimates?project_id={test_project.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "meta" in data
        assert len(data["data"]) >= 1

    async def test_get_estimate(self, client, auth_headers, test_project):
        """GET /api/v1/estimating/estimates/:id should return the estimate."""
        create_response = await client.post(
            "/api/v1/estimating/estimates",
            json={
                "project_id": str(test_project.id),
                "name": "Get Test",
                "estimate_type": "detailed",
            },
            headers=auth_headers,
        )
        estimate_id = create_response.json()["id"]

        response = await client.get(
            f"/api/v1/estimating/estimates/{estimate_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Get Test"

    async def test_get_estimate_not_found(self, client, auth_headers):
        """GET /api/v1/estimating/estimates/:id with invalid ID should return 404."""
        fake_id = uuid.uuid4()
        response = await client.get(
            f"/api/v1/estimating/estimates/{fake_id}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_create_estimate_invalid_type(self, client, auth_headers, test_project):
        """POST with invalid estimate_type should return 422."""
        response = await client.post(
            "/api/v1/estimating/estimates",
            json={
                "project_id": str(test_project.id),
                "name": "Bad Type",
                "estimate_type": "invalid_type",
            },
            headers=auth_headers,
        )
        assert response.status_code == 422
