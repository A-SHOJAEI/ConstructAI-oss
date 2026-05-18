from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta


class CameraCreate(BaseModel):
    project_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    stream_url: str = Field(max_length=2048)
    fps_setting: int = Field(default=5, ge=1, le=30)
    resolution: str = Field(default="1080p", max_length=20)
    location_description: str | None = Field(default=None, max_length=500)


class CameraResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    stream_url: str
    location_description: str | None = None
    is_active: bool
    fps_setting: int
    resolution: str
    config: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    stream_url: str | None = Field(default=None, max_length=2048)
    is_active: bool | None = None
    fps_setting: int | None = None
    resolution: str | None = Field(default=None, max_length=20)
    location_description: str | None = Field(default=None, max_length=500)


class CameraListResponse(BaseModel):
    data: list[CameraResponse]
    meta: PaginationMeta
