"""Pydantic schemas for quality management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

from app.schemas.pagination import PaginationMeta

# H-3: Shared severity/status vocabularies. Constrain these at the schema
# layer so invalid states never reach the DB.
SeverityLevel = Literal["minor", "major", "critical"]
DefectStatus = Literal["open", "in_progress", "resolved", "closed"]
NCRStatus = Literal["open", "under_review", "corrective_action", "closed"]


class InspectionCreate(BaseModel):
    project_id: uuid.UUID
    inspection_type: str = Field(min_length=1, max_length=100)
    location: str | None = Field(default=None, max_length=500)
    checklist_data: dict = Field(default_factory=dict)
    scheduled_at: datetime | None = None


class InspectionResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    inspection_type: str
    status: str
    inspector_id: uuid.UUID | None = None
    location: str | None = None
    checklist_data: dict
    findings: list[dict] | dict
    score: Decimal | None = None
    scheduled_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InspectionListResponse(BaseModel):
    data: list[InspectionResponse]
    meta: PaginationMeta


class DefectReportCreate(BaseModel):
    project_id: uuid.UUID
    inspection_id: uuid.UUID | None = None
    defect_type: str = Field(min_length=1, max_length=100)
    severity: SeverityLevel = "minor"
    description: str = Field(min_length=1, max_length=5000)
    location: str | None = Field(default=None, max_length=500)
    # H-5: HttpUrl rejects non-http(s) schemes (file://, gopher://, etc.)
    # and enforces a reasonable URL shape. Capped at 10 images to prevent
    # payload-size abuse.
    image_urls: list[HttpUrl] = Field(default_factory=list, max_length=10)


class DefectReportResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    inspection_id: uuid.UUID | None = None
    defect_type: str
    severity: str
    status: str
    description: str
    location: str | None = None
    image_urls: list
    ai_classification: dict
    assigned_to: uuid.UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DefectReportListResponse(BaseModel):
    data: list[DefectReportResponse]
    meta: PaginationMeta


class NCRCreate(BaseModel):
    project_id: uuid.UUID
    ncr_number: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10000)
    severity: SeverityLevel = "minor"


class NCRResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    ncr_number: str
    title: str
    description: str
    status: str
    severity: str
    root_cause: str | None = None
    corrective_action: str | None = None
    cost_impact: Decimal | None = None
    reported_by: uuid.UUID | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NCRListResponse(BaseModel):
    data: list[NCRResponse]
    meta: PaginationMeta


class ComplianceCheckResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    regulation_code: str
    regulation_title: str
    status: str
    check_result: str | None = None
    findings: list
    checked_by: uuid.UUID | None = None
    checked_at: datetime | None = None
    next_check_due: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ComplianceCheckListResponse(BaseModel):
    data: list[ComplianceCheckResponse]
    meta: PaginationMeta


class DefectClassificationResult(BaseModel):
    defect_type: str
    confidence: float
    severity_estimate: str
    recommendations: list[str]


# ---------------------------------------------------------------------------
# Compliance checklists (seed-data based)
# ---------------------------------------------------------------------------


class ComplianceChecklistItem(BaseModel):
    """A single compliance checklist entry from seed data."""

    category: str
    check_id: str
    description: str
    standard_reference: str
    severity: str
    applicable_project_types: list[str]
    applicable_phases: list[str]
    frequency: str
    verification_method: str | None = None
    documentation_required: bool = True

    model_config = {"from_attributes": True}


class ComplianceChecklistListResponse(BaseModel):
    data: list[ComplianceChecklistItem]
    total: int


class ComplianceChecklistSummary(BaseModel):
    total_checks: int
    by_category: dict[str, int]
    by_severity: dict[str, int]
