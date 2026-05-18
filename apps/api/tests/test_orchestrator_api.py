"""Tests for Orchestrator API endpoints."""

from __future__ import annotations

import uuid

import pytest_asyncio

from app.models.project import Project
from tests.conftest import *  # noqa: F403


@pytest_asyncio.fixture
async def test_project(db_session, test_org):
    """Create a test project for orchestrator API tests."""
    project = Project(name="Orchestrator Test Project", org_id=test_org.id)
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


class TestOrchestratorAPI:
    async def test_start_workflow(self, client, auth_headers, test_project):
        response = await client.post(
            "/api/v1/orchestrator/workflows",
            json={
                "workflow_type": "new_project_onboarding",
                "project_id": str(test_project.id),
                "input_data": {"document_ids": []},
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["workflow_type"] == "new_project_onboarding"
        assert data["status"] in (
            "completed",
            "running",
            "waiting_human",
        )

    async def test_start_workflow_wrong_project(self, client, auth_headers):
        """Workflow on a non-existent project should return 404."""
        response = await client.post(
            "/api/v1/orchestrator/workflows",
            json={
                "workflow_type": "new_project_onboarding",
                "project_id": str(uuid.uuid4()),
                "input_data": {"document_ids": []},
            },
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_list_workflows(self, client, auth_headers):
        response = await client.get(
            "/api/v1/orchestrator/workflows",
            headers=auth_headers,
        )
        assert response.status_code == 200

    async def test_route_event(self, client, auth_headers, test_project):
        response = await client.post(
            "/api/v1/orchestrator/events/route",
            json={
                "event_type": ("constructai.safety.incident_detected"),
                "project_id": str(test_project.id),
                "source_agent": "safety_agent",
                "priority": 1,
                "data": {"severity": "critical"},
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["routed_to"] == "safety_incident_response"
        assert data["priority"] == 1

    async def test_route_unknown_event(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/orchestrator/events/route",
            json={
                "event_type": "unknown.event.type",
                "project_id": str(test_project.id),
                "source_agent": "test",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["routed_to"] == "none"
