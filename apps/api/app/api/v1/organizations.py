import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission
from app.models.user import User
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationListResponse,
    OrganizationResponse,
)
from app.schemas.pagination import PaginationMeta
from app.services.organization import (
    create_organization,
    get_organization,
    list_organizations,
)

router = APIRouter()


@router.post("/", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_org(
    request: OrganizationCreate,
    current_user: User = Depends(require_permission("users", "manage")),
    db: AsyncSession = Depends(get_db),
):
    # Only platform admins may create new organizations
    if current_user.role != "platform_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only platform admins can create organizations.",
        )
    org = await create_organization(db, name=request.name, slug=request.slug, type=request.type)
    return org


@router.get("/", response_model=OrganizationListResponse)
async def list_orgs(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("users", "read")),
    db: AsyncSession = Depends(get_db),
):
    # Non-platform-admins may only see their own organization
    if current_user.role != "platform_admin":
        org = await get_organization(db, current_user.org_id)
        orgs = [org] if org else []
        return OrganizationListResponse(
            data=cast(list[OrganizationResponse], orgs),
            meta=PaginationMeta(has_more=False, cursor=None),
        )
    result = await list_organizations(db, cursor=cursor, limit=limit)
    return result


@router.get("/{org_id}", response_model=OrganizationResponse)
async def get_org(
    org_id: uuid.UUID,
    current_user: User = Depends(require_permission("users", "read")),
    db: AsyncSession = Depends(get_db),
):
    org = await get_organization(db, org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    # Non-platform-admins may only view their own organization
    if current_user.role != "platform_admin" and org.id != current_user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org
