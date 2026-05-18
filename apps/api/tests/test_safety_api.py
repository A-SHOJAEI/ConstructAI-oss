"""Phase 3: Safety API endpoint tests.

Tests for the safety alert REST API endpoints including querying alerts,
acknowledging alerts, marking false positives, and retrieving stats.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest_asyncio

from app.models.camera import Camera
from app.models.project import Project
from app.models.safety_incident import SafetyAlert


@pytest_asyncio.fixture
async def test_project(db_session, test_org):
    """Create a test project for safety API tests."""
    project = Project(name="Safety Test Project", org_id=test_org.id)
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


@pytest_asyncio.fixture
async def test_camera(db_session, test_project):
    """Create a test camera for safety API tests."""
    camera = Camera(
        project_id=test_project.id,
        name="Safety Test Camera",
        stream_url="rtsp://localhost:8554/safety-test",
    )
    db_session.add(camera)
    await db_session.flush()
    await db_session.refresh(camera)
    return camera


@pytest_asyncio.fixture
async def test_alerts(db_session, test_project, test_camera):
    """Create test safety alerts directly in the database."""
    alerts = []
    alert_data = [
        {
            "priority": "P1_critical",
            "alert_type": "zone_breach",
            "description": "Person in restricted zone",
            "confidence": Decimal("0.95"),
        },
        {
            "priority": "P2_high",
            "alert_type": "ppe_violation",
            "description": "Missing hardhat detected",
            "confidence": Decimal("0.88"),
        },
        {
            "priority": "P2_high",
            "alert_type": "ppe_violation",
            "description": "Missing vest detected",
            "confidence": Decimal("0.85"),
        },
        {
            "priority": "P3_medium",
            "alert_type": "zone_breach",
            "description": "Person in equipment zone",
            "confidence": Decimal("0.80"),
        },
    ]
    for data in alert_data:
        alert = SafetyAlert(
            project_id=test_project.id,
            camera_id=test_camera.id,
            priority=data["priority"],
            alert_type=data["alert_type"],
            description=data["description"],
            detections=[],
            confidence=data["confidence"],
        )
        db_session.add(alert)
        alerts.append(alert)
    await db_session.flush()
    for alert in alerts:
        await db_session.refresh(alert)
    return alerts


class TestSafetyApi:
    """Tests for the safety API endpoints."""

    async def test_query_alerts(self, client, auth_headers, test_project, test_alerts):
        """GET /api/v1/safety/alerts should return alerts for a project."""
        response = await client.get(
            f"/api/v1/safety/alerts?project_id={test_project.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "total" in data
        assert data["total"] == 4
        assert len(data["data"]) == 4

    async def test_query_alerts_filter_by_priority(
        self, client, auth_headers, test_project, test_alerts
    ):
        """GET /api/v1/safety/alerts with priority filter should narrow results."""
        response = await client.get(
            f"/api/v1/safety/alerts?project_id={test_project.id}&priority=P1_critical",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["data"][0]["priority"] == "P1_critical"

    async def test_query_alerts_filter_by_type(
        self, client, auth_headers, test_project, test_alerts
    ):
        """GET /api/v1/safety/alerts with alert_type filter should narrow results."""
        response = await client.get(
            f"/api/v1/safety/alerts?project_id={test_project.id}&alert_type=ppe_violation",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

    async def test_get_single_alert(self, client, auth_headers, test_alerts):
        """GET /api/v1/safety/alerts/:id should return a single alert."""
        alert_id = str(test_alerts[0].id)
        response = await client.get(
            f"/api/v1/safety/alerts/{alert_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["id"] == alert_id

    async def test_get_alert_not_found(self, client, auth_headers):
        """GET /api/v1/safety/alerts/:id with invalid ID should return 404."""
        fake_id = uuid.uuid4()
        response = await client.get(
            f"/api/v1/safety/alerts/{fake_id}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_acknowledge_alert(self, client, auth_headers, test_alerts):
        """PATCH /api/v1/safety/alerts/:id/acknowledge should acknowledge the alert."""
        alert_id = str(test_alerts[0].id)
        response = await client.patch(
            f"/api/v1/safety/alerts/{alert_id}/acknowledge",
            json={"is_false_positive": False, "notes": "Reviewed and confirmed"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_acknowledged"] is True
        assert data["response_notes"] == "Reviewed and confirmed"
        assert data["acknowledged_at"] is not None

    async def test_mark_false_positive(self, client, auth_headers, test_alerts):
        """PATCH /api/v1/safety/alerts/:id/acknowledge with false_positive flag."""
        alert_id = str(test_alerts[1].id)
        response = await client.patch(
            f"/api/v1/safety/alerts/{alert_id}/acknowledge",
            json={"is_false_positive": True, "notes": "Misdetection - shadow"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_acknowledged"] is True
        assert data["is_false_positive"] is True

    async def test_safety_stats(self, client, auth_headers, test_project, test_alerts):
        """GET /api/v1/safety/stats should return aggregated statistics."""
        response = await client.get(
            f"/api/v1/safety/stats?project_id={test_project.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_alerts"] == 4
        assert "P1_critical" in data["alerts_by_priority"]
        assert "P2_high" in data["alerts_by_priority"]
        assert data["alerts_by_priority"]["P2_high"] == 2
        assert "ppe_violation" in data["alerts_by_type"]
        assert "zone_breach" in data["alerts_by_type"]
        assert data["acknowledged_count"] == 0
        assert data["false_positive_count"] == 0
        assert data["period"] == "all"

    async def test_safety_stats_empty_project(self, client, auth_headers, test_project):
        """GET /api/v1/safety/stats with no alerts should return zeros."""
        response = await client.get(
            f"/api/v1/safety/stats?project_id={test_project.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_alerts"] == 0
        assert data["acknowledged_count"] == 0
        assert data["false_positive_count"] == 0
