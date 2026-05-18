"""API tests for PCO -> COR -> CO lifecycle."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from app.models.project import Project
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture(scope="function")
async def test_project(db_session: AsyncSession, test_org):
    project = Project(
        name="Lifecycle Test Project",
        org_id=test_org.id,
        status="active",
        contract_value=Decimal("1000000.00"),
    )
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


class TestPCOLifecycle:
    @pytest.mark.asyncio
    async def test_create_pco(self, client, auth_headers, test_project):
        """Create a PCO with cost breakdown and AI analysis."""
        response = await client.post(
            "/api/v1/controls/pcos",
            json={
                "project_id": str(test_project.id),
                "title": "Unforeseen rock",
                "description": "Hit rock during excavation requiring extra work",
                "change_type": "unforeseen_condition",
                "cost_breakdown": {
                    "labor_cost": "5000",
                    "material_cost": "2000",
                    "equipment_cost": "8000",
                    "subcontractor_cost": "0",
                    "overhead_cost": "1500",
                    "profit_markup_pct": "10",
                },
                "schedule_impact_days": 5,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["pco_number"] == 1
        assert data["status"] == "draft"
        # total = (5000+2000+8000+0+1500) * 1.10 = 18150
        assert Decimal(data["total_cost"]) == Decimal("18150.00")
        assert data["ai_analysis"] is not None
        assert data["change_type"] == "unforeseen_condition"

    @pytest.mark.asyncio
    async def test_pco_auto_increment(self, client, auth_headers, test_project):
        """Second PCO gets pco_number=2."""
        for i in range(2):
            response = await client.post(
                "/api/v1/controls/pcos",
                json={
                    "project_id": str(test_project.id),
                    "title": f"PCO {i + 1}",
                    "description": "Test PCO",
                    "change_type": "field_condition",
                },
                headers=auth_headers,
            )
            assert response.status_code == 201

        # List and verify numbers
        response = await client.get(
            f"/api/v1/controls/pcos?project_id={test_project.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 2
        assert data[0]["pco_number"] == 1
        assert data[1]["pco_number"] == 2

    @pytest.mark.asyncio
    async def test_pco_status_transitions(self, client, auth_headers, test_project):
        """Test draft -> pending_review -> approved."""
        # Create
        resp = await client.post(
            "/api/v1/controls/pcos",
            json={
                "project_id": str(test_project.id),
                "title": "Status test",
                "description": "Testing transitions",
                "change_type": "owner_directed",
            },
            headers=auth_headers,
        )
        pco_id = resp.json()["id"]

        # draft -> pending_review
        resp = await client.patch(
            f"/api/v1/controls/pcos/{pco_id}",
            json={"status": "pending_review"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending_review"

        # pending_review -> approved
        resp = await client.patch(
            f"/api/v1/controls/pcos/{pco_id}",
            json={"status": "approved"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    @pytest.mark.asyncio
    async def test_pco_invalid_transition(self, client, auth_headers, test_project):
        """Cannot go from draft directly to approved."""
        resp = await client.post(
            "/api/v1/controls/pcos",
            json={
                "project_id": str(test_project.id),
                "title": "Invalid transition",
                "description": "Should fail",
                "change_type": "design_error",
            },
            headers=auth_headers,
        )
        pco_id = resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/controls/pcos/{pco_id}",
            json={"status": "approved"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_pco_get_by_id(self, client, auth_headers, test_project):
        """Get a PCO by ID."""
        resp = await client.post(
            "/api/v1/controls/pcos",
            json={
                "project_id": str(test_project.id),
                "title": "Get test",
                "description": "Get by ID",
                "change_type": "regulatory",
            },
            headers=auth_headers,
        )
        pco_id = resp.json()["id"]

        resp = await client.get(
            f"/api/v1/controls/pcos/{pco_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == pco_id

    @pytest.mark.asyncio
    async def test_pco_not_found(self, client, auth_headers):
        """404 for nonexistent PCO."""
        resp = await client.get(
            f"/api/v1/controls/pcos/{uuid.uuid4()}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestCORLifecycle:
    @pytest.mark.asyncio
    async def test_create_cor_from_approved_pcos(self, client, auth_headers, test_project):
        """Create COR from 2 approved PCOs, verify aggregated cost."""
        pco_ids = []
        for i in range(2):
            resp = await client.post(
                "/api/v1/controls/pcos",
                json={
                    "project_id": str(test_project.id),
                    "title": f"PCO for COR {i + 1}",
                    "description": "Test",
                    "change_type": "field_condition",
                    "cost_breakdown": {
                        "labor_cost": "10000",
                        "material_cost": "5000",
                        "profit_markup_pct": "0",
                    },
                },
                headers=auth_headers,
            )
            pco_id = resp.json()["id"]
            # Approve: draft -> pending_review -> approved
            await client.patch(
                f"/api/v1/controls/pcos/{pco_id}",
                json={"status": "pending_review"},
                headers=auth_headers,
            )
            await client.patch(
                f"/api/v1/controls/pcos/{pco_id}",
                json={"status": "approved"},
                headers=auth_headers,
            )
            pco_ids.append(pco_id)

        # Create COR
        resp = await client.post(
            "/api/v1/controls/cors",
            json={
                "project_id": str(test_project.id),
                "title": "COR from 2 PCOs",
                "pco_ids": pco_ids,
                "markup_pct": "0",
                "overhead_pct": "0",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["cor_number"] == 1
        # Each PCO: labor 10000 + material 5000 = 15000, two PCOs = 30000
        assert Decimal(data["total_cost"]) == Decimal("30000.00")
        assert len(data["pco_ids"]) == 2

    @pytest.mark.asyncio
    async def test_create_cor_rejects_unapproved_pcos(self, client, auth_headers, test_project):
        """Cannot include draft PCOs in a COR."""
        resp = await client.post(
            "/api/v1/controls/pcos",
            json={
                "project_id": str(test_project.id),
                "title": "Draft PCO",
                "description": "Still draft",
                "change_type": "owner_directed",
            },
            headers=auth_headers,
        )
        pco_id = resp.json()["id"]

        resp = await client.post(
            "/api/v1/controls/cors",
            json={
                "project_id": str(test_project.id),
                "title": "Should fail",
                "pco_ids": [pco_id],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_approve_cor_creates_co(self, client, auth_headers, test_project):
        """Approving a COR creates a ChangeOrder and updates SOV."""
        # Create and approve PCO
        resp = await client.post(
            "/api/v1/controls/pcos",
            json={
                "project_id": str(test_project.id),
                "title": "Foundation rework",
                "description": "Rework needed",
                "change_type": "field_condition",
                "cost_breakdown": {
                    "labor_cost": "20000",
                    "material_cost": "10000",
                    "profit_markup_pct": "10",
                },
            },
            headers=auth_headers,
        )
        pco_id = resp.json()["id"]
        await client.patch(
            f"/api/v1/controls/pcos/{pco_id}",
            json={"status": "pending_review"},
            headers=auth_headers,
        )
        await client.patch(
            f"/api/v1/controls/pcos/{pco_id}",
            json={"status": "approved"},
            headers=auth_headers,
        )

        # Create COR
        resp = await client.post(
            "/api/v1/controls/cors",
            json={
                "project_id": str(test_project.id),
                "title": "Foundation rework COR",
                "pco_ids": [pco_id],
            },
            headers=auth_headers,
        )
        cor_id = resp.json()["id"]

        # Transition COR: draft -> submitted -> under_review
        await client.patch(
            f"/api/v1/controls/cors/{cor_id}",
            json={"status": "submitted"},
            headers=auth_headers,
        )
        await client.patch(
            f"/api/v1/controls/cors/{cor_id}",
            json={"status": "under_review"},
            headers=auth_headers,
        )

        # Approve COR -> creates CO
        resp = await client.post(
            f"/api/v1/controls/cors/{cor_id}/approve",
            headers=auth_headers,
        )
        assert resp.status_code == 201
        co_data = resp.json()
        assert co_data["status"] == "approved"
        assert co_data["cor_id"] == cor_id
        assert co_data["original_contract_sum"] is not None

        # Verify SOV was updated
        resp = await client.get(
            f"/api/v1/pay-applications/sov?project_id={test_project.id}",
            headers=auth_headers,
        )
        sov_data = resp.json()["data"]
        co_lines = [s for s in sov_data if s["is_change_order_line"]]
        assert len(co_lines) == 1
        assert co_lines[0]["change_order_id"] == co_data["id"]


class TestCumulativeImpact:
    @pytest.mark.asyncio
    async def test_cumulative_impact(self, client, auth_headers, test_project):
        """Verify cumulative CO impact after approval."""
        # Create, approve a PCO, create COR, approve COR -> CO
        resp = await client.post(
            "/api/v1/controls/pcos",
            json={
                "project_id": str(test_project.id),
                "title": "Impact test",
                "description": "Test",
                "change_type": "owner_directed",
                "cost_breakdown": {"labor_cost": "50000", "profit_markup_pct": "0"},
            },
            headers=auth_headers,
        )
        pco_id = resp.json()["id"]
        await client.patch(
            f"/api/v1/controls/pcos/{pco_id}",
            json={"status": "pending_review"},
            headers=auth_headers,
        )
        await client.patch(
            f"/api/v1/controls/pcos/{pco_id}", json={"status": "approved"}, headers=auth_headers
        )

        resp = await client.post(
            "/api/v1/controls/cors",
            json={"project_id": str(test_project.id), "title": "COR", "pco_ids": [pco_id]},
            headers=auth_headers,
        )
        cor_id = resp.json()["id"]
        await client.patch(
            f"/api/v1/controls/cors/{cor_id}", json={"status": "submitted"}, headers=auth_headers
        )
        await client.patch(
            f"/api/v1/controls/cors/{cor_id}", json={"status": "under_review"}, headers=auth_headers
        )
        await client.post(f"/api/v1/controls/cors/{cor_id}/approve", headers=auth_headers)

        # Check cumulative impact
        resp = await client.get(
            f"/api/v1/controls/change-orders/cumulative-impact?project_id={test_project.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_approved_cos"] == 1
        assert Decimal(str(data["total_cost_impact"])) == Decimal("50000.00")
        assert Decimal(str(data["original_contract_value"])) == Decimal("1000000.00")
        assert Decimal(str(data["current_contract_value"])) == Decimal("1050000.00")
        assert Decimal(str(data["percent_change"])) == Decimal("5.00")
