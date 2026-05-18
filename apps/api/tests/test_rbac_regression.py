"""RBAC regression tests.

Walks the full PERMISSION_MATRIX and verifies:
- Every resource referenced in route ``require_permission`` calls exists
  in the matrix for at least one non-admin role.
- Specific denial scenarios (READONLY cannot create, SUBCONTRACTOR is
  limited, etc.) to prevent accidental permission creep.
- Specific grant scenarios so tightening one role doesn't break another.
- Legacy role mappings still work.
- Edge cases in RBACEnforcer (malformed actions, empty strings).
"""

from __future__ import annotations

import pytest

from app.services.security.rbac import (
    _LEGACY_ROLE_MAP,
    PERMISSION_MATRIX,
    RBACEnforcer,
    Role,
)

# Singleton enforcer reused across all tests (stateless, so safe)
_enforcer = RBACEnforcer()


def _check(role: str, resource: str, action: str) -> bool:
    """Shorthand: check_permission using 'resource:action' format."""
    return _enforcer.check_permission(role, f"{resource}:{action}")


# ---------------------------------------------------------------------------
# Matrix completeness
# ---------------------------------------------------------------------------


class TestRBACMatrixCompleteness:
    """Verify every resource used in routes is in the RBAC matrix."""

    # All resource names extracted from require_permission() calls across
    # the route layer (app/api/v1/*.py).  If a new resource is added to a
    # route it MUST be added here AND to the PERMISSION_MATRIX.
    ROUTE_RESOURCES = [
        "projects",
        "documents",
        "estimates",
        "estimating",
        "schedules",
        "change_orders",
        "pay_applications",
        "safety",
        "quality",
        "rfis",
        "submittals",
        "daily_logs",
        "punch_lists",
        "drawings",
        "procurement",
        "reports",
        "cameras",
        "zones",
        "productivity",
        "members",
        "audit",
        "communication",
        "sub_portal",
        "users",
        "twins",
        "drones",
        "payroll",
        "insurance",
        "progress",
        "daily_reports",
        "field_data",
        "contracts",
        "insights",
        "integrations",
        "controls",
    ]

    def test_all_route_resources_in_matrix(self):
        """Every resource used in routes must exist in PERMISSION_MATRIX for at least one role."""
        for resource in self.ROUTE_RESOURCES:
            found = False
            for _role, perms in PERMISSION_MATRIX.items():
                for perm in perms:
                    if perm == "*" or perm.startswith(f"{resource}:"):
                        found = True
                        break
                if found:
                    break
            assert found, f"Resource '{resource}' not found in PERMISSION_MATRIX for any role"

    def test_every_resource_has_non_admin_role(self):
        """Every resource should be accessible by at least one non-ORG_ADMIN role."""
        for resource in self.ROUTE_RESOURCES:
            non_admin_found = False
            for role, perms in PERMISSION_MATRIX.items():
                if role == Role.ORG_ADMIN:
                    continue  # Skip the global wildcard role
                for perm in perms:
                    if perm.startswith(f"{resource}:") or perm == f"{resource}:*":
                        non_admin_found = True
                        break
                if non_admin_found:
                    break
            assert non_admin_found, (
                f"Resource '{resource}' only accessible by ORG_ADMIN -- no other role can use it"
            )

    def test_all_nine_roles_present(self):
        """The matrix must have an entry for every defined Role."""
        for role in Role:
            assert role in PERMISSION_MATRIX, f"Role {role.name} missing from PERMISSION_MATRIX"

    def test_org_admin_has_global_wildcard(self):
        """ORG_ADMIN must have the global '*' permission."""
        assert "*" in PERMISSION_MATRIX[Role.ORG_ADMIN]

    def test_no_empty_permission_sets(self):
        """No role should map to an empty permission set."""
        for role, perms in PERMISSION_MATRIX.items():
            assert len(perms) > 0, f"Role {role.name} has an empty permission set"


# ---------------------------------------------------------------------------
# Role denials -- verify least-privilege boundaries
# ---------------------------------------------------------------------------


class TestRoleDenials:
    """Test that roles are DENIED access they should not have."""

    # -- READONLY --------------------------------------------------------
    def test_readonly_cannot_create_projects(self):
        assert not _check("readonly", "projects", "create")

    def test_readonly_cannot_update_core_resources(self):
        for resource in ("projects", "documents", "estimates", "schedules"):
            assert not _check("readonly", resource, "update"), (
                f"READONLY should not update {resource}"
            )

    def test_readonly_cannot_delete_anything(self):
        for resource in (
            "projects",
            "documents",
            "estimates",
            "schedules",
            "rfis",
            "submittals",
            "cameras",
        ):
            assert not _check("readonly", resource, "delete"), (
                f"READONLY should not delete {resource}"
            )

    def test_readonly_cannot_create_anything(self):
        for resource in (
            "projects",
            "documents",
            "estimates",
            "schedules",
            "rfis",
            "submittals",
            "daily_logs",
        ):
            assert not _check("readonly", resource, "create"), (
                f"READONLY should not create {resource}"
            )

    # -- SUBCONTRACTOR ---------------------------------------------------
    def test_subcontractor_cannot_create_projects(self):
        assert not _check("subcontractor", "projects", "create")

    def test_subcontractor_cannot_update_projects(self):
        assert not _check("subcontractor", "projects", "update")

    def test_subcontractor_cannot_update_schedules(self):
        assert not _check("subcontractor", "schedules", "update")

    def test_subcontractor_cannot_create_change_orders(self):
        assert not _check("subcontractor", "change_orders", "create")

    def test_subcontractor_cannot_delete_documents(self):
        assert not _check("subcontractor", "documents", "delete")

    def test_subcontractor_cannot_access_payroll(self):
        assert not _check("subcontractor", "payroll", "read")
        assert not _check("subcontractor", "payroll", "create")

    def test_subcontractor_cannot_access_procurement(self):
        assert not _check("subcontractor", "procurement", "create")

    def test_subcontractor_cannot_manage_members(self):
        assert not _check("subcontractor", "members", "create")
        assert not _check("subcontractor", "members", "delete")

    # -- FIELD_ENGINEER --------------------------------------------------
    def test_field_engineer_cannot_create_audit(self):
        assert not _check("field_engineer", "audit", "create")

    def test_field_engineer_cannot_delete_members(self):
        assert not _check("field_engineer", "members", "delete")

    def test_field_engineer_cannot_update_projects(self):
        assert not _check("field_engineer", "projects", "update")

    def test_field_engineer_cannot_create_change_orders(self):
        assert not _check("field_engineer", "change_orders", "create")

    # -- SAFETY_MANAGER --------------------------------------------------
    def test_safety_manager_cannot_create_estimates(self):
        assert not _check("safety_manager", "estimates", "create")

    def test_safety_manager_cannot_update_schedules(self):
        assert not _check("safety_manager", "schedules", "update")

    def test_safety_manager_cannot_manage_pay_applications(self):
        assert not _check("safety_manager", "pay_applications", "create")
        assert not _check("safety_manager", "pay_applications", "update")

    # -- OWNER_REP -------------------------------------------------------
    def test_owner_rep_cannot_create_documents(self):
        assert not _check("owner_rep", "documents", "create")

    def test_owner_rep_cannot_update_schedules(self):
        assert not _check("owner_rep", "schedules", "update")

    def test_owner_rep_cannot_create_rfis(self):
        assert not _check("owner_rep", "rfis", "create")


# ---------------------------------------------------------------------------
# Role grants -- verify that intended access paths work
# ---------------------------------------------------------------------------


class TestRoleGrants:
    """Test that roles CAN access what they need."""

    # -- PROJECT_MANAGER -------------------------------------------------
    def test_project_manager_can_read_projects(self):
        assert _check("project_manager", "projects", "read")

    def test_project_manager_can_update_projects(self):
        assert _check("project_manager", "projects", "update")

    def test_project_manager_can_create_documents(self):
        assert _check("project_manager", "documents", "create")

    def test_project_manager_can_create_rfis(self):
        assert _check("project_manager", "rfis", "create")

    def test_project_manager_can_update_schedules(self):
        assert _check("project_manager", "schedules", "update")

    def test_project_manager_can_manage_procurement(self):
        assert _check("project_manager", "procurement", "read")
        assert _check("project_manager", "procurement", "create")

    def test_project_manager_can_submit_change_orders(self):
        assert _check("project_manager", "change_orders", "submit")

    def test_project_manager_can_read_insights(self):
        assert _check("project_manager", "insights", "read")

    # -- SUPERINTENDENT --------------------------------------------------
    def test_superintendent_can_create_daily_logs(self):
        assert _check("superintendent", "daily_logs", "create")

    def test_superintendent_can_create_punch_lists(self):
        assert _check("superintendent", "punch_lists", "create")

    def test_superintendent_can_create_safety(self):
        # Safety update (observations, reports)
        assert _check("superintendent", "safety", "update")

    def test_superintendent_can_create_field_data(self):
        assert _check("superintendent", "field_data", "create")

    def test_superintendent_can_create_daily_reports(self):
        assert _check("superintendent", "daily_reports", "create")

    def test_superintendent_can_create_rfis(self):
        assert _check("superintendent", "rfis", "create")

    def test_superintendent_can_create_drones(self):
        assert _check("superintendent", "drones", "create")

    # -- SUBCONTRACTOR ---------------------------------------------------
    def test_subcontractor_can_read_sub_portal(self):
        assert _check("subcontractor", "sub_portal", "read")

    def test_subcontractor_can_create_sub_portal(self):
        assert _check("subcontractor", "sub_portal", "create")

    def test_subcontractor_can_create_daily_logs(self):
        assert _check("subcontractor", "daily_logs", "create")

    def test_subcontractor_can_create_rfis(self):
        assert _check("subcontractor", "rfis", "create")

    def test_subcontractor_can_read_safety(self):
        assert _check("subcontractor", "safety", "read")

    # -- SAFETY_MANAGER --------------------------------------------------
    def test_safety_manager_full_safety_access(self):
        for action in ("read", "create", "update", "delete"):
            assert _check("safety_manager", "safety", action), (
                f"SAFETY_MANAGER should have safety:{action}"
            )

    def test_safety_manager_full_camera_access(self):
        for action in ("read", "create", "update", "delete"):
            assert _check("safety_manager", "cameras", action), (
                f"SAFETY_MANAGER should have cameras:{action}"
            )

    def test_safety_manager_full_zone_access(self):
        for action in ("read", "create", "update"):
            assert _check("safety_manager", "zones", action), (
                f"SAFETY_MANAGER should have zones:{action}"
            )

    # -- OWNER_REP -------------------------------------------------------
    def test_owner_rep_can_approve_change_orders(self):
        assert _check("owner_rep", "change_orders", "approve")

    def test_owner_rep_can_approve_pay_applications(self):
        assert _check("owner_rep", "pay_applications", "approve")

    def test_owner_rep_can_approve_submittals(self):
        assert _check("owner_rep", "submittals", "approve")

    def test_owner_rep_can_read_all_core_resources(self):
        for resource in (
            "projects",
            "documents",
            "estimates",
            "schedules",
            "rfis",
            "procurement",
            "reports",
        ):
            assert _check("owner_rep", resource, "read"), f"OWNER_REP should read {resource}"

    # -- PROJECT_ADMIN ---------------------------------------------------
    def test_project_admin_has_full_projects(self):
        for action in ("read", "create", "update", "delete"):
            assert _check("project_admin", "projects", action)

    def test_project_admin_can_read_audit(self):
        assert _check("project_admin", "audit", "read")

    def test_project_admin_cannot_create_audit(self):
        """audit:read only, not audit:* for PROJECT_ADMIN."""
        assert not _check("project_admin", "audit", "create")

    # -- FIELD_ENGINEER --------------------------------------------------
    def test_field_engineer_can_upload_documents(self):
        assert _check("field_engineer", "documents", "upload")

    def test_field_engineer_can_create_rfis(self):
        assert _check("field_engineer", "rfis", "create")

    def test_field_engineer_can_create_daily_logs(self):
        assert _check("field_engineer", "daily_logs", "create")


# ---------------------------------------------------------------------------
# Wildcard expansion
# ---------------------------------------------------------------------------


class TestWildcardExpansion:
    """Verify that 'resource:*' grants all operations on a resource."""

    def test_project_admin_documents_wildcard(self):
        """PROJECT_ADMIN has documents:* -- arbitrary actions should pass."""
        for action in ("read", "create", "update", "delete", "upload", "export"):
            assert _check("project_admin", "documents", action), (
                f"documents:* should grant documents:{action}"
            )

    def test_project_manager_documents_wildcard(self):
        """PROJECT_MANAGER has documents:* via matrix."""
        assert _check("project_manager", "documents", "delete")

    def test_org_admin_global_wildcard(self):
        """ORG_ADMIN '*' grants anything."""
        for resource in ("projects", "unknown_resource", "foo"):
            for action in ("read", "create", "delete"):
                assert _check("org_admin", resource, action)


# ---------------------------------------------------------------------------
# Legacy role mapping
# ---------------------------------------------------------------------------


class TestLegacyRoleMappings:
    """Ensure backward-compatible role names still resolve correctly."""

    def test_platform_admin_maps_to_org_admin(self):
        assert _check("platform_admin", "anything", "whatever")

    def test_owner_developer_maps_to_owner_rep(self):
        assert _check("owner_developer", "change_orders", "approve")
        assert not _check("owner_developer", "documents", "create")

    def test_general_contractor_maps_to_project_admin(self):
        assert _check("general_contractor", "projects", "create")

    def test_architect_engineer_maps_to_field_engineer(self):
        assert _check("architect_engineer", "rfis", "create")
        assert not _check("architect_engineer", "projects", "update")

    def test_inspector_maps_to_field_engineer(self):
        assert _check("inspector", "quality", "create")

    def test_read_only_maps_to_readonly(self):
        assert _check("read_only", "projects", "read")
        assert not _check("read_only", "projects", "create")

    def test_member_maps_to_field_engineer(self):
        assert _check("member", "daily_logs", "create")

    def test_all_legacy_names_resolve(self):
        """Every key in _LEGACY_ROLE_MAP should resolve to a valid Role."""
        for legacy_name in _LEGACY_ROLE_MAP:
            actions = _enforcer.get_allowed_actions(legacy_name)
            assert len(actions) > 0, f"Legacy role '{legacy_name}' resolved to empty permissions"


# ---------------------------------------------------------------------------
# Edge cases in the enforcer
# ---------------------------------------------------------------------------


class TestEnforcerEdgeCases:
    """Exercise boundary conditions in RBACEnforcer.check_permission."""

    def test_unknown_role_denied(self):
        assert not _check("nonexistent_role", "projects", "read")

    def test_empty_role_denied(self):
        assert not _enforcer.check_permission("", "projects:read")

    def test_malformed_action_no_colon_denied(self):
        """Actions without ':' separator are rejected."""
        assert not _enforcer.check_permission("readonly", "projects_read")

    def test_case_insensitive_role(self):
        """Role resolution should be case-insensitive."""
        assert _enforcer.check_permission("READONLY", "projects:read")
        assert _enforcer.check_permission("Readonly", "projects:read")
        assert _enforcer.check_permission("ReadOnly", "projects:read")

    def test_role_with_whitespace(self):
        """Leading/trailing whitespace should be trimmed."""
        assert _enforcer.check_permission("  readonly  ", "projects:read")

    def test_get_allowed_actions_returns_set(self):
        result = _enforcer.get_allowed_actions("readonly")
        assert isinstance(result, set)
        assert len(result) > 0

    def test_get_allowed_actions_unknown_role_empty(self):
        result = _enforcer.get_allowed_actions("does_not_exist")
        assert isinstance(result, set)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Privilege escalation guards
# ---------------------------------------------------------------------------


class TestPrivilegeEscalationGuards:
    """Verify that no lower role accidentally gains higher privileges."""

    @pytest.mark.parametrize(
        "role_name",
        [
            "readonly",
            "subcontractor",
            "owner_rep",
            "field_engineer",
            "safety_manager",
        ],
    )
    def test_non_admin_cannot_manage_members(self, role_name):
        """Only PROJECT_ADMIN and ORG_ADMIN (via wildcard) should manage members."""
        assert not _check(role_name, "members", "create")
        assert not _check(role_name, "members", "delete")

    @pytest.mark.parametrize(
        "role_name",
        [
            "readonly",
            "subcontractor",
            "owner_rep",
        ],
    )
    def test_low_privilege_cannot_create_estimates(self, role_name):
        assert not _check(role_name, "estimates", "create")

    @pytest.mark.parametrize(
        "role_name",
        [
            "readonly",
            "subcontractor",
            "owner_rep",
            "safety_manager",
        ],
    )
    def test_low_privilege_cannot_manage_integrations(self, role_name):
        assert not _check(role_name, "integrations", "create")

    def test_readonly_has_minimum_permissions(self):
        """READONLY should only have 'read' actions (no filtered variants either)."""
        perms = PERMISSION_MATRIX[Role.READONLY]
        for perm in perms:
            if ":" in perm:
                _resource, ops = perm.split(":", 1)
                allowed_ops = {op.strip() for op in ops.split(",")}
                assert allowed_ops == {"read"}, f"READONLY has non-read permission: {perm}"
