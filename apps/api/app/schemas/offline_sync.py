"""Pydantic schemas for offline-first mobile sync endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Push: client → server
# ---------------------------------------------------------------------------


class SyncPushItem(BaseModel):
    entity_type: str = Field(
        ...,
        pattern=r"^(daily_log|punch_list_item|safety_observation|time_entry)$",
        description="Type of entity being synced",
    )
    entity_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="UUID of the entity",
    )
    operation: str = Field(
        default="update",
        pattern=r"^(create|update|delete)$",
    )
    payload: dict = Field(
        default_factory=dict,
        description="Entity data to sync",
    )
    client_timestamp: datetime = Field(
        ..., description="Timestamp of the change on the client device"
    )

    @field_validator("entity_id")
    @classmethod
    def validate_entity_id_is_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("entity_id must be a valid UUID")
        return v


class SyncPushRequest(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=255)
    items: list[SyncPushItem] = Field(..., max_length=200)
    device_info: dict = Field(
        default_factory=dict,
        description="Optional device metadata (OS, app version, etc.)",
    )


class SyncPushResponse(BaseModel):
    processed: int
    created: int
    updated: int
    conflicts: int
    errors: int
    conflict_details: list[dict] = Field(default_factory=list)
    error_details: list[dict] = Field(default_factory=list)
    server_timestamp: str


# ---------------------------------------------------------------------------
# Pull: server → client
# ---------------------------------------------------------------------------


class SyncPullRequest(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=255)
    since: datetime | None = Field(
        default=None,
        description="Pull records updated after this timestamp. None = full sync.",
    )
    entity_types: list[str] | None = Field(
        default=None,
        description="Filter to specific entity types. None = all types.",
    )
    limit: int = Field(default=500, ge=1, le=1000)


class SyncPullResponse(BaseModel):
    items: list[dict]
    server_timestamp: str
    has_more: bool
    total_available: int


# ---------------------------------------------------------------------------
# Sync status
# ---------------------------------------------------------------------------


class SyncStatusResponse(BaseModel):
    id: uuid.UUID
    device_id: str
    project_id: uuid.UUID
    user_id: uuid.UUID
    last_push_at: datetime | None = None
    last_pull_at: datetime | None = None
    last_server_timestamp: datetime | None = None
    device_info: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Conflict log
# ---------------------------------------------------------------------------


class ConflictLogResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    device_id: str
    entity_type: str
    entity_id: str
    client_data: dict
    server_data: dict
    client_timestamp: datetime
    server_timestamp: datetime
    resolution: str
    is_resolved: bool
    resolved_by: uuid.UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConflictResolveRequest(BaseModel):
    resolution: str = Field(
        ...,
        pattern=r"^(client_wins|server_wins|manual_merge)$",
    )
    merged_data: dict | None = Field(
        default=None,
        description="Merged payload for manual_merge resolution",
    )


# ---------------------------------------------------------------------------
# Photo upload
# ---------------------------------------------------------------------------


class PhotoUploadResponse(BaseModel):
    id: uuid.UUID
    s3_key: str | None = None
    status: str
    file_size_bytes: int | None = None

    model_config = {"from_attributes": True}


class PendingPhotoResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    device_id: str
    entity_type: str | None = None
    entity_id: str | None = None
    file_name: str
    content_type: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
