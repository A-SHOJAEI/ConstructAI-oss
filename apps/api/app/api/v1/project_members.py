"""Project membership management API."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.project import ProjectMember
from app.models.user import User
from app.services.observability.audit_logger import AuditAction, audit_log

router = APIRouter()

VALID_PROJECT_ROLES = {
    "org_admin",
    "project_admin",
    "project_manager",
    "superintendent",
    "safety_manager",
    "field_engineer",
    "subcontractor",
    "owner_rep",
    "readonly",
}


class AddMemberRequest(BaseModel):
    user_id: uuid.UUID
    role: str = "field_engineer"


class UpdateRoleRequest(BaseModel):
    role: str


@router.get("/{project_id}/members")
async def list_members(
    project_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    cursor: uuid.UUID | None = Query(None),
    current_user: User = Depends(require_permission("members", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List members of a project with pagination (JOIN to avoid N+1)."""
    await verify_project_access(project_id, current_user, db)

    query = (
        select(ProjectMember)
        .options(joinedload(ProjectMember.user))
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.id)
    )
    if cursor:
        query = query.where(ProjectMember.id > cursor)
    query = query.limit(limit + 1)

    result = await db.execute(query)
    members = list(result.unique().scalars().all())
    has_more = len(members) > limit
    if has_more:
        members = members[:limit]

    items = []
    for m in members:
        user = m.user if hasattr(m, "user") else None
        items.append(
            {
                "id": str(m.id),
                "user_id": str(m.user_id),
                "role": m.role,
                "full_name": user.full_name if user else None,
                "email": user.email if user else None,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
        )

    next_cursor = str(members[-1].id) if has_more and members else None
    return {"items": items, "cursor": next_cursor, "has_more": has_more}


@router.post("/{project_id}/members", status_code=status.HTTP_201_CREATED)
async def add_member(
    project_id: uuid.UUID,
    body: AddMemberRequest,
    current_user: User = Depends(require_permission("members", "create")),
    db: AsyncSession = Depends(get_db),
):
    """Add a user to a project with a specific role."""
    await verify_project_access(project_id, current_user, db)

    user_id = body.user_id
    role = body.role

    if role not in VALID_PROJECT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(sorted(VALID_PROJECT_ROLES))}",
        )

    # Check user exists and is in the same org
    target_user = await db.get(User, user_id)
    if target_user is None or target_user.org_id != current_user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Check not already a member
    existing = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this project",
        )

    member = ProjectMember(
        project_id=project_id,
        user_id=user_id,
        role=role,
        invited_by=current_user.id,
    )
    db.add(member)
    await db.flush()
    await db.refresh(member)

    audit_log(
        AuditAction.RESOURCE_CREATED,
        user_id=current_user.id,
        org_id=current_user.org_id,
        resource_type="project_member",
        resource_id=member.id,
        details={"project_id": str(project_id), "target_user_id": str(user_id), "role": role},
    )

    return {
        "id": str(member.id),
        "project_id": str(project_id),
        "user_id": str(user_id),
        "role": role,
    }


@router.patch("/{project_id}/members/{user_id}")
async def update_member_role(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UpdateRoleRequest,
    current_user: User = Depends(require_permission("members", "update")),
    db: AsyncSession = Depends(get_db),
):
    """Update a project member's role."""
    await verify_project_access(project_id, current_user, db)

    role = body.role

    if role not in VALID_PROJECT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(sorted(VALID_PROJECT_ROLES))}",
        )

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    old_role = member.role
    member.role = role
    db.add(member)
    await db.flush()

    audit_log(
        AuditAction.ROLE_CHANGED,
        user_id=current_user.id,
        org_id=current_user.org_id,
        resource_type="project_member",
        resource_id=member.id,
        details={
            "project_id": str(project_id),
            "target_user_id": str(user_id),
            "old_role": old_role,
            "new_role": role,
        },
    )

    return {"detail": "Role updated", "user_id": str(user_id), "role": role}


@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_200_OK)
async def remove_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: User = Depends(require_permission("members", "delete")),
    db: AsyncSession = Depends(get_db),
):
    """Remove a user from a project."""
    await verify_project_access(project_id, current_user, db)

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    await db.delete(member)
    await db.flush()

    audit_log(
        AuditAction.RESOURCE_DELETED,
        user_id=current_user.id,
        org_id=current_user.org_id,
        resource_type="project_member",
        resource_id=member.id,
        details={"project_id": str(project_id), "target_user_id": str(user_id)},
    )

    return {"detail": "Member removed"}
