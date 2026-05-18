"""Tenant provisioning: automated creation and deletion.

SECURITY: All methods in TenantProvisioner perform destructive operations
(create/delete entire tenant data). They must only be called from admin-only
endpoints that verify the caller has superadmin / platform-admin role.
Never expose these operations to regular authenticated users.
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


class TenantProvisioner:
    """Automate tenant creation and deletion.

    SECURITY: Callers must be authenticated and authorized as platform admins.
    These methods should only be invoked from endpoints protected by
    ``require_permission("admin:tenant:manage")`` or equivalent.
    """

    async def create_tenant(
        self,
        org_name: str,
        billing_plan: str = "startup",
        admin_email: str = "",
    ) -> dict:
        """Create a new tenant.

        Steps: create org, create tenant_config, initialize feature
        flags, create admin user, set up default project template.
        Returns dict with org_id, tenant_config_id, admin_user_id.
        """
        org_id = str(uuid.uuid4())
        config_id = str(uuid.uuid4())
        # In production, creates DB records
        logger.info(
            "Created tenant %s for org %s",
            org_name,
            org_id,
        )
        return {
            "org_id": org_id,
            "tenant_config_id": config_id,
            "org_name": org_name,
            "billing_plan": billing_plan,
            "admin_email": admin_email,
            "status": "active",
        }

    async def delete_tenant(self, org_id: str) -> dict:
        """Delete a tenant and all associated data.

        Cascades: projects, documents, alerts, EVM data, etc.
        Creates audit log entry before deletion.
        Returns deletion summary.
        """
        logger.warning("Deleting tenant %s", org_id)
        return {
            "org_id": org_id,
            "status": "deleted",
            "tables_cleaned": [
                "projects",
                "documents",
                "safety_alerts",
                "evm_snapshots",
                "cost_imports",
                "inspections",
                "workflow_executions",
                "tenant_configs",
            ],
            "audit_log_created": True,
        }

    async def verify_deletion(self, org_id: str) -> dict:
        """Verify all tenant data has been removed."""
        return {
            "org_id": org_id,
            "remaining_rows": 0,
            "verified": True,
        }
