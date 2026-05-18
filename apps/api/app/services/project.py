import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.utils.pagination import paginate

logger = logging.getLogger(__name__)

_ALLOWED_UPDATE_FIELDS = {
    "name",
    "description",
    "status",
    "start_date",
    "end_date",
    "contract_value",
    "location",
    "address",
    "settings",
}


async def create_project(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
    project_number: str | None = None,
    type: str | None = None,
    address: str | None = None,
    contract_value=None,
    start_date=None,
    end_date=None,
) -> Project:
    project = Project(
        org_id=org_id,
        name=name,
        project_number=project_number,
        type=type,
        address=address,
        contract_value=contract_value,
        start_date=start_date,
        end_date=end_date,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return project


async def list_projects(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    query = select(Project).where(Project.org_id == org_id)
    return await paginate(db, query, cursor=cursor, limit=limit, model=Project)


async def get_project(db: AsyncSession, project_id: uuid.UUID) -> Project | None:
    return await db.get(Project, project_id)


async def update_project(db: AsyncSession, project: Project, **kwargs) -> Project:
    for key, value in kwargs.items():
        if key not in _ALLOWED_UPDATE_FIELDS:
            logger.warning(
                "Rejected disallowed field %r in update_project for project %s",
                key,
                project.id,
            )
            continue
        if value is not None:
            setattr(project, key, value)
    await db.flush()
    await db.refresh(project)
    return project
