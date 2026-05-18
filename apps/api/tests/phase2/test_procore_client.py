"""Phase 2: Procore API client tests.

Tests for the Procore mock client that returns simulated project management
data. No real API calls are made; the client returns mock data internally.
"""

from __future__ import annotations

from app.services.procurement.procore_client import ProcoreClient


class TestProcoreClient:
    """Tests for the Procore API client."""

    async def test_get_projects(self):
        """Should return a list of projects for a company."""
        client = ProcoreClient()
        result = await client.get_projects("company-1")
        assert isinstance(result, list)
        assert len(result) > 0
        assert all("id" in p for p in result)
        assert all("name" in p for p in result)

    async def test_get_rfis(self):
        """Should return a list of RFIs for a project."""
        client = ProcoreClient()
        result = await client.get_rfis("project-1")
        assert isinstance(result, list)
        assert len(result) > 0
        assert all("subject" in r for r in result)
        assert all("status" in r for r in result)

    async def test_get_submittals(self):
        """Should return a list of submittals for a project."""
        client = ProcoreClient()
        result = await client.get_submittals("project-1")
        assert isinstance(result, list)
        assert len(result) > 0
        assert all("title" in s for s in result)

    async def test_get_change_orders(self):
        """Should return a list of change orders for a project."""
        client = ProcoreClient()
        result = await client.get_change_orders("project-1")
        assert isinstance(result, list)
        assert len(result) > 0
        assert all("cost_impact" in co for co in result)

    async def test_sync_cost_data(self):
        """Should return a sync summary with item counts."""
        client = ProcoreClient()
        result = await client.sync_cost_data("project-1")
        assert "items_synced" in result or "status" in result
        # sync_cost_data returns items_synced dict
        assert result["status"] == "completed"

    async def test_get_budget(self):
        """Should return budget data with cost codes."""
        client = ProcoreClient()
        result = await client.get_budget("project-1")
        assert "original_budget" in result
        assert "cost_codes" in result
        assert result["original_budget"] > 0
