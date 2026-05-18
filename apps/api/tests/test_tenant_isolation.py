from __future__ import annotations

from app.services.tenant.provisioner import TenantProvisioner


class TestTenantIsolation:
    async def test_create_separate_tenants(self):
        """Two tenants get different org_ids."""
        provisioner = TenantProvisioner()
        t1 = await provisioner.create_tenant("Acme Construction")
        t2 = await provisioner.create_tenant("BuildCo Inc")
        assert t1["org_id"] != t2["org_id"]

    async def test_tenant_has_status(self):
        provisioner = TenantProvisioner()
        t = await provisioner.create_tenant("TestCo")
        assert t["status"] == "active"

    async def test_tenant_deletion(self):
        provisioner = TenantProvisioner()
        t = await provisioner.create_tenant("DeleteMe")
        result = await provisioner.delete_tenant(t["org_id"])
        assert result["status"] == "deleted"
        assert result["audit_log_created"] is True

    async def test_verify_deletion(self):
        provisioner = TenantProvisioner()
        t = await provisioner.create_tenant("VerifyMe")
        await provisioner.delete_tenant(t["org_id"])
        verify = await provisioner.verify_deletion(t["org_id"])
        assert verify["verified"] is True
        assert verify["remaining_rows"] == 0

    async def test_deletion_cleans_tables(self):
        provisioner = TenantProvisioner()
        t = await provisioner.create_tenant("CleanMe")
        result = await provisioner.delete_tenant(t["org_id"])
        assert "projects" in result["tables_cleaned"]
        assert "documents" in result["tables_cleaned"]
