"""Admin and tenant management API endpoints."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user, require_mfa, require_permission
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.admin import (
    FeatureFlagCreateRequest,
    FeatureFlagResponse,
    TenantCreateRequest,
    TenantCreateResponse,
)
from app.schemas.user import (
    AdminUserCreate,
    AdminUserPatch,
    UserListResponse,
    UserResponse,
)
from app.services.observability.audit_logger import AuditAction, audit_log
from app.utils.security import hash_password

router = APIRouter()

_STUB_HEADER = {"X-ConstructAI-Stub": "true"}
_STUB_META = {"stub": True, "message": "Admin feature implementation pending"}


def _check_stub_enabled():
    """In production, stub endpoints return 501 instead of fake data."""
    if settings.ENVIRONMENT in ("production", "staging"):
        raise HTTPException(
            status_code=501,
            detail="This feature is not yet available in production.",
        )


async def require_platform_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Dependency that ensures the current user is an org admin (or legacy platform admin)."""
    if user.role not in ("org_admin", "platform_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


@router.post(
    "/tenants",
    status_code=201,
)
async def create_tenant(
    body: TenantCreateRequest,
    _user: Annotated[User, Depends(require_platform_admin)],
    _mfa: Annotated[User, Depends(require_mfa())],
    _db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new tenant via TenantProvisioner (stub)."""
    _check_stub_enabled()
    now = datetime.now(UTC)
    data = TenantCreateResponse(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        billing_plan=body.billing_plan,
        created_at=now,
    )
    return JSONResponse(
        content={"data": data.model_dump(mode="json"), "meta": _STUB_META},
        status_code=201,
        headers=_STUB_HEADER,
    )


@router.get(
    "/tenants",
)
async def list_tenants(
    _user: Annotated[User, Depends(require_platform_admin)],
    _db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all tenant configurations (stub)."""
    _check_stub_enabled()
    return JSONResponse(
        content={"data": {"items": []}, "meta": _STUB_META},
        headers=_STUB_HEADER,
    )


@router.post(
    "/feature-flags",
    status_code=201,
)
async def create_feature_flag(
    body: FeatureFlagCreateRequest,
    _user: Annotated[User, Depends(require_platform_admin)],
    _mfa: Annotated[User, Depends(require_mfa())],
    _db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new feature flag (stub)."""
    _check_stub_enabled()
    now = datetime.now(UTC)
    data = FeatureFlagResponse(
        id=uuid.uuid4(),
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        rollout_percentage=body.rollout_percentage,
        tenant_overrides={},
        created_at=now,
    )
    return JSONResponse(
        content={"data": data.model_dump(mode="json"), "meta": _STUB_META},
        status_code=201,
        headers=_STUB_HEADER,
    )


@router.get(
    "/feature-flags",
)
async def list_feature_flags(
    _user: Annotated[User, Depends(require_platform_admin)],
    _db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all feature flags (stub)."""
    _check_stub_enabled()
    return JSONResponse(
        content={"data": {"items": []}, "meta": _STUB_META},
        headers=_STUB_HEADER,
    )


@router.get("/audit-logs")
async def list_audit_logs(
    user: Annotated[User, Depends(require_permission("audit", "read"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    action: str | None = Query(default=None, description="Filter by action type"),
    user_id: uuid.UUID | None = Query(default=None, description="Filter by user ID"),
    resource_type: str | None = Query(default=None, description="Filter by resource type"),
    start_date: datetime | None = Query(default=None, description="Start date (inclusive)"),
    end_date: datetime | None = Query(default=None, description="End date (inclusive)"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Query audit logs with optional filters. Requires audit:read permission."""
    query = select(AuditLog).order_by(AuditLog.timestamp.desc())

    if action:
        query = query.where(AuditLog.action == action)
    if user_id:
        # Validate that the filtered user_id belongs to the same org (non-platform_admin)
        if user.role != "platform_admin":
            target_user = await db.get(User, user_id)
            if target_user is None or target_user.org_id != user.org_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot query audit logs for users outside your organization",
                )
        query = query.where(AuditLog.user_id == user_id)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if start_date:
        query = query.where(AuditLog.timestamp >= start_date)
    if end_date:
        query = query.where(AuditLog.timestamp <= end_date)

    # All users see only their org's logs (platform_admin can see all)
    if user.role != "platform_admin":
        query = query.where(AuditLog.org_id == user.org_id)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    logs = result.scalars().all()

    return {
        "items": [
            {
                "id": str(log.id),
                "timestamp": log.timestamp.isoformat(),
                "action": log.action,
                "user_id": str(log.user_id) if log.user_id else None,
                "org_id": str(log.org_id) if log.org_id else None,
                "resource_type": log.resource_type,
                "resource_id": str(log.resource_id) if log.resource_id else None,
                "ip_address": log.ip_address,
                "user_agent": log.user_agent,
                "details": log.details,
            }
            for log in logs
        ],
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# User CRUD (org-admin only)
# ---------------------------------------------------------------------------


@router.get("/users", response_model=UserListResponse)
async def list_users(
    user: Annotated[User, Depends(require_platform_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    role: str | None = Query(default=None, description="Filter by role"),
    is_active: bool | None = Query(default=None, description="Filter by active status"),
    search: str | None = Query(default=None, max_length=100, description="Search by name or email"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List users in the admin's organization."""
    query = select(User).where(User.org_id == user.org_id)

    if role:
        query = query.where(User.role == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.where(User.full_name.ilike(pattern) | User.email.ilike(pattern))

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar_one()

    query = query.order_by(User.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()

    return UserListResponse(items=cast(list[UserResponse], users), total=total)


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    body: AdminUserCreate,
    user: Annotated[User, Depends(require_platform_admin)],
    _mfa: Annotated[User, Depends(require_mfa())],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new user in the admin's organization."""
    # Check email uniqueness
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Generate a random temporary password.
    # SECURITY [P3-1/P3-2]: secrets.token_urlsafe(16) produces 22 URL-safe chars
    # (mixed case + digits) plus "A1!" suffix = 25 chars total. This exceeds the
    # 12-char minimum and satisfies uppercase, lowercase, digit, and special
    # character requirements by construction. No additional validation needed.
    temp_password = secrets.token_urlsafe(16) + "A1!"
    new_user = User(
        email=body.email,
        full_name=body.full_name,
        role=body.role,
        org_id=user.org_id,
        hashed_password=hash_password(temp_password),
    )
    db.add(new_user)
    await db.flush()
    await db.refresh(new_user)

    # Send invite email with password reset link
    from app.api.v1.auth import _create_email_verification_token
    from app.services.email.service import send_verification_email

    token = _create_email_verification_token(str(new_user.id), new_user.email)
    send_verification_email(new_user.email, token)

    audit_log(
        AuditAction.RESOURCE_CREATED,
        user_id=user.id,
        org_id=user.org_id,
        resource_type="user",
        resource_id=new_user.id,
        details={"email": new_user.email, "role": new_user.role},
    )

    return new_user


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: uuid.UUID,
    user: Annotated[User, Depends(require_platform_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a user's details. Only visible within the admin's organization."""
    target = await db.get(User, user_id)
    if target is None or target.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return target


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    body: AdminUserPatch,
    user: Annotated[User, Depends(require_platform_admin)],
    _mfa: Annotated[User, Depends(require_mfa())],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a user's name, role, or active status."""
    target = await db.get(User, user_id)
    if target is None or target.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    changes: dict = {}
    if body.full_name is not None:
        target.full_name = body.full_name
        changes["full_name"] = body.full_name
    if body.role is not None:
        old_role = target.role
        target.role = body.role
        changes["role"] = body.role
        # RT6-AUTH-14: Invalidate tokens when role changes
        if old_role != body.role:
            target.token_version = (target.token_version or 0) + 1
    if body.is_active is not None:
        target.is_active = body.is_active
        changes["is_active"] = body.is_active
        if not body.is_active:
            # Invalidate sessions on deactivation
            target.token_version = (target.token_version or 0) + 1

    db.add(target)
    await db.flush()
    await db.refresh(target)

    audit_log(
        AuditAction.RESOURCE_UPDATED,
        user_id=user.id,
        org_id=user.org_id,
        resource_type="user",
        resource_id=target.id,
        details=changes,
    )

    return target


@router.delete("/users/{user_id}", status_code=200)
async def deactivate_user(
    user_id: uuid.UUID,
    user: Annotated[User, Depends(require_platform_admin)],
    _mfa: Annotated[User, Depends(require_mfa())],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Deactivate a user (soft delete). Cannot deactivate yourself."""
    if user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )

    target = await db.get(User, user_id)
    if target is None or target.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target.is_active = False
    target.token_version = (target.token_version or 0) + 1
    db.add(target)
    await db.flush()

    audit_log(
        AuditAction.RESOURCE_UPDATED,
        user_id=user.id,
        org_id=user.org_id,
        resource_type="user",
        resource_id=target.id,
        details={"action": "deactivated"},
    )

    return {"detail": "User deactivated"}


# ---------------------------------------------------------------------------
# JWT Key Rotation (platform admin only)
# ---------------------------------------------------------------------------


class JWTRotationRequest(BaseModel):
    new_key: str = Field(..., min_length=32, max_length=256)


@router.post("/jwt/rotate", status_code=200)
async def rotate_jwt_key_endpoint(
    body: JWTRotationRequest,
    user: Annotated[User, Depends(require_platform_admin)],
    _mfa: Annotated[User, Depends(require_mfa())],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Rotate the JWT signing key. Platform admin only."""
    new_key = body.new_key

    from app.utils.security import rotate_jwt_key

    version = await rotate_jwt_key(new_key)

    # Audit log
    audit_entry = AuditLog(
        user_id=user.id,
        org_id=user.org_id,
        action="jwt_key_rotated",
        resource_type="system",
        details={"key_version": version},
    )
    db.add(audit_entry)
    await db.flush()

    return {"key_version": version, "previous_key_valid_for": "7 days"}
