"""Schemas for Change Order Lifecycle: PCO -> COR -> CO."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.schemas.pagination import PaginationMeta

PCO_CHANGE_TYPES = {
    "owner_directed",
    "field_condition",
    "design_error",
    "value_engineering",
    "regulatory",
    "unforeseen_condition",
}

PCO_STATUSES = {"draft", "pending_review", "approved", "rejected", "void"}
COR_STATUSES = {"draft", "submitted", "under_review", "approved", "rejected", "void"}


# ---------------------------------------------------------------------------
# PCO
# ---------------------------------------------------------------------------


class PCOCostBreakdown(BaseModel):
    labor_cost: Decimal = Decimal("0")
    material_cost: Decimal = Decimal("0")
    equipment_cost: Decimal = Decimal("0")
    subcontractor_cost: Decimal = Decimal("0")
    overhead_cost: Decimal = Decimal("0")
    profit_markup_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class PCOCreate(BaseModel):
    project_id: uuid.UUID
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1)
    change_type: str
    cost_breakdown: PCOCostBreakdown = PCOCostBreakdown()
    schedule_impact_days: int = 0
    spec_section: str | None = None
    drawing_reference: str | None = None
    attachments: list[dict] = []

    @field_validator("change_type")
    @classmethod
    def validate_change_type(cls, v: str) -> str:
        if v not in PCO_CHANGE_TYPES:
            raise ValueError(f"change_type must be one of {sorted(PCO_CHANGE_TYPES)}")
        return v


class PCOUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    change_type: str | None = None
    cost_breakdown: PCOCostBreakdown | None = None
    schedule_impact_days: int | None = None
    status: str | None = None
    spec_section: str | None = None
    drawing_reference: str | None = None
    attachments: list[dict] | None = None

    @field_validator("change_type")
    @classmethod
    def validate_change_type(cls, v: str | None) -> str | None:
        if v is not None and v not in PCO_CHANGE_TYPES:
            raise ValueError(f"change_type must be one of {sorted(PCO_CHANGE_TYPES)}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in PCO_STATUSES:
            raise ValueError(f"status must be one of {sorted(PCO_STATUSES)}")
        return v


class PCOResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    pco_number: int
    title: str
    description: str
    change_type: str
    status: str
    originated_by: uuid.UUID | None = None
    reviewed_by: uuid.UUID | None = None
    labor_cost: Decimal
    material_cost: Decimal
    equipment_cost: Decimal
    subcontractor_cost: Decimal
    overhead_cost: Decimal
    profit_markup_pct: Decimal
    total_cost: Decimal
    schedule_impact_days: int
    spec_section: str | None = None
    drawing_reference: str | None = None
    attachments: list[dict]
    risk_score: Decimal | None = None
    ai_analysis: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PCOListResponse(BaseModel):
    data: list[PCOResponse]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# COR
# ---------------------------------------------------------------------------


class CORCreate(BaseModel):
    project_id: uuid.UUID
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    pco_ids: list[uuid.UUID] = Field(min_length=1)
    markup_pct: Decimal = Decimal("0")
    overhead_pct: Decimal = Decimal("0")
    cor_adjustment: Decimal = Decimal("0")


class CORUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    markup_pct: Decimal | None = None
    overhead_pct: Decimal | None = None
    cor_adjustment: Decimal | None = None
    submitted_to: uuid.UUID | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in COR_STATUSES:
            raise ValueError(f"status must be one of {sorted(COR_STATUSES)}")
        return v


class CORResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    cor_number: int
    title: str
    description: str | None = None
    status: str
    markup_pct: Decimal
    overhead_pct: Decimal
    cor_adjustment: Decimal
    total_cost: Decimal
    schedule_impact_days: int
    pco_ids: list[uuid.UUID] = []
    submitted_to: uuid.UUID | None = None
    approved_by: uuid.UUID | None = None
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CORListResponse(BaseModel):
    data: list[CORResponse]
    meta: PaginationMeta
