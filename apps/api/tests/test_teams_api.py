"""Tests for the Teams API endpoints (/api/v1/teams/)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest_asyncio

from app.models.project import Project


@pytest_asyncio.fixture
async def test_project(db_session, test_org):
    """Create a real project so verify_project_access(project_id) succeeds."""
    project = Project(name="Teams Test", org_id=test_org.id, status="active")
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


# ── Authentication ────────────────────────────────────────────────────


async def test_run_execution_team_requires_auth(client):
    """Un-authed POST is rejected by CSRFMiddleware with 403 before the
    auth dependency runs."""
    response = await client.post(
        "/api/v1/teams/execution/run",
        json={"project_id": "00000000-0000-0000-0000-000000000001"},
    )
    assert response.status_code == 403


async def test_run_compliance_team_requires_auth(client):
    """Un-authed POST is rejected by CSRFMiddleware with 403 before the
    auth dependency runs."""
    response = await client.post(
        "/api/v1/teams/compliance/run",
        json={"project_id": "00000000-0000-0000-0000-000000000001"},
    )
    assert response.status_code == 403


# ── Input validation ──────────────────────────────────────────────────


async def test_run_execution_team_missing_project_id(client, auth_headers):
    """POST /teams/execution/run should return 422 without project_id."""
    response = await client.post(
        "/api/v1/teams/execution/run",
        json={},
        headers=auth_headers,
    )
    assert response.status_code == 422


async def test_run_compliance_team_missing_project_id(client, auth_headers):
    """POST /teams/compliance/run should return 422 without project_id."""
    response = await client.post(
        "/api/v1/teams/compliance/run",
        json={},
        headers=auth_headers,
    )
    assert response.status_code == 422


# ── Execution team happy path ─────────────────────────────────────────


async def test_run_execution_team_success(client, auth_headers, test_project):
    """POST /teams/execution/run should invoke the execution team agent."""
    mock_result = {
        "status": "completed",
        "task_type": "full",
        "final_report": {"safety_score": 0.95, "quality_score": 0.88},
    }

    with patch(
        "app.services.agents.execution_team.run_execution_team",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        response = await client.post(
            "/api/v1/teams/execution/run",
            json={
                "project_id": str(test_project.id),
                "request": "Run full analysis",
                "task_type": "full",
            },
            headers=auth_headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == str(test_project.id)
    assert data["status"] in ("completed", "unknown")


async def test_run_execution_team_default_task_type(client, auth_headers, test_project):
    """POST /teams/execution/run should default task_type to 'full'."""
    mock_result = {
        "status": "completed",
        "task_type": "full",
        "final_report": {},
    }

    with patch(
        "app.services.agents.execution_team.run_execution_team",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        response = await client.post(
            "/api/v1/teams/execution/run",
            json={"project_id": str(test_project.id)},
            headers=auth_headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["task_type"] == "full"


# ── Compliance team happy path ────────────────────────────────────────


async def test_run_compliance_team_success(client, auth_headers, test_project):
    """POST /teams/compliance/run should invoke the compliance team agent."""
    mock_result = {
        "status": "completed",
        "task_type": "audit",
        "final_report": {"compliance_score": 0.92, "issues_found": 2},
    }

    with patch(
        "app.services.agents.compliance_team.run_compliance_team",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        response = await client.post(
            "/api/v1/teams/compliance/run",
            json={
                "project_id": str(test_project.id),
                "request": "Run compliance audit",
                "task_type": "audit",
            },
            headers=auth_headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == str(test_project.id)
    assert data["status"] in ("completed", "unknown")


# ── Edge cases ────────────────────────────────────────────────────────


async def test_run_execution_team_agent_failure_returns_unknown(client, auth_headers, test_project):
    """If the agent returns no status, the endpoint should default to 'unknown'."""
    mock_result: dict = {}  # no status, no task_type, no final_report

    with patch(
        "app.services.agents.execution_team.run_execution_team",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        response = await client.post(
            "/api/v1/teams/execution/run",
            json={"project_id": str(test_project.id)},
            headers=auth_headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unknown"
    assert data["results"] == {}


async def test_run_compliance_team_with_empty_request(client, auth_headers, test_project):
    """Compliance team should work with an empty request string."""
    mock_result = {
        "status": "completed",
        "task_type": "full",
        "final_report": {"ok": True},
    }

    with patch(
        "app.services.agents.compliance_team.run_compliance_team",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        response = await client.post(
            "/api/v1/teams/compliance/run",
            json={
                "project_id": str(test_project.id),
                "request": "",
            },
            headers=auth_headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
