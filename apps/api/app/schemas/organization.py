import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    type: str = Field(default="gc")


class OrganizationResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    type: str
    subscription_tier: str
    created_at: datetime

    model_config = {"from_attributes": True}


class OrganizationListResponse(BaseModel):
    data: list[OrganizationResponse]
    meta: PaginationMeta
