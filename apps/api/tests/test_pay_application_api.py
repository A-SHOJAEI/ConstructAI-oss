"""API tests for AIA G702/G703 pay applications."""

from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from app.models.project import Project
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture(scope="function")
async def test_project(db_session: AsyncSession, test_org):
    project = Project(
        name="Pay App Test Project",
        org_id=test_org.id,
        status="active",
        contract_value=Decimal("1000000.00"),
    )
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)
    return project


@pytest_asyncio.fixture(scope="function")
async def test_project_with_sov(client, auth_headers, test_project):
    """Project with SOV line items created."""
    resp = await client.post(
        "/api/v1/pay-applications/sov",
        json={
            "project_id": str(test_project.id),
            "line_items": [
                {"item_number": "1", "description": "Site Work", "scheduled_value": "200000"},
                {"item_number": "2", "description": "Concrete", "scheduled_value": "300000"},
                {
                    "item_number": "3",
                    "description": "Structural Steel",
                    "scheduled_value": "500000",
                },
            ],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return test_project


class TestSOV:
    @pytest.mark.asyncio
    async def test_create_sov_bulk(self, client, auth_headers, test_project):
        """Bulk create SOV line items."""
        resp = await client.post(
            "/api/v1/pay-applications/sov",
            json={
                "project_id": str(test_project.id),
                "line_items": [
                    {"item_number": "1", "description": "Foundation", "scheduled_value": "100000"},
                    {"item_number": "2", "description": "Framing", "scheduled_value": "200000"},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert len(data) == 2
        assert Decimal(data[0]["scheduled_value"]) == Decimal("100000.00")

    @pytest.mark.asyncio
    async def test_list_sov(self, client, auth_headers, test_project_with_sov):
        """List SOV items for a project."""
        resp = await client.get(
            f"/api/v1/pay-applications/sov?project_id={test_project_with_sov.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 3
        total = sum(Decimal(d["scheduled_value"]) for d in data)
        assert total == Decimal("1000000.00")


class TestPayApplication:
    @pytest.mark.asyncio
    async def test_create_pay_app(self, client, auth_headers, test_project_with_sov):
        """Create pay application with line items, verify G702 math."""
        # Get SOV items for sov_ids
        sov_resp = await client.get(
            f"/api/v1/pay-applications/sov?project_id={test_project_with_sov.id}",
            headers=auth_headers,
        )
        sov_items = sov_resp.json()["data"]

        resp = await client.post(
            "/api/v1/pay-applications",
            json={
                "project_id": str(test_project_with_sov.id),
                "period_to": "2025-06-30",
                "retainage_pct": "10",
                "line_items": [
                    {
                        "sov_id": sov_items[0]["id"],
                        "item_number": "1",
                        "description_of_work": "Site Work",
                        "scheduled_value": "200000",
                        "work_completed_this_period": "50000",
                        "materials_presently_stored": "10000",
                    },
                    {
                        "sov_id": sov_items[1]["id"],
                        "item_number": "2",
                        "description_of_work": "Concrete",
                        "scheduled_value": "300000",
                        "work_completed_this_period": "75000",
                        "materials_presently_stored": "15000",
                    },
                    {
                        "sov_id": sov_items[2]["id"],
                        "item_number": "3",
                        "description_of_work": "Structural Steel",
                        "scheduled_value": "500000",
                        "work_completed_this_period": "0",
                        "materials_presently_stored": "25000",
                    },
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()

        # Verify G702 fields
        assert data["application_number"] == 1
        assert Decimal(data["original_contract_sum"]) == Decimal("1000000.00")
        assert Decimal(data["net_change_by_cos"]) == Decimal("0.00")
        assert Decimal(data["contract_sum_to_date"]) == Decimal("1000000.00")

        # total_completed = (50000+10000) + (75000+15000) + (0+25000) = 175000
        assert Decimal(data["total_completed_and_stored"]) == Decimal("175000.00")

        # retainage on work = (50000+75000+0)*0.10 = 12500
        assert Decimal(data["retainage_work_completed"]) == Decimal("12500.00")
        # retainage on stored = (10000+15000+25000)*0.10 = 5000
        assert Decimal(data["retainage_stored_materials"]) == Decimal("5000.00")
        assert Decimal(data["total_retainage"]) == Decimal("17500.00")

        # earned less retainage = 175000 - 17500 = 157500
        assert Decimal(data["total_earned_less_retainage"]) == Decimal("157500.00")
        # current payment = 157500 - 0 = 157500
        assert Decimal(data["current_payment_due"]) == Decimal("157500.00")
        # balance = 1000000 - 175000 + 17500 = 842500
        assert Decimal(data["balance_to_finish_including_retainage"]) == Decimal("842500.00")

        # Verify line items
        assert len(data["line_items"]) == 3
        li1 = data["line_items"][0]
        assert Decimal(li1["total_completed_and_stored"]) == Decimal("60000.00")
        assert Decimal(li1["percent_complete"]) == Decimal("30.0000")
        assert Decimal(li1["balance_to_finish"]) == Decimal("140000.00")

    @pytest.mark.asyncio
    async def test_auto_populate_work_completed_previous(
        self, client, auth_headers, test_project_with_sov
    ):
        """Second pay app auto-fills Column D from first pay app's Column G."""
        sov_resp = await client.get(
            f"/api/v1/pay-applications/sov?project_id={test_project_with_sov.id}",
            headers=auth_headers,
        )
        sov_items = sov_resp.json()["data"]

        # Create first pay app
        resp1 = await client.post(
            "/api/v1/pay-applications",
            json={
                "project_id": str(test_project_with_sov.id),
                "period_to": "2025-06-30",
                "retainage_pct": "10",
                "line_items": [
                    {
                        "sov_id": sov_items[0]["id"],
                        "item_number": "1",
                        "description_of_work": "Site Work",
                        "scheduled_value": "200000",
                        "work_completed_this_period": "40000",
                    },
                    {
                        "sov_id": sov_items[1]["id"],
                        "item_number": "2",
                        "description_of_work": "Concrete",
                        "scheduled_value": "300000",
                        "work_completed_this_period": "60000",
                    },
                    {
                        "sov_id": sov_items[2]["id"],
                        "item_number": "3",
                        "description_of_work": "Steel",
                        "scheduled_value": "500000",
                        "work_completed_this_period": "0",
                    },
                ],
            },
            headers=auth_headers,
        )
        assert resp1.status_code == 201
        pay_app_1_id = resp1.json()["id"]

        # Submit and certify first pay app
        await client.post(
            f"/api/v1/pay-applications/{pay_app_1_id}/submit",
            headers=auth_headers,
        )
        await client.post(
            f"/api/v1/pay-applications/{pay_app_1_id}/certify",
            headers=auth_headers,
        )

        # Create second pay app — Column D should be auto-populated
        resp2 = await client.post(
            "/api/v1/pay-applications",
            json={
                "project_id": str(test_project_with_sov.id),
                "period_to": "2025-07-31",
                "retainage_pct": "10",
                "line_items": [
                    {
                        "sov_id": sov_items[0]["id"],
                        "item_number": "1",
                        "description_of_work": "Site Work",
                        "scheduled_value": "200000",
                        "work_completed_this_period": "30000",
                    },
                    {
                        "sov_id": sov_items[1]["id"],
                        "item_number": "2",
                        "description_of_work": "Concrete",
                        "scheduled_value": "300000",
                        "work_completed_this_period": "50000",
                    },
                    {
                        "sov_id": sov_items[2]["id"],
                        "item_number": "3",
                        "description_of_work": "Steel",
                        "scheduled_value": "500000",
                        "work_completed_this_period": "100000",
                    },
                ],
            },
            headers=auth_headers,
        )
        assert resp2.status_code == 201
        data2 = resp2.json()

        assert data2["application_number"] == 2
        # Column D for line 1 should be 40000 (from first pay app's Column G)
        li1 = data2["line_items"][0]
        assert Decimal(li1["work_completed_previous"]) == Decimal("40000.00")
        # Column G for line 1 = 40000 + 30000 = 70000
        assert Decimal(li1["total_completed_and_stored"]) == Decimal("70000.00")

        # less_previous_certificates should come from first pay app
        assert Decimal(data2["less_previous_certificates"]) == Decimal(
            resp1.json()["total_earned_less_retainage"]
        )

    @pytest.mark.asyncio
    async def test_pay_app_status_workflow(self, client, auth_headers, test_project_with_sov):
        """draft -> submitted -> certified."""
        sov_resp = await client.get(
            f"/api/v1/pay-applications/sov?project_id={test_project_with_sov.id}",
            headers=auth_headers,
        )
        sov_resp.json()["data"]

        resp = await client.post(
            "/api/v1/pay-applications",
            json={
                "project_id": str(test_project_with_sov.id),
                "period_to": "2025-06-30",
                "line_items": [
                    {
                        "item_number": "1",
                        "description_of_work": "Site",
                        "scheduled_value": "200000",
                    },
                    {
                        "item_number": "2",
                        "description_of_work": "Concrete",
                        "scheduled_value": "300000",
                    },
                    {
                        "item_number": "3",
                        "description_of_work": "Steel",
                        "scheduled_value": "500000",
                    },
                ],
            },
            headers=auth_headers,
        )
        pay_app_id = resp.json()["id"]
        assert resp.json()["status"] == "draft"

        # Submit
        resp = await client.post(
            f"/api/v1/pay-applications/{pay_app_id}/submit",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"
        assert resp.json()["submitted_at"] is not None

        # Certify
        resp = await client.post(
            f"/api/v1/pay-applications/{pay_app_id}/certify",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "certified"
        assert resp.json()["certified_at"] is not None

    @pytest.mark.asyncio
    async def test_get_pay_application(self, client, auth_headers, test_project_with_sov):
        """Get a pay application with full line items."""
        resp = await client.post(
            "/api/v1/pay-applications",
            json={
                "project_id": str(test_project_with_sov.id),
                "period_to": "2025-06-30",
                "line_items": [
                    {
                        "item_number": "1",
                        "description_of_work": "Work",
                        "scheduled_value": "100000",
                    },
                ],
            },
            headers=auth_headers,
        )
        pay_app_id = resp.json()["id"]

        resp = await client.get(
            f"/api/v1/pay-applications/{pay_app_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == pay_app_id
        assert len(data["line_items"]) == 1

    @pytest.mark.asyncio
    async def test_list_pay_applications(self, client, auth_headers, test_project_with_sov):
        """List pay applications for a project."""
        # Create two pay apps
        for period in ("2025-06-30", "2025-07-31"):
            await client.post(
                "/api/v1/pay-applications",
                json={
                    "project_id": str(test_project_with_sov.id),
                    "period_to": period,
                    "line_items": [
                        {
                            "item_number": "1",
                            "description_of_work": "Work",
                            "scheduled_value": "100000",
                        },
                    ],
                },
                headers=auth_headers,
            )

        resp = await client.get(
            f"/api/v1/pay-applications?project_id={test_project_with_sov.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_cannot_update_non_draft(self, client, auth_headers, test_project_with_sov):
        """Cannot update a submitted pay application."""
        resp = await client.post(
            "/api/v1/pay-applications",
            json={
                "project_id": str(test_project_with_sov.id),
                "period_to": "2025-06-30",
                "line_items": [
                    {
                        "item_number": "1",
                        "description_of_work": "Work",
                        "scheduled_value": "100000",
                    },
                ],
            },
            headers=auth_headers,
        )
        pay_app_id = resp.json()["id"]
        await client.post(f"/api/v1/pay-applications/{pay_app_id}/submit", headers=auth_headers)

        resp = await client.patch(
            f"/api/v1/pay-applications/{pay_app_id}",
            json={"period_to": "2025-08-31"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestPayApplicationPDF:
    @pytest.mark.asyncio
    async def test_g702_pdf_generation(self, client, auth_headers, test_project_with_sov):
        """GET pdf/g702 returns valid PDF bytes."""
        resp = await client.post(
            "/api/v1/pay-applications",
            json={
                "project_id": str(test_project_with_sov.id),
                "period_to": "2025-06-30",
                "contractor_info": {"name": "ABC Construction"},
                "architect_info": {"name": "XYZ Architects"},
                "line_items": [
                    {
                        "item_number": "1",
                        "description_of_work": "Site Work",
                        "scheduled_value": "200000",
                        "work_completed_this_period": "50000",
                    },
                ],
            },
            headers=auth_headers,
        )
        pay_app_id = resp.json()["id"]

        resp = await client.get(
            f"/api/v1/pay-applications/{pay_app_id}/pdf/g702",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content[:4] == b"%PDF"

    @pytest.mark.asyncio
    async def test_g703_pdf_generation(self, client, auth_headers, test_project_with_sov):
        """GET pdf/g703 returns valid PDF bytes."""
        resp = await client.post(
            "/api/v1/pay-applications",
            json={
                "project_id": str(test_project_with_sov.id),
                "period_to": "2025-06-30",
                "line_items": [
                    {
                        "item_number": "1",
                        "description_of_work": "Site",
                        "scheduled_value": "200000",
                        "work_completed_this_period": "50000",
                    },
                    {
                        "item_number": "2",
                        "description_of_work": "Concrete",
                        "scheduled_value": "300000",
                        "work_completed_this_period": "75000",
                    },
                ],
            },
            headers=auth_headers,
        )
        pay_app_id = resp.json()["id"]

        resp = await client.get(
            f"/api/v1/pay-applications/{pay_app_id}/pdf/g703",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content[:4] == b"%PDF"
