"""Audit logging for security-relevant events.

Provides structured audit trail for:
- Authentication events (login, logout, registration, password reset)
- Authorization events (access denied, role changes)
- Data access events (sensitive resource reads/writes)
- Admin operations (tenant creation, feature flags, user management)

Events are written to both:
1. A dedicated 'audit' Python logger (structured JSON for SIEM ingestion)
2. The ``audit_logs`` database table (immutable, queryable, 2-year retention)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.models.audit import AuditLog

logger = logging.getLogger("audit")


class AuditAction(StrEnum):
    # Authentication
    LOGIN_SUCCESS = "auth.login.success"
    LOGIN_FAILED = "auth.login.failed"
    LOGIN_LOCKED = "auth.login.locked"
    LOGOUT = "auth.logout"
    REGISTER = "auth.register"
    PASSWORD_RESET_REQUEST = "auth.password_reset.request"
    PASSWORD_RESET_COMPLETE = "auth.password_reset.complete"
    EMAIL_VERIFIED = "auth.email.verified"
    TOKEN_REFRESH = "auth.token.refresh"

    # Authorization
    ACCESS_DENIED = "authz.access_denied"
    ROLE_CHANGED = "authz.role_changed"

    # Data access
    RESOURCE_CREATED = "data.resource.created"
    RESOURCE_UPDATED = "data.resource.updated"
    RESOURCE_DELETED = "data.resource.deleted"

    # Admin
    TENANT_CREATED = "admin.tenant.created"
    FEATURE_FLAG_CHANGED = "admin.feature_flag.changed"
    USER_DEACTIVATED = "admin.user.deactivated"


def audit_log(
    action: AuditAction,
    *,
    user_id: str | UUID | None = None,
    org_id: str | UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | UUID | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Write a structured audit log entry (sync, logger-only).

    This is the backward-compatible sync version. For DB persistence,
    use ``audit_log_db()`` from async contexts with a DB session.
    """
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "action": action.value,
        "user_id": str(user_id) if user_id else None,
        "org_id": str(org_id) if org_id else None,
        "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id else None,
        "ip_address": ip_address,
        "details": details or {},
    }
    logger.info("AUDIT: %s", entry)


async def audit_log_db(
    db,
    action: AuditAction,
    *,
    user_id: str | UUID | None = None,
    org_id: str | UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Write an audit event to both the database and the structured logger.

    Parameters
    ----------
    db : AsyncSession
        SQLAlchemy async session.
    action : AuditAction
        The type of event being logged.
    user_id, org_id, resource_type, resource_id, ip_address, user_agent, details :
        Event context fields.
    """
    entry = AuditLog(
        action=action.value,
        user_id=str(user_id) if user_id else None,
        org_id=str(org_id) if org_id else None,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        ip_address=ip_address,
        user_agent=user_agent,
        details=details or {},
    )
    db.add(entry)

    # Also log to the structured logger for SIEM tools
    log_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "action": action.value,
        "user_id": str(user_id) if user_id else None,
        "org_id": str(org_id) if org_id else None,
        "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id else None,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "details": details or {},
    }
    logger.info("AUDIT: %s", log_entry)
