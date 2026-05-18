import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission, verify_project_access
from app.models.user import User
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)
from app.services.project import (
    create_project,
    list_projects,
    update_project,
)

router = APIRouter()


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_new_project(
    request: ProjectCreate,
    current_user: User = Depends(require_permission("projects", "create")),
    db: AsyncSession = Depends(get_db),
):
    project = await create_project(
        db,
        org_id=current_user.org_id,
        name=request.name,
        project_number=request.project_number,
        type=request.type,
        address=request.address,
        contract_value=request.contract_value,
        start_date=request.start_date,
        end_date=request.end_date,
    )
    return project


@router.get("/", response_model=ProjectListResponse)
async def list_user_projects(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_permission("projects", "read")),
    db: AsyncSession = Depends(get_db),
):
    result = await list_projects(db, org_id=current_user.org_id, cursor=cursor, limit=limit)
    return result


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_single_project(
    project_id: uuid.UUID,
    current_user: User = Depends(require_permission("projects", "read")),
    db: AsyncSession = Depends(get_db),
):
    project = await verify_project_access(project_id, current_user, db)
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_existing_project(
    project_id: uuid.UUID,
    update_data: ProjectUpdate,
    current_user: User = Depends(require_permission("projects", "update")),
    db: AsyncSession = Depends(get_db),
):
    project = await verify_project_access(project_id, current_user, db)
    updated = await update_project(
        db,
        project,
        **update_data.model_dump(exclude_unset=True),
    )
    return updated
