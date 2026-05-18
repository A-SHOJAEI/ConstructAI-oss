"""Pydantic schemas for project controls endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta


class EVMSnapshotCreate(BaseModel):
    project_id: uuid.UUID
    snapshot_date: date
    bac: Decimal = Field(ge=0)
    pv: Decimal = Field(ge=0)
    ev: Decimal = Field(ge=0)
    ac: Decimal = Field(ge=0)


class EVMSnapshotResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    snapshot_date: date
    bac: Decimal
    pv: Decimal
    ev: Decimal
    ac: Decimal
    sv: Decimal
    cv: Decimal
    spi: Decimal
    cpi: Decimal
    eac: Decimal
    etc: Decimal
    vac: Decimal
    tcpi: Decimal
    percent_complete: Decimal
    data_date: date
    created_at: datetime

    model_config = {"from_attributes": True}


class EVMSnapshotListResponse(BaseModel):
    data: list[EVMSnapshotResponse]
    meta: PaginationMeta


class EACForecastResponse(BaseModel):
    id: uuid.UUID
    snapshot_id: uuid.UUID
    project_id: uuid.UUID
    method: str
    eac_value: Decimal
    confidence_low: Decimal | None = None
    confidence_high: Decimal | None = None
    model_params: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class ChangeOrderCreate(BaseModel):
    project_id: uuid.UUID
    co_number: str
    title: str
    description: str
    change_type: str = Field(
        pattern=(
            r"^(owner_directed|field_condition"
            r"|design_error|value_engineering|regulatory|unforeseen_condition)$"
        )
    )
    cost_impact: Decimal = Decimal("0")
    schedule_impact_days: int = 0


class ChangeOrderResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    co_number: str
    title: str
    description: str
    status: str
    change_type: str
    requested_by: uuid.UUID | None = None
    cost_impact: Decimal
    schedule_impact_days: int
    risk_score: Decimal | None = None
    ai_analysis: dict
    submitted_at: datetime
    resolved_at: datetime | None = None
    # Lifecycle fields
    cor_id: uuid.UUID | None = None
    approved_date: datetime | None = None
    executed_date: datetime | None = None
    # Cost breakdown
    labor_cost: Decimal = Decimal("0")
    material_cost: Decimal = Decimal("0")
    equipment_cost: Decimal = Decimal("0")
    subcontractor_cost: Decimal = Decimal("0")
    overhead_cost: Decimal = Decimal("0")
    markup_pct: Decimal = Decimal("0")
    overhead_pct: Decimal = Decimal("0")
    # Contract adjustment tracking
    original_contract_sum: Decimal | None = None
    previous_cos_sum: Decimal | None = None
    this_co_amount: Decimal | None = None
    new_contract_sum: Decimal | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChangeOrderListResponse(BaseModel):
    data: list[ChangeOrderResponse]
    meta: PaginationMeta


class MonteCarloScheduleRequest(BaseModel):
    project_id: uuid.UUID
    baseline_id: uuid.UUID | None = None
    num_iterations: int = Field(default=10000, ge=100, le=100000)


class MonteCarloScheduleResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    num_iterations: int
    p10_duration: int
    p50_duration: int
    p80_duration: int
    p90_duration: int
    mean_duration: Decimal
    std_dev: Decimal
    critical_risk_drivers: list[dict]
    histogram_data: list[float]
    created_at: datetime

    model_config = {"from_attributes": True}


class SCurveDataPoint(BaseModel):
    date: date
    planned_value: Decimal
    earned_value: Decimal
    actual_cost: Decimal


class SCurveResponse(BaseModel):
    project_id: uuid.UUID
    data_points: list[SCurveDataPoint]
    bac: Decimal
    forecast_completion: date | None = None
