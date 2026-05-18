"""API tests for productivity tracking endpoints."""

from __future__ import annotations

import pytest

from app.models.project import Project


class TestProductivityAPI:
    @pytest.fixture
    async def test_project(self, db_session, test_org):
        project = Project(
            name="Productivity Test Project",
            org_id=test_org.id,
            status="active",
        )
        db_session.add(project)
        await db_session.flush()
        await db_session.refresh(project)
        return project

    async def test_create_daily_log(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/productivity/daily-logs",
            json={
                "project_id": str(test_project.id),
                "log_date": "2024-06-15",
                "crew_count": 45,
                "work_hours": "360",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["crew_count"] == 45

    async def test_list_daily_logs(
        self,
        client,
        auth_headers,
        test_project,
    ):
        await client.post(
            "/api/v1/productivity/daily-logs",
            json={
                "project_id": str(test_project.id),
                "log_date": "2024-06-15",
                "crew_count": 30,
            },
            headers=auth_headers,
        )
        response = await client.get(
            "/api/v1/productivity/daily-logs",
            params={
                "project_id": str(test_project.id),
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert len(response.json()["data"]) >= 1

    async def test_create_crew_productivity(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/productivity/crew-productivity",
            json={
                "project_id": str(test_project.id),
                "trade": "concrete",
                "crew_size": 8,
                "work_date": "2024-06-15",
                "planned_units": "150",
                "actual_units": "142",
                "unit_of_measure": "cubic_yards",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["trade"] == "concrete"
        assert data["pf_ratio"] is not None

    async def test_create_equipment_telemetry(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/productivity/equipment-telemetry",
            json={
                "project_id": str(test_project.id),
                "equipment_id": "CAT-336F-001",
                "equipment_type": "excavator",
                "timestamp": "2024-06-15T14:30:00Z",
                "engine_hours": "4521.5",
                "utilization_pct": "73.3",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["equipment_id"] == "CAT-336F-001"
