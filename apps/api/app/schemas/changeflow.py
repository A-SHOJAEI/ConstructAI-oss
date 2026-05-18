"""Pydantic schemas for the ChangeFlow T&M product."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Valid entry types and negotiation actions
# ---------------------------------------------------------------------------

TM_ENTRY_TYPES = {"labor", "material", "equipment", "subcontractor"}

NEGOTIATION_ACTIONS = {
    "submitted",
    "returned",
    "revised",
    "counter_offer",
    "approved",
    "rejected",
}


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class TmEntryCreate(BaseModel):
    """Create a T&M line item."""

    entry_type: str
    entry_date: date | None = None
    worker_name: str | None = None
    classification: str | None = None
    straight_hours: float | None = None
    overtime_hours: float | None = None
    labor_rate: float | None = None
    ot_rate: float | None = None
    material_description: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_cost: float | None = None
    vendor: str | None = None
    equipment_type: str | None = None
    equipment_hours: float | None = None
    equipment_rate: float | None = None
    sub_name: str | None = None
    sub_scope: str | None = None
    sub_amount: float | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None
    photos: list[str] = Field(default_factory=list)
    voice_note_s3_key: str | None = None
    notes: str | None = None

    @field_validator("entry_type")
    @classmethod
    def validate_entry_type(cls, v: str) -> str:
        if v not in TM_ENTRY_TYPES:
            raise ValueError(f"entry_type must be one of {sorted(TM_ENTRY_TYPES)}")
        return v


class CorGenerateRequest(BaseModel):
    """Request to generate a COR from T&M entries."""

    change_event_id: uuid.UUID
    subject: str | None = None


class NegotiationCreate(BaseModel):
    """Record a negotiation action on a COR."""

    action: str
    amount: float | None = None
    notes: str | None = None

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in NEGOTIATION_ACTIONS:
            raise ValueError(f"action must be one of {sorted(NEGOTIATION_ACTIONS)}")
        return v


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class TmEntryResponse(BaseModel):
    """T&M entry detail."""

    id: uuid.UUID
    change_event_id: uuid.UUID | None = None
    project_id: uuid.UUID
    organization_id: uuid.UUID
    entry_date: date
    entry_type: str
    worker_name: str | None = None
    classification: str | None = None
    straight_hours: float | None = None
    overtime_hours: float | None = None
    labor_rate: float | None = None
    ot_rate: float | None = None
    material_description: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_cost: float | None = None
    vendor: str | None = None
    equipment_type: str | None = None
    equipment_hours: float | None = None
    equipment_rate: float | None = None
    sub_name: str | None = None
    sub_scope: str | None = None
    sub_amount: float | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None
    photos: list[str] = []
    voice_note_s3_key: str | None = None
    notes: str | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TmSummaryResponse(BaseModel):
    """Aggregated T&M summary for a change event."""

    labor_subtotal: float
    material_subtotal: float
    equipment_subtotal: float
    sub_subtotal: float
    entry_count: int
    entries: list[TmEntryResponse]


class PricingSummaryResponse(BaseModel):
    """Full pricing breakdown with markup cascade."""

    labor_subtotal: float
    labor_burden: float
    labor_total: float
    material_subtotal: float
    material_tax: float
    material_total: float
    equipment_total: float
    sub_total: float
    direct_cost_subtotal: float
    overhead_amount: float
    profit_amount: float
    bond_amount: float
    grand_total: float


class CorNegotiationResponse(BaseModel):
    """COR negotiation history entry."""

    id: uuid.UUID
    cor_id: uuid.UUID
    action: str
    amount: float | None = None
    notes: str | None = None
    acted_by: uuid.UUID | None = None
    acted_at: datetime

    model_config = {"from_attributes": True}


class ChangeFlowDashboardResponse(BaseModel):
    """ChangeFlow dashboard aggregate metrics."""

    pending_value: float
    approved_to_date: float
    rejected_value: float
    total_events: int
    total_cors: int
    avg_processing_days: float | None = None
