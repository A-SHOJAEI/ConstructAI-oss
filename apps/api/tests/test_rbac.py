from __future__ import annotations

from app.services.security.rbac import (
    PERMISSION_MATRIX,
    RBACEnforcer,
    Role,
)


class TestRBACPermissionMatrix:
    def test_permission_matrix_complete(self):
        """All 9 roles have defined permissions."""
        for role in Role:
            assert role in PERMISSION_MATRIX

    def test_nine_roles_defined(self):
        assert len(Role) == 9

    def test_platform_admin_full_access(self):
        enforcer = RBACEnforcer()
        assert enforcer.check_permission("platform_admin", "projects:create")
        assert enforcer.check_permission("platform_admin", "anything:whatever")

    def test_read_only_restricted(self):
        enforcer = RBACEnforcer()
        assert enforcer.check_permission("read_only", "projects:read")
        assert not enforcer.check_permission("read_only", "projects:create")
        assert not enforcer.check_permission("read_only", "projects:delete")

    def test_subcontractor_filtered(self):
        enforcer = RBACEnforcer()
        assert enforcer.check_permission("subcontractor", "documents:read_filtered")
        assert not enforcer.check_permission("subcontractor", "documents:upload")

    def test_safety_manager_safety_access(self):
        enforcer = RBACEnforcer()
        assert enforcer.check_permission("safety_manager", "safety:read")
        assert enforcer.check_permission("safety_manager", "cameras:read")

    def test_inspector_quality_access(self):
        """inspector maps to field_engineer via legacy role map."""
        enforcer = RBACEnforcer()
        assert enforcer.check_permission("inspector", "quality:read")
        assert enforcer.check_permission("inspector", "punch_lists:read")

    def test_unknown_role_denied(self):
        enforcer = RBACEnforcer()
        assert not enforcer.check_permission("unknown_role", "projects:read")

    def test_get_allowed_actions(self):
        enforcer = RBACEnforcer()
        actions = enforcer.get_allowed_actions("read_only")
        assert len(actions) > 0
        assert "projects:read" in actions or any("projects" in a for a in actions)

    def test_wildcard_resource(self):
        """Role with 'resource:*' can do any action on that resource."""
        enforcer = RBACEnforcer()
        assert enforcer.check_permission("general_contractor", "safety:read")
        assert enforcer.check_permission("general_contractor", "safety:create")
