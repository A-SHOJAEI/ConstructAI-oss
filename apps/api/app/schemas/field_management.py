"""Pydantic schemas for field management endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.pagination import PaginationMeta

# ---------------------------------------------------------------------------
# Equipment
# ---------------------------------------------------------------------------


EquipmentStatus = Literal["available", "in_use", "maintenance", "retired"]
PermitStatus = Literal["pending", "submitted", "approved", "expired", "rejected"]
PunchListPriority = Literal["low", "medium", "high", "critical"]
PunchListStatus = Literal["open", "in_progress", "resolved", "verified"]
RiskProbability = Literal["low", "medium", "high", "very_high"]
RiskImpact = Literal["low", "medium", "high", "very_high"]
RiskStatus = Literal["identified", "mitigated", "accepted", "closed"]


class EquipmentCreate(BaseModel):
    project_id: uuid.UUID
    equipment_type: str
    make: str | None = None
    model: str | None = None
    serial_number: str | None = None
    status: EquipmentStatus = "available"
    daily_rate: Decimal | None = None
    location: str | None = None
    maintenance_due_date: date | None = None
    last_inspection_date: date | None = None
    operator_id: uuid.UUID | None = None
    notes: str | None = None
    metadata_: dict = Field(default_factory=dict, alias="metadata")


class EquipmentUpdate(BaseModel):
    equipment_type: str | None = None
    make: str | None = None
    model: str | None = None
    serial_number: str | None = None
    status: EquipmentStatus | None = None
    daily_rate: Decimal | None = None
    location: str | None = None
    maintenance_due_date: date | None = None
    last_inspection_date: date | None = None
    operator_id: uuid.UUID | None = None
    notes: str | None = None
    metadata_: dict | None = Field(default=None, alias="metadata")


class EquipmentResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    equipment_type: str
    make: str | None = None
    model: str | None = None
    serial_number: str | None = None
    status: str
    daily_rate: Decimal | None = None
    location: str | None = None
    maintenance_due_date: date | None = None
    last_inspection_date: date | None = None
    operator_id: uuid.UUID | None = None
    notes: str | None = None
    metadata_: dict = Field(default_factory=dict, alias="metadata")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class EquipmentListResponse(BaseModel):
    data: list[EquipmentResponse]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------


class MaterialCreate(BaseModel):
    project_id: uuid.UUID
    name: str
    category: str | None = None
    csi_code: str | None = None
    unit: str
    quantity_ordered: Decimal = Decimal("0")
    quantity_received: Decimal = Decimal("0")
    quantity_installed: Decimal = Decimal("0")
    unit_cost: Decimal | None = None
    supplier: str | None = None
    lead_time_days: int | None = None
    expected_delivery: date | None = None
    status: str = "ordered"
    storage_location: str | None = None
    notes: str | None = None


class MaterialUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    csi_code: str | None = None
    unit: str | None = None
    quantity_ordered: Decimal | None = None
    quantity_received: Decimal | None = None
    quantity_installed: Decimal | None = None
    unit_cost: Decimal | None = None
    supplier: str | None = None
    lead_time_days: int | None = None
    expected_delivery: date | None = None
    status: str | None = None
    storage_location: str | None = None
    notes: str | None = None


class MaterialResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    category: str | None = None
    csi_code: str | None = None
    unit: str
    quantity_ordered: Decimal
    quantity_received: Decimal
    quantity_installed: Decimal
    unit_cost: Decimal | None = None
    supplier: str | None = None
    lead_time_days: int | None = None
    expected_delivery: date | None = None
    status: str
    storage_location: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MaterialListResponse(BaseModel):
    data: list[MaterialResponse]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# Permit
# ---------------------------------------------------------------------------


class PermitCreate(BaseModel):
    project_id: uuid.UUID
    permit_type: str
    permit_number: str | None = None
    issuing_authority: str
    status: PermitStatus = "pending"
    application_date: date | None = None
    approval_date: date | None = None
    expiration_date: date | None = None
    conditions: list[dict] = Field(default_factory=list)
    inspections: list[dict] = Field(default_factory=list)
    documents: list[dict] = Field(default_factory=list)
    notes: str | None = None


class PermitUpdate(BaseModel):
    permit_type: str | None = None
    permit_number: str | None = None
    issuing_authority: str | None = None
    status: PermitStatus | None = None
    application_date: date | None = None
    approval_date: date | None = None
    expiration_date: date | None = None
    conditions: list[dict] | None = None
    inspections: list[dict] | None = None
    documents: list[dict] | None = None
    notes: str | None = None


class PermitResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    permit_type: str
    permit_number: str | None = None
    issuing_authority: str
    status: str
    application_date: date | None = None
    approval_date: date | None = None
    expiration_date: date | None = None
    conditions: list
    inspections: list
    documents: list
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PermitListResponse(BaseModel):
    data: list[PermitResponse]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# PunchListItem
# ---------------------------------------------------------------------------


class PunchListItemCreate(BaseModel):
    project_id: uuid.UUID
    item_number: str
    description: str
    location: str | None = None
    category: str | None = None
    priority: PunchListPriority = "medium"
    status: PunchListStatus = "open"
    assigned_to: uuid.UUID | None = None
    due_date: date | None = None
    photos: list[dict] = Field(default_factory=list)
    notes: str | None = None


class PunchListItemUpdate(BaseModel):
    item_number: str | None = None
    description: str | None = None
    location: str | None = None
    category: str | None = None
    priority: PunchListPriority | None = None
    status: PunchListStatus | None = None
    assigned_to: uuid.UUID | None = None
    due_date: date | None = None
    completed_date: date | None = None
    photos: list[dict] | None = None
    notes: str | None = None


class PunchListItemResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    item_number: str
    description: str
    location: str | None = None
    category: str | None = None
    priority: str
    status: str
    assigned_to: uuid.UUID | None = None
    created_by: uuid.UUID | None = None
    due_date: date | None = None
    completed_date: date | None = None
    photos: list
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PunchListItemListResponse(BaseModel):
    data: list[PunchListItemResponse]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# RiskRegisterEntry
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PunchListItem v2 schemas
# ---------------------------------------------------------------------------


class PunchListCreate(BaseModel):
    """Create a punch list (walkthrough grouping)."""

    name: str
    description: str | None = None
    walk_date: date | None = None
    participants: list[dict] = Field(default_factory=list)


class PunchListUpdate(BaseModel):
    """Update a punch list."""

    name: str | None = None
    description: str | None = None
    walk_date: date | None = None
    status: str | None = None
    participants: list[dict] | None = None


class PunchListResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None = None
    walk_date: date | None = None
    status: str
    participants: list
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    item_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class PunchListListResponse(BaseModel):
    data: list[PunchListResponse]
    meta: PaginationMeta


class PunchListItemCreateV2(BaseModel):
    description: str
    location: str | None = None
    category: str | None = None
    priority: PunchListPriority = "medium"
    assigned_to: uuid.UUID | None = None
    due_date: date | None = None
    photos: list[dict] = Field(default_factory=list)
    notes: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    drawing_reference: str | None = None
    company: str | None = None
    spec_section: str | None = None
    punch_list_id: uuid.UUID | None = None


class PunchListItemUpdateV2(BaseModel):
    description: str | None = None
    location: str | None = None
    category: str | None = None
    priority: PunchListPriority | None = None
    status: PunchListStatus | None = None
    assigned_to: uuid.UUID | None = None
    due_date: date | None = None
    completed_date: date | None = None
    photos: list[dict] | None = None
    notes: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    drawing_reference: str | None = None
    company: str | None = None
    spec_section: str | None = None
    punch_list_id: uuid.UUID | None = None
    verified_by: uuid.UUID | None = None


class PunchListBulkCreateItem(BaseModel):
    description: str
    location: str | None = None
    category: str | None = None
    priority: PunchListPriority = "medium"
    assigned_to: uuid.UUID | None = None
    due_date: date | None = None
    notes: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    drawing_reference: str | None = None
    company: str | None = None
    spec_section: str | None = None
    punch_list_id: uuid.UUID | None = None


class PunchListBulkCreateRequest(BaseModel):
    items: list[PunchListBulkCreateItem] = Field(..., min_length=1, max_length=50)


class PunchListBulkStatusUpdate(BaseModel):
    item_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=100)
    status: PunchListStatus


class PunchListDetailResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    punch_list_id: uuid.UUID | None = None
    item_number: str
    description: str
    location: str | None = None
    category: str | None = None
    priority: str
    status: str
    assigned_to: uuid.UUID | None = None
    created_by: uuid.UUID | None = None
    due_date: date | None = None
    completed_date: date | None = None
    photos: list
    notes: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    drawing_reference: str | None = None
    company: str | None = None
    spec_section: str | None = None
    verified_by: uuid.UUID | None = None
    date_verified: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PunchListDetailListResponse(BaseModel):
    data: list[PunchListDetailResponse]
    meta: PaginationMeta


class PunchListStatsResponse(BaseModel):
    total: int
    open: int
    in_progress: int
    resolved: int
    verified: int
    by_priority: dict
    by_company: dict
    overdue: int


class RiskRegisterEntryCreate(BaseModel):
    project_id: uuid.UUID
    risk_id: str
    description: str
    category: str | None = None
    probability: RiskProbability = "medium"
    impact: RiskImpact = "medium"
    risk_score: Decimal | None = None
    mitigation_strategy: str | None = None
    contingency_plan: str | None = None
    owner_id: uuid.UUID | None = None
    status: RiskStatus = "identified"
    trigger_conditions: str | None = None
    response_actions: list[dict] = Field(default_factory=list)


class RiskRegisterEntryUpdate(BaseModel):
    risk_id: str | None = None
    description: str | None = None
    category: str | None = None
    probability: RiskProbability | None = None
    impact: RiskImpact | None = None
    risk_score: Decimal | None = None
    mitigation_strategy: str | None = None
    contingency_plan: str | None = None
    owner_id: uuid.UUID | None = None
    status: RiskStatus | None = None
    trigger_conditions: str | None = None
    response_actions: list[dict] | None = None


class RiskRegisterEntryResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    risk_id: str
    description: str
    category: str | None = None
    probability: str
    impact: str
    risk_score: Decimal | None = None
    mitigation_strategy: str | None = None
    contingency_plan: str | None = None
    owner_id: uuid.UUID | None = None
    status: str
    trigger_conditions: str | None = None
    response_actions: list
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RiskRegisterEntryListResponse(BaseModel):
    data: list[RiskRegisterEntryResponse]
    meta: PaginationMeta
