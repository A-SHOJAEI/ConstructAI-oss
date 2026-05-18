"""Phase 2: Scheduling API endpoint tests.

Tests for the scheduling REST API endpoints including baseline creation,
listing, and retrieval.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from app.models.project import Project


@pytest_asyncio.fixture
async def test_project(db_session, test_org):
    """Create a test project for scheduling API tests."""
    project = Project(name="Scheduling Test Project", org_id=test_org.id)
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


class TestSchedulingApi:
    """Tests for the scheduling API endpoints."""

    async def test_create_baseline(self, client, auth_headers, test_project):
        """POST /api/v1/scheduling/baselines should create a new baseline."""
        response = await client.post(
            "/api/v1/scheduling/baselines",
            json={
                "project_id": str(test_project.id),
                "activity_code": "A001",
                "name": "Site Preparation",
                "duration_days": 10,
                "start_date": "2025-03-01",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["version"] == 1

    async def test_list_baselines(self, client, auth_headers, test_project):
        """GET /api/v1/scheduling/baselines should list project baselines."""
        await client.post(
            "/api/v1/scheduling/baselines",
            json={
                "project_id": str(test_project.id),
                "activity_code": "A001",
                "name": "Foundation",
                "duration_days": 20,
                "start_date": "2025-03-01",
            },
            headers=auth_headers,
        )
        response = await client.get(
            f"/api/v1/scheduling/baselines?project_id={test_project.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "meta" in data
        assert len(data["data"]) >= 1

    async def test_get_baseline(self, client, auth_headers, test_project):
        """GET /api/v1/scheduling/baselines/:id should return the baseline."""
        create_response = await client.post(
            "/api/v1/scheduling/baselines",
            json={
                "project_id": str(test_project.id),
                "activity_code": "A001",
                "name": "Structural Steel",
                "duration_days": 30,
                "start_date": "2025-04-01",
            },
            headers=auth_headers,
        )
        baseline_id = create_response.json()["id"]

        response = await client.get(
            f"/api/v1/scheduling/baselines/{baseline_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200

    async def test_get_baseline_not_found(self, client, auth_headers):
        """GET /api/v1/scheduling/baselines/:id with invalid ID should return 404."""
        fake_id = uuid.uuid4()
        response = await client.get(
            f"/api/v1/scheduling/baselines/{fake_id}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_create_baseline_increments_version(self, client, auth_headers, test_project):
        """Creating multiple baselines should auto-increment the version number."""
        await client.post(
            "/api/v1/scheduling/baselines",
            json={
                "project_id": str(test_project.id),
                "activity_code": "A001",
                "name": "First Activity",
                "duration_days": 10,
                "start_date": "2025-03-01",
            },
            headers=auth_headers,
        )
        response = await client.post(
            "/api/v1/scheduling/baselines",
            json={
                "project_id": str(test_project.id),
                "activity_code": "A002",
                "name": "Second Activity",
                "duration_days": 15,
                "start_date": "2025-03-15",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["version"] == 2
