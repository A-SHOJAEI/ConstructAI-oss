"""Pydantic schemas for CloseoutIQ endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Valid enumerations
# ---------------------------------------------------------------------------

REQUIREMENT_TYPES = {
    "warranty",
    "om_manual",
    "test_report",
    "certification",
    "as_built",
    "training",
    "attic_stock",
    "spare_parts",
    "lien_waiver",
    "other",
}

REQUIREMENT_STATUSES = {
    "not_started",
    "requested",
    "submitted",
    "under_review",
    "accepted",
    "rejected",
    "waived",
}

DUE_MILESTONES = {
    "installation_complete",
    "system_test",
    "substantial_completion",
    "final_completion",
    "custom",
}

WARRANTY_STATUSES = {"active", "expiring_soon", "expired", "claimed"}

CLAIM_STATUSES = {"reported", "acknowledged", "in_progress", "resolved", "denied"}


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CloseoutGenerateRequest(BaseModel):
    spec_document_id: uuid.UUID


class CloseoutRequirementUpdate(BaseModel):
    status: str | None = None
    due_date: date | None = None
    responsible_sub_name: str | None = None
    responsible_sub_email: str | None = None
    rejection_notes: str | None = None


class CloseoutDocumentRequestCreate(BaseModel):
    recipient_email: str
    recipient_name: str | None = None
    message: str | None = None


class CloseoutReviewRequest(BaseModel):
    accepted: bool
    notes: str | None = None


class WarrantyClaimCreate(BaseModel):
    issue_description: str
    photos: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class CloseoutRequirementResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    csi_division: str | None = None
    section_title: str | None = None
    requirement_type: str
    description: str | None = None
    spec_reference: str | None = None
    responsible_sub_id: uuid.UUID | None = None
    responsible_sub_name: str | None = None
    responsible_sub_email: str | None = None
    due_milestone: str
    due_date: date | None = None
    pay_app_linkage: bool
    status: str
    submitted_doc_s3_key: str | None = None
    submitted_doc_name: str | None = None
    validation_flags: list[dict] = Field(default_factory=list)
    reviewer_id: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    rejection_notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CloseoutRequirementListResponse(BaseModel):
    data: list[CloseoutRequirementResponse]
    total: int


class WarrantyRecordResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    closeout_requirement_id: uuid.UUID | None = None
    warrantor: str
    system_description: str | None = None
    coverage_description: str | None = None
    warranty_years: int
    start_date: date | None = None
    end_date: date | None = None
    warranty_letter_s3_key: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WarrantyClaimResponse(BaseModel):
    id: uuid.UUID
    warranty_id: uuid.UUID
    reported_by: uuid.UUID | None = None
    issue_description: str
    photos: list[str] = Field(default_factory=list)
    claim_date: date
    resolution_status: str
    resolution_notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CloseoutDashboardResponse(BaseModel):
    progress_by_division: list[dict]
    progress_by_sub: list[dict]
    overdue_count: int
    total_items: int
    completed_items: int
    overall_pct: float
    projected_completion_date: str | None = None


class WarrantyCheckResponse(BaseModel):
    expiring_soon: list[WarrantyRecordResponse]
    expired: list[WarrantyRecordResponse]
