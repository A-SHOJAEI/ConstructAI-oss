"""Phase 3: Zone API endpoint tests.

Tests for the safety zone CRUD REST API endpoints including creation,
listing, update, and deletion.
"""

from __future__ import annotations

import uuid

import pytest_asyncio

from app.models.camera import Camera
from app.models.project import Project


@pytest_asyncio.fixture
async def test_project(db_session, test_org):
    """Create a test project for zone API tests."""
    project = Project(name="Zone Test Project", org_id=test_org.id)
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


@pytest_asyncio.fixture
async def test_camera(db_session, test_project):
    """Create a test camera for zone API tests."""
    camera = Camera(
        project_id=test_project.id,
        name="Zone Test Camera",
        stream_url="rtsp://localhost:8554/zone-test",
    )
    db_session.add(camera)
    await db_session.flush()
    await db_session.refresh(camera)
    return camera


class TestZonesApi:
    """Tests for the zones API endpoints."""

    async def test_create_zone(self, client, auth_headers, test_project, test_camera):
        """POST /api/v1/zones/ should create a new safety zone."""
        response = await client.post(
            "/api/v1/zones/",
            json={
                "camera_id": str(test_camera.id),
                "project_id": str(test_project.id),
                "name": "Restricted Area",
                "zone_type": "restricted",
                "polygon_points": [[100, 100], [300, 100], [300, 400], [100, 400]],
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Restricted Area"
        assert data["zone_type"] == "restricted"
        assert data["is_active"] is True
        assert "id" in data

    async def test_list_zones(self, client, auth_headers, test_project, test_camera):
        """GET /api/v1/zones/ should list zones for a camera."""
        await client.post(
            "/api/v1/zones/",
            json={
                "camera_id": str(test_camera.id),
                "project_id": str(test_project.id),
                "name": "PPE Zone",
                "zone_type": "ppe_required",
                "polygon_points": [[0, 0], [640, 0], [640, 480], [0, 480]],
                "ppe_requirements": ["hardhat", "vest"],
            },
            headers=auth_headers,
        )
        response = await client.get(
            f"/api/v1/zones/?camera_id={test_camera.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert len(data["data"]) >= 1

    async def test_update_zone_polygon(self, client, auth_headers, test_project, test_camera):
        """PATCH /api/v1/zones/:id should update zone polygon points."""
        create_response = await client.post(
            "/api/v1/zones/",
            json={
                "camera_id": str(test_camera.id),
                "project_id": str(test_project.id),
                "name": "Update Test Zone",
                "zone_type": "restricted",
                "polygon_points": [[0, 0], [100, 0], [100, 100], [0, 100]],
            },
            headers=auth_headers,
        )
        zone_id = create_response.json()["id"]

        new_polygon = [[50, 50], [200, 50], [200, 200], [50, 200]]
        response = await client.patch(
            f"/api/v1/zones/{zone_id}",
            json={"polygon_points": new_polygon},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["polygon_points"] == new_polygon

    async def test_zone_types_validated(self, client, auth_headers, test_project, test_camera):
        """POST /api/v1/zones/ should reject invalid zone types."""
        response = await client.post(
            "/api/v1/zones/",
            json={
                "camera_id": str(test_camera.id),
                "project_id": str(test_project.id),
                "name": "Bad Zone",
                "zone_type": "invalid_type",
                "polygon_points": [[0, 0], [100, 0], [100, 100], [0, 100]],
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_delete_zone(self, client, auth_headers, test_project, test_camera):
        """DELETE /api/v1/zones/:id should remove the zone."""
        create_response = await client.post(
            "/api/v1/zones/",
            json={
                "camera_id": str(test_camera.id),
                "project_id": str(test_project.id),
                "name": "To Delete",
                "zone_type": "general",
                "polygon_points": [[0, 0], [50, 0], [50, 50], [0, 50]],
            },
            headers=auth_headers,
        )
        zone_id = create_response.json()["id"]
        response = await client.delete(
            f"/api/v1/zones/{zone_id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

    async def test_delete_zone_not_found(self, client, auth_headers):
        """DELETE /api/v1/zones/:id with invalid ID should return 404."""
        fake_id = uuid.uuid4()
        response = await client.delete(
            f"/api/v1/zones/{fake_id}",
            headers=auth_headers,
        )
        assert response.status_code == 404
