import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    project_number: str | None = None
    type: str | None = None
    address: str | None = None
    contract_value: Decimal | None = None
    start_date: date | None = None
    end_date: date | None = None


class ProjectResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    project_number: str | None = None
    type: str | None = None
    status: str
    address: str | None = None
    contract_value: Decimal | None = None
    start_date: date | None = None
    end_date: date | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    status: Literal["active", "completed", "on_hold", "archived"] | None = None
    project_number: str | None = None
    type: str | None = None
    address: str | None = None
    contract_value: Decimal | None = None
    start_date: date | None = None
    end_date: date | None = None


class ProjectListResponse(BaseModel):
    data: list[ProjectResponse]
    meta: PaginationMeta


class ProjectMemberCreate(BaseModel):
    user_id: uuid.UUID
    role: str = Field(default="field_engineer", max_length=50)


class ProjectMemberUpdate(BaseModel):
    role: str = Field(max_length=50)


class ProjectMemberResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    role: str
    full_name: str | None = None
    email: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
