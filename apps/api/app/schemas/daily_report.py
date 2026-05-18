"""Pydantic schemas for automated daily report endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta

# ---------------------------------------------------------------------------
# Generate / Create
# ---------------------------------------------------------------------------


class DailyReportGenerateRequest(BaseModel):
    report_date: date = Field(..., description="The date to generate the report for")


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class GeneratedDailyReportResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    report_date: date
    aggregated_data: dict
    narrative_markdown: str | None = None
    status: str
    generated_by: uuid.UUID | None = None
    reviewed_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    daily_log_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GeneratedDailyReportListResponse(BaseModel):
    data: list[GeneratedDailyReportResponse]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# Edit / Approve
# ---------------------------------------------------------------------------


class DailyReportEditRequest(BaseModel):
    narrative_markdown: str = Field(..., description="Updated narrative content (Markdown)")


class DailyReportApproveRequest(BaseModel):
    edits: str | None = Field(
        default=None,
        description="Optional edited narrative to replace on approval",
    )


# ---------------------------------------------------------------------------
# Save as daily log
# ---------------------------------------------------------------------------


class SaveAsLogResponse(BaseModel):
    """Response when saving an approved report as a DailyLog record."""

    daily_log_id: uuid.UUID
    report_id: uuid.UUID
    message: str = "Report saved as daily log"
