"""Role-Based Access Control with 9-role project-scoped permission matrix."""

from __future__ import annotations

import logging
from enum import IntEnum
from typing import ClassVar

logger = logging.getLogger(__name__)


class Role(IntEnum):
    """Nine-level role hierarchy for construction projects.

    Lower values = higher privilege. Roles are project-scoped via
    ``ProjectMember.role`` and org-scoped via ``User.role``.
    """

    ORG_ADMIN = 0
    PROJECT_ADMIN = 1
    PROJECT_MANAGER = 2
    SUPERINTENDENT = 3
    SAFETY_MANAGER = 4
    FIELD_ENGINEER = 5
    SUBCONTRACTOR = 6
    OWNER_REP = 7
    READONLY = 8


# Maps each role to its set of allowed "resource:operation" strings.
# Supports wildcards: "*" (global) and "resource:*" (all ops on resource).
# Comma-separated ops: "resource:read,update" means both read AND update.
PERMISSION_MATRIX: dict[Role, set[str]] = {
    Role.ORG_ADMIN: {"*"},
    Role.PROJECT_ADMIN: {
        "projects:*",
        "documents:*",
        "estimates:*",
        "estimating:*",
        "schedules:*",
        "change_orders:*",
        "pay_applications:*",
        "safety:*",
        "quality:*",
        "rfis:*",
        "submittals:*",
        "daily_logs:*",
        "daily_reports:*",
        "punch_lists:*",
        "drawings:*",
        "procurement:*",
        "reports:*",
        "cameras:*",
        "zones:*",
        "productivity:*",
        "members:*",
        "audit:read",
        "communication:*",
        "sub_portal:*",
        "users:*",
        "twins:*",
        "drones:*",
        "payroll:*",
        "insurance:*",
        "progress:*",
        "field_data:*",
        "contracts:*",
        "insights:read",
        "integrations:*",
        "controls:*",
    },
    Role.PROJECT_MANAGER: {
        "projects:read,update",
        "documents:*",
        "estimates:*",
        "estimating:read,create,update",
        "schedules:*",
        "change_orders:read,create,update,submit",
        "pay_applications:read,create,update,submit",
        "safety:read",
        "quality:*",
        "rfis:*",
        "submittals:*",
        "daily_logs:*",
        "daily_reports:read,create,update",
        "punch_lists:*",
        "drawings:*",
        "procurement:*",
        "reports:*",
        "cameras:read",
        "zones:read,update",
        "productivity:*",
        "members:read",
        "communication:*",
        "sub_portal:read,create,approve",
        "users:read",
        "twins:read,create,update",
        "drones:read,create",
        "payroll:read,create,update",
        "insurance:read,create,update",
        "progress:read,create,update",
        "field_data:read,create,update",
        "contracts:read,create,update",
        "insights:read",
        "integrations:read,create",
        "controls:read,create,update",
    },
    Role.SUPERINTENDENT: {
        "projects:read",
        "documents:read,upload",
        "estimates:read",
        "estimating:read",
        "schedules:read,update",
        "change_orders:read",
        "pay_applications:read",
        "safety:read,update",
        "quality:*",
        "rfis:read,create",
        "submittals:read",
        "daily_logs:*",
        "daily_reports:read,create",
        "punch_lists:*",
        "drawings:read",
        "procurement:read",
        "reports:read",
        "cameras:read",
        "zones:read,update",
        "productivity:read",
        "members:read",
        "communication:read,create",
        "twins:read",
        "drones:read,create",
        "progress:read,create",
        "field_data:read,create",
        "contracts:read",
        "controls:read",
        "insights:read",
        "users:read",
        "payroll:read",
        "insurance:read",
        "integrations:read",
    },
    Role.SAFETY_MANAGER: {
        "projects:read",
        "documents:read",
        "schedules:read",
        "safety:*",
        "quality:read",
        "rfis:read",
        "daily_logs:read",
        "daily_reports:read",
        "punch_lists:read",
        "drawings:read",
        "reports:read",
        "cameras:*",
        "zones:*",
        "members:read",
        "communication:read",
        "drones:read",
        "progress:read",
        "field_data:read",
        "users:read",
        "insurance:read",
        "twins:read",
        "controls:read",
    },
    Role.FIELD_ENGINEER: {
        "projects:read",
        "documents:read,upload",
        "estimates:read",
        "estimating:read",
        "schedules:read",
        "change_orders:read",
        "safety:read,create",
        "quality:read,create",
        "rfis:read,create",
        "submittals:read,create",
        "daily_logs:*",
        "daily_reports:read,create",
        "punch_lists:read,update",
        "drawings:read",
        "procurement:read",
        "reports:read",
        "cameras:read",
        "zones:read",
        "productivity:read",
        "members:read",
        "communication:read,create",
        "twins:read",
        "drones:read,create",
        "progress:read,create",
        "field_data:read,create",
        "contracts:read",
        "controls:read",
        "users:read",
        "insurance:read",
        "integrations:read",
    },
    Role.SUBCONTRACTOR: {
        "projects:read",
        "documents:read_filtered",
        "schedules:read_filtered",
        "change_orders:read_filtered",
        "rfis:read_filtered,create",
        "submittals:read_filtered",
        "daily_logs:create",
        "daily_reports:read",
        "punch_lists:update_assigned",
        "drawings:read_filtered",
        "communication:read",
        "sub_portal:read,create",
        "progress:read",
        "field_data:read",
        "safety:read",
        "users:read",
        "insurance:read",
        "controls:read",
    },
    Role.OWNER_REP: {
        "projects:read",
        "documents:read",
        "estimates:read",
        "estimating:read",
        "schedules:read",
        "change_orders:read,approve",
        "pay_applications:read,approve",
        "safety:read",
        "quality:read",
        "rfis:read",
        "submittals:read,approve",
        "daily_logs:read",
        "daily_reports:read",
        "punch_lists:read",
        "drawings:read",
        "procurement:read",
        "reports:read",
        "cameras:read",
        "zones:read",
        "productivity:read",
        "members:read",
        "communication:read",
        "users:read",
        "twins:read",
        "drones:read",
        "payroll:read",
        "insurance:read",
        "progress:read",
        "field_data:read",
        "contracts:read",
        "insights:read",
        "integrations:read",
        "controls:read",
    },
    Role.READONLY: {
        "projects:read",
        "documents:read",
        "estimates:read",
        "estimating:read",
        "schedules:read",
        "change_orders:read",
        "pay_applications:read",
        "safety:read",
        "quality:read",
        "rfis:read",
        "submittals:read",
        "daily_logs:read",
        "daily_reports:read",
        "punch_lists:read",
        "drawings:read",
        "procurement:read",
        "reports:read",
        "communication:read",
        "users:read",
        "twins:read",
        "drones:read",
        "payroll:read",
        "insurance:read",
        "progress:read",
        "field_data:read",
        "contracts:read",
        "insights:read",
        "integrations:read",
        "controls:read",
        "cameras:read",
        "zones:read",
        "productivity:read",
        "members:read",
    },
}

# Legacy role name mapping for backward compatibility
_LEGACY_ROLE_MAP: dict[str, str] = {
    "platform_admin": "org_admin",
    "owner_developer": "owner_rep",
    "general_contractor": "project_admin",
    "architect_engineer": "field_engineer",
    "inspector": "field_engineer",
    "read_only": "readonly",
    "member": "field_engineer",
}


class RBACEnforcer:
    """Check if a user's role permits a given action."""

    _matrix: ClassVar[dict[Role, set[str]]] = PERMISSION_MATRIX

    def check_permission(
        self,
        user_role: str,
        action: str,
    ) -> bool:
        """Check if role has permission for action.

        Action format: ``resource:operation``
        Supports wildcard matching: ``*`` matches everything,
        ``resource:*`` matches all ops on resource.
        """
        role = self._resolve_role(user_role)
        if role is None:
            logger.warning("Unknown role: %s", user_role)
            return False

        permissions = self._matrix.get(role, set())

        # Global wildcard — org admin
        if "*" in permissions:
            logger.debug("Role %s has global wildcard; granting %s", user_role, action)
            return True

        # Parse requested action
        if ":" not in action:
            logger.warning("Malformed action (no colon): %s", action)
            return False

        resource, operation = action.split(":", 1)

        for perm in permissions:
            if ":" not in perm:
                continue

            perm_resource, perm_ops = perm.split(":", 1)

            if perm_resource != resource:
                continue

            # resource:* grants every operation on this resource
            if perm_ops == "*":
                logger.debug("Wildcard match %s:* for action %s", perm_resource, action)
                return True

            # Exact full match
            if perm_ops == operation:
                return True

            # Comma-separated list
            allowed_ops = {op.strip() for op in perm_ops.split(",")}
            if operation in allowed_ops:
                logger.debug(
                    "Operation %s found in %s for resource %s",
                    operation,
                    allowed_ops,
                    resource,
                )
                return True

        logger.info("Permission denied: role=%s action=%s", user_role, action)
        return False

    def get_allowed_actions(self, user_role: str) -> set[str]:
        """Get all allowed actions for a role."""
        role = self._resolve_role(user_role)
        if role is None:
            logger.warning("Cannot resolve role '%s'; returning empty set", user_role)
            return set()
        return set(self._matrix.get(role, set()))

    def _resolve_role(self, role_str: str) -> Role | None:
        """Convert role string to Role enum (case-insensitive).

        Supports legacy role names via ``_LEGACY_ROLE_MAP``.
        """
        normalised = role_str.strip().lower()

        # Check legacy mapping first
        if normalised in _LEGACY_ROLE_MAP:
            normalised = _LEGACY_ROLE_MAP[normalised]

        for member in Role:
            if member.name.lower() == normalised:
                return member

        logger.debug("Role string '%s' did not match any Role", role_str)
        return None
