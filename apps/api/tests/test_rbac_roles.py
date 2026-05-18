"""Additional RBAC tests for org_admin and project_manager roles.

Complements test_rbac.py with more granular role-specific permission checks
for org_admin and project_manager roles.
"""

from app.services.security.rbac import PERMISSION_MATRIX, RBACEnforcer, Role


class TestOrgAdminPermissions:
    """org_admin (ORG_ADMIN) has '*' wildcard — full access within org."""

    def setup_method(self):
        self.enforcer = RBACEnforcer()

    def test_org_admin_has_wildcard(self):
        """ORG_ADMIN role should have the global wildcard '*'."""
        assert "*" in PERMISSION_MATRIX[Role.ORG_ADMIN]

    def test_org_admin_can_manage_projects(self):
        """org_admin can create, read, update, delete projects."""
        for action in ("create", "read", "update", "delete"):
            assert self.enforcer.check_permission("org_admin", f"projects:{action}")

    def test_org_admin_can_manage_users(self):
        """org_admin can manage users (create, read, update, delete)."""
        for action in ("create", "read", "update", "delete"):
            assert self.enforcer.check_permission("org_admin", f"users:{action}")

    def test_org_admin_can_manage_all_resources(self):
        """org_admin should be able to access any resource:action combo."""
        resources = [
            "documents",
            "estimates",
            "schedules",
            "safety",
            "quality",
            "rfis",
            "submittals",
            "procurement",
        ]
        for resource in resources:
            assert self.enforcer.check_permission("org_admin", f"{resource}:create")
            assert self.enforcer.check_permission("org_admin", f"{resource}:read")
            assert self.enforcer.check_permission("org_admin", f"{resource}:delete")

    def test_org_admin_via_legacy_name(self):
        """'platform_admin' legacy name should map to org_admin."""
        assert self.enforcer.check_permission("platform_admin", "projects:create")
        assert self.enforcer.check_permission("platform_admin", "users:delete")

    def test_org_admin_can_access_audit(self):
        """org_admin should be able to read audit logs."""
        assert self.enforcer.check_permission("org_admin", "audit:read")

    def test_org_admin_can_manage_integrations(self):
        """org_admin should have full integration access."""
        assert self.enforcer.check_permission("org_admin", "integrations:create")
        assert self.enforcer.check_permission("org_admin", "integrations:delete")


class TestProjectManagerPermissions:
    """project_manager has broad project-level access but limited org-level."""

    def setup_method(self):
        self.enforcer = RBACEnforcer()

    def test_project_manager_can_read_projects(self):
        assert self.enforcer.check_permission("project_manager", "projects:read")

    def test_project_manager_can_update_projects(self):
        assert self.enforcer.check_permission("project_manager", "projects:update")

    def test_project_manager_cannot_create_projects(self):
        """project_manager has 'projects:read,update' — no create or delete."""
        assert not self.enforcer.check_permission("project_manager", "projects:create")

    def test_project_manager_cannot_delete_projects(self):
        assert not self.enforcer.check_permission("project_manager", "projects:delete")

    def test_project_manager_full_document_access(self):
        """project_manager has 'documents:*' — all operations allowed."""
        for action in ("read", "create", "update", "delete", "upload"):
            assert self.enforcer.check_permission("project_manager", f"documents:{action}")

    def test_project_manager_full_rfi_access(self):
        """project_manager has 'rfis:*'."""
        for action in ("read", "create", "update"):
            assert self.enforcer.check_permission("project_manager", f"rfis:{action}")

    def test_project_manager_can_read_members(self):
        """project_manager has 'members:read' only."""
        assert self.enforcer.check_permission("project_manager", "members:read")

    def test_project_manager_cannot_manage_members(self):
        """project_manager should not be able to create or delete members."""
        assert not self.enforcer.check_permission("project_manager", "members:create")
        assert not self.enforcer.check_permission("project_manager", "members:delete")

    def test_project_manager_cannot_manage_org_level_resources(self):
        """project_manager should not have org-level admin access."""
        # The org-level wildcard '*' should NOT be in project_manager permissions
        assert "*" not in PERMISSION_MATRIX[Role.PROJECT_MANAGER]

    def test_project_manager_schedule_access(self):
        """project_manager has 'schedules:*'."""
        assert self.enforcer.check_permission("project_manager", "schedules:read")
        assert self.enforcer.check_permission("project_manager", "schedules:update")

    def test_project_manager_change_order_limited(self):
        """project_manager has change_orders:read,create,update,submit but not approve."""
        assert self.enforcer.check_permission("project_manager", "change_orders:read")
        assert self.enforcer.check_permission("project_manager", "change_orders:create")
        assert self.enforcer.check_permission("project_manager", "change_orders:submit")
        # approve is not in the list for project_manager
        assert not self.enforcer.check_permission("project_manager", "change_orders:approve")


class TestProjectAdminPermissions:
    """project_admin has broad project-scoped access including member management."""

    def setup_method(self):
        self.enforcer = RBACEnforcer()

    def test_project_admin_can_manage_members(self):
        """project_admin has 'members:*'."""
        assert self.enforcer.check_permission("project_admin", "members:read")
        assert self.enforcer.check_permission("project_admin", "members:create")
        assert self.enforcer.check_permission("project_admin", "members:delete")

    def test_project_admin_can_manage_all_project_resources(self):
        """project_admin should have wildcard access on all project resources."""
        resources = [
            "documents",
            "estimates",
            "schedules",
            "change_orders",
            "pay_applications",
            "safety",
            "quality",
            "rfis",
        ]
        for resource in resources:
            assert self.enforcer.check_permission("project_admin", f"{resource}:create")
            assert self.enforcer.check_permission("project_admin", f"{resource}:delete")

    def test_project_admin_does_not_have_global_wildcard(self):
        """project_admin should NOT have '*' (that's org_admin only)."""
        assert "*" not in PERMISSION_MATRIX[Role.PROJECT_ADMIN]
