"""Schemas for AI Plan Takeoff feature."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.schemas.pagination import PaginationMeta

TAKEOFF_STATUSES = {"processing", "completed", "failed", "converted"}
DRAWING_TYPES = {"floor_plan", "elevation", "section", "site_plan", "detail"}
ELEMENT_TYPES = {
    "room",
    "wall",
    "door",
    "window",
    "fixture",
    "material",
    "finish",
    "structural",
    "mechanical",
    "electrical",
    "plumbing",
}


# ---------------------------------------------------------------------------
# Takeoff Line Item
# ---------------------------------------------------------------------------


class TakeoffLineItemResponse(BaseModel):
    id: uuid.UUID
    takeoff_id: uuid.UUID
    element_type: str
    description: str
    csi_code: str | None = None
    quantity: Decimal
    unit: str
    dimensions: dict | None = None
    unit_cost: Decimal | None = None
    total_cost: Decimal | None = None
    material_cost: Decimal | None = None
    labor_cost: Decimal | None = None
    confidence: Decimal | None = None
    source: str
    cost_item_id: uuid.UUID | None = None
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Plan Takeoff
# ---------------------------------------------------------------------------


class PlanTakeoffUpload(BaseModel):
    """Form fields accompanying a plan upload (file is multipart)."""

    project_id: uuid.UUID
    name: str | None = None
    drawing_type: str | None = None
    location_state: str | None = Field(
        default=None,
        description="Two-letter state abbreviation for regional cost factors",
    )
    location_region: str | None = Field(
        default=None,
        description="Region name (northeast, southeast, etc.)",
    )

    @field_validator("drawing_type")
    @classmethod
    def validate_drawing_type(cls, v: str | None) -> str | None:
        if v is not None and v not in DRAWING_TYPES:
            raise ValueError(f"drawing_type must be one of {sorted(DRAWING_TYPES)}")
        return v


class PlanTakeoffResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    source_document_id: uuid.UUID | None = None
    file_url: str | None = None
    file_name: str
    status: str
    drawing_type: str | None = None
    extraction_metadata: dict
    total_estimated_cost: Decimal | None = None
    confidence_score: Decimal | None = None
    regional_factors: dict | None = None
    created_by: uuid.UUID | None = None
    line_items: list[TakeoffLineItemResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlanTakeoffSummary(BaseModel):
    """Lighter response for list views (no line items)."""

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    file_name: str
    status: str
    drawing_type: str | None = None
    total_estimated_cost: Decimal | None = None
    confidence_score: Decimal | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PlanTakeoffListResponse(BaseModel):
    data: list[PlanTakeoffSummary]
    meta: PaginationMeta


class ConvertToEstimateRequest(BaseModel):
    estimate_name: str | None = None
    contingency_pct: Decimal = Field(default=Decimal("10.0"), ge=0, le=50)


class ConvertToEstimateResponse(BaseModel):
    estimate_id: uuid.UUID
    estimate_name: str
    total_cost: Decimal | None = None
    line_item_count: int
    contingency_pct: Decimal
    takeoff_status: str

    model_config = {"from_attributes": False}
