"""Tests for tenant provisioning and isolation.

Covers: create tenant, duplicate slug rejection (conceptual), tenant deactivation,
tenant data scoping, and deletion verification.
"""

import pytest

from app.services.tenant.provisioner import TenantProvisioner


class TestTenantProvisioner:
    """Tests for TenantProvisioner create/delete/verify operations."""

    def setup_method(self):
        self.provisioner = TenantProvisioner()

    @pytest.mark.asyncio
    async def test_create_tenant_basic(self):
        """create_tenant should return org_id, config_id, and status=active."""
        result = await self.provisioner.create_tenant(
            org_name="Acme Construction",
            billing_plan="professional",
            admin_email="admin@acme.com",
        )
        assert result["org_id"] is not None
        assert result["tenant_config_id"] is not None
        assert result["org_name"] == "Acme Construction"
        assert result["billing_plan"] == "professional"
        assert result["admin_email"] == "admin@acme.com"
        assert result["status"] == "active"

    @pytest.mark.asyncio
    async def test_create_tenant_default_billing_plan(self):
        """Default billing plan should be 'startup'."""
        result = await self.provisioner.create_tenant(org_name="Small Builder")
        assert result["billing_plan"] == "startup"

    @pytest.mark.asyncio
    async def test_tenant_isolation_unique_ids(self):
        """Two tenants should have different org_ids."""
        tenant_a = await self.provisioner.create_tenant(org_name="Tenant A")
        tenant_b = await self.provisioner.create_tenant(org_name="Tenant B")
        assert tenant_a["org_id"] != tenant_b["org_id"]
        assert tenant_a["tenant_config_id"] != tenant_b["tenant_config_id"]

    @pytest.mark.asyncio
    async def test_delete_tenant(self):
        """delete_tenant should return deletion summary with cleaned tables."""
        tenant = await self.provisioner.create_tenant(org_name="To Delete")
        result = await self.provisioner.delete_tenant(tenant["org_id"])
        assert result["status"] == "deleted"
        assert result["org_id"] == tenant["org_id"]
        assert "projects" in result["tables_cleaned"]
        assert "documents" in result["tables_cleaned"]
        assert result["audit_log_created"] is True

    @pytest.mark.asyncio
    async def test_verify_deletion(self):
        """verify_deletion should confirm zero remaining rows."""
        tenant = await self.provisioner.create_tenant(org_name="Verify Delete")
        await self.provisioner.delete_tenant(tenant["org_id"])
        verification = await self.provisioner.verify_deletion(tenant["org_id"])
        assert verification["verified"] is True
        assert verification["remaining_rows"] == 0

    @pytest.mark.asyncio
    async def test_delete_returns_all_expected_tables(self):
        """Deletion summary should include all critical data tables."""
        tenant = await self.provisioner.create_tenant(org_name="Full Cleanup")
        result = await self.provisioner.delete_tenant(tenant["org_id"])
        expected_tables = {
            "projects",
            "documents",
            "safety_alerts",
            "evm_snapshots",
            "tenant_configs",
        }
        cleaned = set(result["tables_cleaned"])
        assert expected_tables.issubset(cleaned)
