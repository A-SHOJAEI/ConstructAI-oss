"""Pydantic schemas for SiteScribe source management endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class ReportSourceCreate(BaseModel):
    source_type: str = Field(
        ..., description="Type of source: photo, voice_memo, text_message, manual"
    )
    text_content: str | None = Field(
        default=None, description="Text body for text_message or manual sources"
    )
    s3_key: str | None = Field(default=None, description="S3 object key for uploaded file")
    filename: str | None = Field(default=None, description="Original filename")
    mime_type: str | None = Field(default=None, description="MIME type of uploaded file")


class SiteScribeGenerateRequest(BaseModel):
    report_date: date = Field(..., description="Date for the daily report")
    include_previous_day: bool = Field(
        default=True,
        description="Whether to include previous day context in narrative",
    )


class SiteScribeApproveRequest(BaseModel):
    reviewer_notes: str | None = Field(default=None, description="Optional notes from the reviewer")


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ReportSourceResponse(BaseModel):
    id: uuid.UUID
    daily_report_id: uuid.UUID
    source_type: str
    s3_key: str | None = None
    filename: str | None = None
    transcript: str | None = None
    text_content: str | None = None
    ai_tags: dict = {}
    exif_data: dict = {}
    processing_status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SiteScribeReportResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    report_date: date
    status: str
    weather_data: dict | None = None
    manpower_data: list[dict] = []
    work_performed: list[dict] = []
    delays: list[dict] = []
    deliveries: list[dict] = []
    narrative_draft: str | None = None
    narrative_final: str | None = None
    sources: list[ReportSourceResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SiteScribeDashboardResponse(BaseModel):
    total_reports: int
    draft_count: int
    approved_count: int
    latest_report_date: str | None = None
    avg_sources_per_report: float
