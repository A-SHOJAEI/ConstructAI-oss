"""Pydantic schemas for the subcontractor portal."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.schemas.pagination import PaginationMeta

# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class SubcontractorProfileResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    company_name: str
    trade: str
    sov_item_ids: list[str]
    contact_info: dict
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SubcontractorProfileCreate(BaseModel):
    company_name: str = Field(min_length=1, max_length=500)
    trade: str = Field(min_length=1, max_length=200)
    sov_item_ids: list[str] = Field(default_factory=list)
    contact_info: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Manpower
# ---------------------------------------------------------------------------


class ManpowerSubmissionRequest(BaseModel):
    date: date
    workers_by_trade: dict[str, int] = Field(
        ...,
        description="Mapping of trade name to worker count, e.g. {'electrician': 4}",
    )
    total_hours: float = Field(gt=0, description="Total manhours for the day")
    notes: str | None = None

    @field_validator("workers_by_trade")
    @classmethod
    def validate_workers(cls, v: dict[str, int]) -> dict[str, int]:
        if not v:
            raise ValueError("workers_by_trade must have at least one entry")
        for trade, count in v.items():
            if count < 0:
                raise ValueError(f"Worker count for '{trade}' cannot be negative")
        return v


# ---------------------------------------------------------------------------
# Delivery Receipt
# ---------------------------------------------------------------------------


class DeliveryReceiptRequest(BaseModel):
    material_description: str = Field(min_length=1, max_length=1000)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=50)
    supplier: str = Field(min_length=1, max_length=500)
    delivery_date: date
    document_url: str | None = None


# ---------------------------------------------------------------------------
# Sub Pay Application
# ---------------------------------------------------------------------------


class SubPayAppLineItem(BaseModel):
    """A single line item in a subcontractor pay application."""

    item_id: str = Field(description="SOV line item UUID")
    work_completed_this_period: Decimal = Field(ge=0, default=Decimal("0"))
    materials_presently_stored: Decimal = Field(ge=0, default=Decimal("0"))


class SubPayApplicationRequest(BaseModel):
    line_items: list[SubPayAppLineItem] = Field(min_length=1)
    period_to: date
    notes: str | None = None


# ---------------------------------------------------------------------------
# Payment Status
# ---------------------------------------------------------------------------


class PaymentStatusEntry(BaseModel):
    period_to: date
    submission_id: uuid.UUID
    submitted_amount: Decimal
    approved_amount: Decimal
    paid_amount: Decimal
    retainage_held: Decimal
    status: str


class PaymentStatusResponse(BaseModel):
    data: list[PaymentStatusEntry]


# ---------------------------------------------------------------------------
# Submission responses
# ---------------------------------------------------------------------------


class SubmissionResponse(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    submission_type: str
    submission_date: date
    data: dict
    document_url: str | None = None
    status: str
    reviewed_by: uuid.UUID | None = None
    review_notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SubmissionListResponse(BaseModel):
    data: list[SubmissionResponse]
    total: int
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


class ReviewSubmissionRequest(BaseModel):
    status: str = Field(description="One of: reviewed, approved, rejected")
    notes: str | None = None

    @field_validator("status")
    @classmethod
    def validate_review_status(cls, v: str) -> str:
        allowed = {"reviewed", "approved", "rejected"}
        if v not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return v


# ---------------------------------------------------------------------------
# Translated safety briefing
# ---------------------------------------------------------------------------


class TranslatedBriefingRequest(BaseModel):
    briefing_text: str = Field(min_length=1, max_length=5000)
    target_language: str = Field(
        min_length=2,
        max_length=2,
        description="ISO 639-1 language code (e.g., 'es', 'zh', 'fr')",
    )


class TranslatedBriefingResponse(BaseModel):
    translated_text: str
    target_language: str


# ---------------------------------------------------------------------------
# Filtered SOV
# ---------------------------------------------------------------------------


class FilteredSOVItem(BaseModel):
    id: str
    item_number: str
    description: str
    scheduled_value: str
    csi_code: str | None = None
    sort_order: int


class FilteredSOVResponse(BaseModel):
    data: list[FilteredSOVItem]
