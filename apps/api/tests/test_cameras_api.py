"""Phase 3: Camera API endpoint tests.

Tests for the camera CRUD REST API endpoints including registration,
listing, update, and deletion.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from app.models.project import Project


@pytest.fixture(autouse=True)
def _bypass_stream_url_validation():
    """The cameras endpoints reject private/localhost RTSP hosts via SSRF
    checks. Tests use rtsp://localhost:8554/... which is invalid in prod
    but fine for fixtures, so disable the validator inside this module.
    """
    with patch("app.api.v1.cameras._validate_stream_url", lambda url: None):
        yield


@pytest_asyncio.fixture
async def test_project(db_session, test_org):
    """Create a test project for camera API tests."""
    project = Project(name="Vision Test Project", org_id=test_org.id)
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


class TestCamerasApi:
    """Tests for the cameras API endpoints."""

    async def test_register_camera(self, client, auth_headers, test_project):
        """POST /api/v1/cameras/ should register a new camera."""
        response = await client.post(
            "/api/v1/cameras/",
            json={
                "project_id": str(test_project.id),
                "name": "Camera 1",
                "stream_url": "rtsp://localhost:8554/cam1",
                "fps_setting": 5,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Camera 1"
        assert data["stream_url"] == "rtsp://localhost:8554/cam1"
        assert data["fps_setting"] == 5
        assert data["is_active"] is True
        assert "id" in data

    async def test_list_cameras(self, client, auth_headers, test_project):
        """GET /api/v1/cameras/ should list cameras for a project."""
        await client.post(
            "/api/v1/cameras/",
            json={
                "project_id": str(test_project.id),
                "name": "Cam A",
                "stream_url": "rtsp://localhost:8554/a",
            },
            headers=auth_headers,
        )
        response = await client.get(
            f"/api/v1/cameras/?project_id={test_project.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "meta" in data
        assert len(data["data"]) >= 1

    async def test_get_camera(self, client, auth_headers, test_project):
        """GET /api/v1/cameras/:id should return a single camera."""
        create_response = await client.post(
            "/api/v1/cameras/",
            json={
                "project_id": str(test_project.id),
                "name": "Get Test Cam",
                "stream_url": "rtsp://localhost:8554/get",
            },
            headers=auth_headers,
        )
        cam_id = create_response.json()["id"]
        response = await client.get(
            f"/api/v1/cameras/{cam_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Get Test Cam"

    async def test_get_camera_not_found(self, client, auth_headers):
        """GET /api/v1/cameras/:id with invalid ID should return 404."""
        fake_id = uuid.uuid4()
        response = await client.get(
            f"/api/v1/cameras/{fake_id}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_update_camera(self, client, auth_headers, test_project):
        """PATCH /api/v1/cameras/:id should update camera fields."""
        create_response = await client.post(
            "/api/v1/cameras/",
            json={
                "project_id": str(test_project.id),
                "name": "Old Name",
                "stream_url": "rtsp://localhost:8554/x",
            },
            headers=auth_headers,
        )
        cam_id = create_response.json()["id"]
        response = await client.patch(
            f"/api/v1/cameras/{cam_id}",
            json={"name": "New Name"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

    async def test_delete_camera(self, client, auth_headers, test_project):
        """DELETE /api/v1/cameras/:id should remove the camera."""
        create_response = await client.post(
            "/api/v1/cameras/",
            json={
                "project_id": str(test_project.id),
                "name": "To Delete",
                "stream_url": "rtsp://localhost:8554/del",
            },
            headers=auth_headers,
        )
        cam_id = create_response.json()["id"]
        response = await client.delete(
            f"/api/v1/cameras/{cam_id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        # Verify it's gone
        get_response = await client.get(
            f"/api/v1/cameras/{cam_id}",
            headers=auth_headers,
        )
        assert get_response.status_code == 404
