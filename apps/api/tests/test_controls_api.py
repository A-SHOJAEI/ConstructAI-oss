"""API tests for project controls endpoints."""

from __future__ import annotations

import pytest
from app.models.project import Project


class TestControlsAPI:
    @pytest.fixture
    async def test_project(self, db_session, test_org):
        project = Project(
            name="Controls Test Project",
            org_id=test_org.id,
            status="active",
        )
        db_session.add(project)
        await db_session.flush()
        await db_session.refresh(project)
        return project

    async def test_create_evm_snapshot(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/controls/evm-snapshots",
            json={
                "project_id": str(test_project.id),
                "snapshot_date": "2024-06-15",
                "bac": "1000000",
                "pv": "500000",
                "ev": "450000",
                "ac": "480000",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["spi"] is not None
        assert data["cpi"] is not None
        assert float(data["spi"]) < 1.0

    async def test_list_evm_snapshots(
        self,
        client,
        auth_headers,
        test_project,
    ):
        # Create a snapshot first
        await client.post(
            "/api/v1/controls/evm-snapshots",
            json={
                "project_id": str(test_project.id),
                "snapshot_date": "2024-06-15",
                "bac": "1000000",
                "pv": "500000",
                "ev": "450000",
                "ac": "480000",
            },
            headers=auth_headers,
        )
        response = await client.get(
            "/api/v1/controls/evm-snapshots",
            params={
                "project_id": str(test_project.id),
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) >= 1

    async def test_create_change_order(
        self,
        client,
        auth_headers,
        test_project,
    ):
        response = await client.post(
            "/api/v1/controls/change-orders",
            json={
                "project_id": str(test_project.id),
                "co_number": "CO-001",
                "title": "Foundation redesign",
                "description": "Unexpected soil conditions",
                "change_type": "field_condition",
                "cost_impact": "150000",
                "schedule_impact_days": 21,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["ai_analysis"] is not None
        assert "risk_score" in data["ai_analysis"]

    async def test_list_change_orders(
        self,
        client,
        auth_headers,
        test_project,
    ):
        await client.post(
            "/api/v1/controls/change-orders",
            json={
                "project_id": str(test_project.id),
                "co_number": "CO-002",
                "title": "Test CO",
                "description": "Test",
                "change_type": "owner_directed",
            },
            headers=auth_headers,
        )
        response = await client.get(
            "/api/v1/controls/change-orders",
            params={
                "project_id": str(test_project.id),
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert len(response.json()["data"]) >= 1

    async def test_run_schedule_risk(
        self,
        client,
        auth_headers,
        test_project,
        db_session,
    ):
        # The schedule-risk endpoint short-circuits with 422 when the
        # baseline has no activities. Seed a small baseline + two activities
        # so the Monte Carlo run has something to simulate.
        from datetime import date

        from app.models.scheduling import ScheduleActivity, ScheduleBaseline

        baseline = ScheduleBaseline(
            project_id=test_project.id,
            name="Test Baseline",
            baseline_date=date.today(),
        )
        db_session.add(baseline)
        await db_session.flush()
        await db_session.refresh(baseline)

        for i, name in enumerate(("Foundation", "Framing", "Roof")):
            activity = ScheduleActivity(
                project_id=test_project.id,
                baseline_id=baseline.id,
                activity_code=f"A{i:03d}",
                name=name,
                duration_days=10 + i * 5,
                predecessors=[f"A{i - 1:03d}"] if i > 0 else [],
            )
            db_session.add(activity)
        await db_session.flush()

        response = await client.post(
            "/api/v1/controls/schedule-risk",
            json={
                "project_id": str(test_project.id),
                "baseline_id": str(baseline.id),
                "num_iterations": 500,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["p50_duration"] > 0
        assert data["p80_duration"] >= data["p50_duration"]
