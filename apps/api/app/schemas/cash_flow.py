"""Schemas for Predictive Cash Flow: forecasts, lien waivers, analysis."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.schemas.pagination import PaginationMeta

WAIVER_TYPES = {
    "conditional_partial",
    "conditional_final",
    "unconditional_partial",
    "unconditional_final",
}

WAIVER_STATUSES = {"pending", "received", "void"}


# ---------------------------------------------------------------------------
# Cash flow forecast
# ---------------------------------------------------------------------------


class CashFlowConfigRequest(BaseModel):
    """Configuration for cash flow forecast generation."""

    payment_lag_owner_days: int = Field(default=30, ge=5, le=90)
    payment_lag_sub_days: int = Field(default=45, ge=15, le=120)
    retainage_pct: Decimal = Field(default=Decimal("10"), ge=0, le=20)
    include_monte_carlo: bool = True
    num_simulations: int = Field(default=5000, ge=100, le=10000)


class MonthlyCashPointSchema(BaseModel):
    """Monthly cash flow data point."""

    month: str
    planned_billings: str
    actual_billings: str
    expected_receipts: str
    actual_receipts: str
    net_cash_position: str
    cumulative_billed: str
    cumulative_received: str


class CashFlowSummarySchema(BaseModel):
    """Cash flow summary totals."""

    total_contract_value: str
    total_billed: str
    total_received: str
    retainage_held: str
    months_remaining: int


class ConfidenceIntervalSchema(BaseModel):
    """Monte Carlo confidence intervals."""

    p10: list[str]
    p50: list[str]
    p90: list[str]
    worst_month_position: str
    months_negative: int


class CashFlowForecastResponse(BaseModel):
    """Full cash flow forecast response."""

    project_id: str
    snapshot_id: str | None = None
    monthly_projections: list[MonthlyCashPointSchema]
    summary: CashFlowSummarySchema
    confidence_intervals: ConfidenceIntervalSchema | None = None
    risk_indicators: list[str]
    generated_at: str | None = None


# ---------------------------------------------------------------------------
# Lien waivers
# ---------------------------------------------------------------------------


class LienWaiverCreate(BaseModel):
    """Create a new lien waiver."""

    pay_application_id: uuid.UUID | None = None
    waiver_type: str
    vendor_name: str = Field(min_length=1, max_length=500)
    amount: Decimal | None = Field(default=None, gt=0)
    through_date: date | None = None
    signed_date: date | None = None
    status: str = "pending"
    document_url: str | None = None
    notes: str | None = None

    @field_validator("waiver_type")
    @classmethod
    def validate_waiver_type(cls, v: str) -> str:
        if v not in WAIVER_TYPES:
            raise ValueError(f"waiver_type must be one of {sorted(WAIVER_TYPES)}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in WAIVER_STATUSES:
            raise ValueError(f"status must be one of {sorted(WAIVER_STATUSES)}")
        return v


class LienWaiverUpdate(BaseModel):
    """Update a lien waiver."""

    status: str | None = None
    signed_date: date | None = None
    document_url: str | None = None
    notes: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in WAIVER_STATUSES:
            raise ValueError(f"status must be one of {sorted(WAIVER_STATUSES)}")
        return v


class LienWaiverResponse(BaseModel):
    """Lien waiver response."""

    id: uuid.UUID
    project_id: uuid.UUID
    pay_application_id: uuid.UUID | None = None
    waiver_type: str
    vendor_name: str
    amount: Decimal | None = None
    through_date: date | None = None
    signed_date: date | None = None
    status: str
    document_url: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LienWaiverListResponse(BaseModel):
    """Paginated lien waiver list."""

    data: list[LienWaiverResponse]
    meta: PaginationMeta


class LienWaiverAnalysisResponse(BaseModel):
    """Lien waiver coverage analysis."""

    coverage_pct: str
    missing_waivers: list[dict]
    upcoming_deadlines: list[dict]
