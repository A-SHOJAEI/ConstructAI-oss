"""API tests for communication endpoints."""

from __future__ import annotations

import pytest
from app.models.project import Project


class TestCommunicationAPI:
    @pytest.fixture
    async def test_project(self, db_session, test_org):
        project = Project(
            name="Communication Test Project",
            org_id=test_org.id,
            status="active",
        )
        db_session.add(project)
        await db_session.flush()
        await db_session.refresh(project)
        return project

    async def test_create_daily_report(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/communication/daily-reports",
            json={
                "project_id": str(test_project.id),
                "report_date": "2024-06-15",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "draft"
        assert data["content_markdown"] is not None

    async def test_list_daily_reports(
        self,
        client,
        auth_headers,
        test_project,
    ):
        await client.post(
            "/api/v1/communication/daily-reports",
            json={
                "project_id": str(test_project.id),
                "report_date": "2024-06-15",
            },
            headers=auth_headers,
        )
        response = await client.get(
            "/api/v1/communication/daily-reports",
            params={
                "project_id": str(test_project.id),
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert len(response.json()["data"]) >= 1

    async def test_create_meeting(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/communication/meetings",
            json={
                "project_id": str(test_project.id),
                "meeting_type": "weekly_progress",
                "meeting_date": "2024-06-15",
                "title": "Weekly Progress #24",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["meeting_type"] == "weekly_progress"

    async def test_create_rfi(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/communication/rfis",
            json={
                "project_id": str(test_project.id),
                "rfi_number": "RFI-042",
                "subject": "Column detail",
                "question": "What rebar size for B3?",
                "priority": "high",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["rfi_number"] == "RFI-042"
        assert data["ai_suggested_response"] is not None

    async def test_create_submittal(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/communication/submittals",
            json={
                "project_id": str(test_project.id),
                "submittal_number": "SUB-018",
                "title": "Steel Shop Drawings",
                "spec_section": "05120",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["submittal_number"] == "SUB-018"

    async def test_list_rfis(
        self,
        client,
        auth_headers,
        test_project,
    ):
        await client.post(
            "/api/v1/communication/rfis",
            json={
                "project_id": str(test_project.id),
                "rfi_number": "RFI-043",
                "subject": "Test",
                "question": "Test question",
            },
            headers=auth_headers,
        )
        response = await client.get(
            "/api/v1/communication/rfis",
            params={
                "project_id": str(test_project.id),
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert len(response.json()["data"]) >= 1
