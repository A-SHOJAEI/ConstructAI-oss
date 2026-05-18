"""Pydantic schemas for AI progress tracking endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta

# ---------------------------------------------------------------------------
# Progress Photo schemas
# ---------------------------------------------------------------------------


class ProgressPhotoUpload(BaseModel):
    """Metadata for a progress photo upload (photo bytes sent as multipart)."""

    photo_url: str = Field(..., description="URL or S3 path of the uploaded photo")


class DetectionResult(BaseModel):
    class_name: str
    confidence: float
    bbox: list[int] | None = None


class ActivityMatchResult(BaseModel):
    activity_id: str
    activity_name: str
    detection_class: str
    csi_division: str
    match_score: float
    detection_confidence: float


class ProgressPhotoResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    photo_url: str
    s3_key: str
    detections: list
    matched_activities: list
    overall_confidence: Decimal | None = None
    uploaded_by: uuid.UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProgressAnalysisResponse(BaseModel):
    """Response from progress photo analysis."""

    photo_id: str
    project_id: str
    worker_count: int
    equipment_detected: list[dict]
    activity_matches: list[ActivityMatchResult]
    estimated_progress: dict[str, float]
    overall_confidence: float


# ---------------------------------------------------------------------------
# Progress Snapshot schemas
# ---------------------------------------------------------------------------


class ProgressSnapshotCreate(BaseModel):
    photo_ids: list[uuid.UUID] = Field(..., description="Photo IDs to include in this snapshot")
    activities_progress: dict[str, float] = Field(
        ..., description="Activity ID to percent complete mapping"
    )
    notes: str | None = None


class ProgressSnapshotResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    snapshot_date: date
    activities_progress: dict
    overall_progress: Decimal | None = None
    photo_ids: list
    notes: str | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProgressSnapshotListResponse(BaseModel):
    data: list[ProgressSnapshotResponse]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# Progress Variance schemas
# ---------------------------------------------------------------------------


class ProgressVarianceResponse(BaseModel):
    activity_id: str
    activity_name: str
    scheduled_pct: float
    estimated_pct: float
    variance_pct: float
    status: str  # ahead, behind, on_track


class ProgressVarianceListResponse(BaseModel):
    project_id: str
    variances: list[ProgressVarianceResponse]
    summary: dict = Field(
        default_factory=dict,
        description="Summary: counts of ahead/behind/on_track",
    )


# ---------------------------------------------------------------------------
# Apply progress request
# ---------------------------------------------------------------------------


class ApplyProgressRequest(BaseModel):
    snapshot_id: uuid.UUID = Field(..., description="Snapshot whose progress estimates to apply")


class ApplyProgressResponse(BaseModel):
    activities_updated: int
    snapshot_id: str


# ---------------------------------------------------------------------------
# Progress report
# ---------------------------------------------------------------------------


class ProgressReportResponse(BaseModel):
    """Combined progress report for a project."""

    project_id: str
    report_date: date
    overall_progress: float | None = None
    activity_count: int
    worker_count: int
    equipment_summary: list[dict]
    variances: list[ProgressVarianceResponse]
    recent_photos: list[ProgressPhotoResponse]
