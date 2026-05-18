import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.utils.pagination import paginate


async def create_organization(
    db: AsyncSession,
    *,
    name: str,
    slug: str,
    type: str = "gc",
) -> Organization:
    org = Organization(name=name, slug=slug, type=type)
    db.add(org)
    await db.flush()
    await db.refresh(org)
    return org


async def get_organization(db: AsyncSession, org_id: uuid.UUID) -> Organization | None:
    return await db.get(Organization, org_id)


async def list_organizations(
    db: AsyncSession,
    *,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    query = select(Organization)
    return await paginate(db, query, cursor=cursor, limit=limit, model=Organization)
