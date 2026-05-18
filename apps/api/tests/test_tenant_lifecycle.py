from __future__ import annotations

from app.services.tenant.config_manager import TenantConfigManager
from app.services.tenant.provisioner import TenantProvisioner
from app.services.tenant.usage_meter import UsageMeter


class TestTenantLifecycle:
    async def test_create_tenant(self):
        p = TenantProvisioner()
        result = await p.create_tenant(
            "TestCorp",
            "growth",
            "admin@test.com",
        )
        assert result["org_name"] == "TestCorp"
        assert result["billing_plan"] == "growth"
        assert result["status"] == "active"

    async def test_configure_tenant(self):
        mgr = TenantConfigManager()
        config = await mgr.update_config(
            "org-1",
            {"billing_plan": "enterprise"},
        )
        assert config["billing_plan"] == "enterprise"
        retrieved = await mgr.get_config("org-1")
        assert retrieved["billing_plan"] == "enterprise"

    async def test_meter_usage(self):
        meter = UsageMeter()
        await meter.record("org-1", "api_calls", 50)
        await meter.record("org-1", "api_calls", 30)
        usage = await meter.get_usage("org-1")
        assert usage["api_calls"] == 80

    async def test_check_limit_within(self):
        meter = UsageMeter()
        await meter.record("org-1", "api_calls", 5000)
        within, pct = await meter.check_limit(
            "org-1",
            "api_calls",
            "growth",
        )
        assert within is True
        assert pct == 5.0

    async def test_delete_and_verify(self):
        p = TenantProvisioner()
        t = await p.create_tenant("DeleteCorp")
        delete_result = await p.delete_tenant(t["org_id"])
        assert delete_result["status"] == "deleted"
        verify = await p.verify_deletion(t["org_id"])
        assert verify["verified"] is True
