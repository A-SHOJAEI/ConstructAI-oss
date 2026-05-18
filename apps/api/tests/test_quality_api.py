"""API tests for quality management endpoints."""

from __future__ import annotations

import pytest

from app.models.project import Project


class TestQualityAPI:
    @pytest.fixture
    async def test_project(self, db_session, test_org):
        project = Project(
            name="Quality Test Project",
            org_id=test_org.id,
            status="active",
        )
        db_session.add(project)
        await db_session.flush()
        await db_session.refresh(project)
        return project

    async def test_create_inspection(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/quality/inspections",
            json={
                "project_id": str(test_project.id),
                "inspection_type": "concrete_placement",
                "location": "Level 3",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["inspection_type"] == ("concrete_placement")

    async def test_list_inspections(
        self,
        client,
        auth_headers,
        test_project,
    ):
        await client.post(
            "/api/v1/quality/inspections",
            json={
                "project_id": str(test_project.id),
                "inspection_type": "welding",
            },
            headers=auth_headers,
        )
        response = await client.get(
            "/api/v1/quality/inspections",
            params={
                "project_id": str(test_project.id),
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert len(response.json()["data"]) >= 1

    async def test_create_defect_report(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/quality/defects",
            json={
                "project_id": str(test_project.id),
                "defect_type": "crack_structural",
                "severity": "major",
                "description": "Crack in column C3",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["defect_type"] == "crack_structural"
        assert data["severity"] == "major"

    async def test_create_ncr(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/quality/ncrs",
            json={
                "project_id": str(test_project.id),
                "ncr_number": "NCR-001",
                "title": "Concrete below spec",
                "description": "3800 PSI vs 4000 required",
                "severity": "major",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["ncr_number"] == "NCR-001"

    async def test_list_ncrs(
        self,
        client,
        auth_headers,
        test_project,
    ):
        await client.post(
            "/api/v1/quality/ncrs",
            json={
                "project_id": str(test_project.id),
                "ncr_number": "NCR-002",
                "title": "Test NCR",
                "description": "Test desc",
            },
            headers=auth_headers,
        )
        response = await client.get(
            "/api/v1/quality/ncrs",
            params={
                "project_id": str(test_project.id),
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert len(response.json()["data"]) >= 1
