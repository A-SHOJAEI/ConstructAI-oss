"""Pydantic schemas for drawing management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta

# ---------------------------------------------------------------------------
# Drawing Set
# ---------------------------------------------------------------------------


class DrawingSetCreate(BaseModel):
    name: str
    discipline: str
    description: str | None = None


class DrawingSetUpdate(BaseModel):
    name: str | None = None
    discipline: str | None = None
    description: str | None = None


class DrawingSetResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    discipline: str
    description: str | None = None
    drawing_count: int = 0
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DrawingSetListResponse(BaseModel):
    data: list[DrawingSetResponse]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# Drawing Revision
# ---------------------------------------------------------------------------


class DrawingRevisionResponse(BaseModel):
    id: uuid.UUID
    drawing_id: uuid.UUID
    revision_number: int
    s3_key: str
    original_filename: str
    file_size_bytes: int | None = None
    content_hash: str | None = None
    status: str
    uploaded_by: uuid.UUID | None = None
    created_at: datetime
    download_url: str | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


class DrawingResponse(BaseModel):
    id: uuid.UUID
    drawing_set_id: uuid.UUID
    project_id: uuid.UUID
    sheet_number: str
    title: str
    discipline: str
    status: str
    current_revision: DrawingRevisionResponse | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DrawingDetailResponse(DrawingResponse):
    revisions: list[DrawingRevisionResponse] = Field(default_factory=list)
    links: DrawingLinksResponse | None = None


class DrawingSetDetailResponse(DrawingSetResponse):
    drawings: list[DrawingResponse] = Field(default_factory=list)


class DrawingListResponse(BaseModel):
    data: list[DrawingResponse]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# Bulk Upload
# ---------------------------------------------------------------------------


class BulkUploadResult(BaseModel):
    drawing: DrawingResponse
    revision: DrawingRevisionResponse
    warnings: list[str] = Field(default_factory=list)


class BulkUploadResponse(BaseModel):
    uploaded: list[BulkUploadResult]
    errors: list[dict] = Field(default_factory=list)
    total_files: int
    successful: int
    failed: int


# ---------------------------------------------------------------------------
# Revision Comparison
# ---------------------------------------------------------------------------


class RevisionComparisonResponse(BaseModel):
    rev_a: DrawingRevisionResponse
    rev_b: DrawingRevisionResponse


# ---------------------------------------------------------------------------
# Drawing Markup
# ---------------------------------------------------------------------------


class DrawingMarkupCreate(BaseModel):
    markup_data: dict
    markup_type: str
    layer: str = "review"
    label: str | None = None


class DrawingMarkupUpdate(BaseModel):
    markup_data: dict | None = None
    markup_type: str | None = None
    layer: str | None = None
    label: str | None = None


class DrawingMarkupResponse(BaseModel):
    id: uuid.UUID
    drawing_revision_id: uuid.UUID
    markup_data: dict
    markup_type: str
    layer: str
    label: str | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Drawing Links
# ---------------------------------------------------------------------------


class DrawingLinkCreate(BaseModel):
    link_type: str  # "rfi", "submittal", "punch_list"
    entity_id: uuid.UUID


class DrawingLinksResponse(BaseModel):
    rfis: list[dict] = Field(default_factory=list)
    submittals: list[dict] = Field(default_factory=list)
    punch_list_items: list[dict] = Field(default_factory=list)
