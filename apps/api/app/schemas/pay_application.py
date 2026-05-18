"""Schemas for AIA G702/G703 Pay Applications."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.schemas.pagination import PaginationMeta

PAY_APP_STATUSES = {"draft", "submitted", "reviewed", "certified", "paid", "rejected"}


# ---------------------------------------------------------------------------
# Schedule of Values (SOV)
# ---------------------------------------------------------------------------


class SOVLineItemCreate(BaseModel):
    item_number: str
    description: str
    scheduled_value: Decimal = Field(ge=0)
    csi_code: str | None = None
    sort_order: int = 0


class SOVBulkCreate(BaseModel):
    project_id: uuid.UUID
    line_items: list[SOVLineItemCreate] = Field(min_length=1)


class SOVLineItemResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    item_number: str
    description: str
    scheduled_value: Decimal
    csi_code: str | None = None
    change_order_id: uuid.UUID | None = None
    is_change_order_line: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SOVListResponse(BaseModel):
    data: list[SOVLineItemResponse]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# Pay Application Line Item (G703 Continuation Sheet)
# ---------------------------------------------------------------------------


class PayAppLineItemInput(BaseModel):
    """Input for a single G703 line — only user-editable columns."""

    sov_id: uuid.UUID | None = None
    item_number: str
    description_of_work: str
    scheduled_value: Decimal = Field(ge=0)
    work_completed_this_period: Decimal = Field(ge=0, default=Decimal("0"))
    materials_presently_stored: Decimal = Field(ge=0, default=Decimal("0"))
    retainage_pct: Decimal = Field(ge=0, le=100, default=Decimal("10.00"))


class PayAppLineItemResponse(BaseModel):
    id: uuid.UUID
    pay_application_id: uuid.UUID
    sov_id: uuid.UUID | None = None
    item_number: str
    description_of_work: str
    scheduled_value: Decimal
    work_completed_previous: Decimal
    work_completed_this_period: Decimal
    materials_presently_stored: Decimal
    total_completed_and_stored: Decimal  # computed: D+E+F
    percent_complete: Decimal  # computed: G/C
    balance_to_finish: Decimal  # computed: C-G
    retainage_pct: Decimal
    sort_order: int

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Pay Application (G702 Header)
# ---------------------------------------------------------------------------


class PayApplicationCreate(BaseModel):
    project_id: uuid.UUID
    period_to: date
    contractor_info: dict = {}
    architect_info: dict = {}
    retainage_pct: Decimal = Field(ge=0, le=100, default=Decimal("10.00"))
    line_items: list[PayAppLineItemInput] = Field(min_length=1)


class PayApplicationUpdate(BaseModel):
    """Update a draft pay application."""

    period_to: date | None = None
    contractor_info: dict | None = None
    architect_info: dict | None = None
    retainage_pct: Decimal | None = None
    line_items: list[PayAppLineItemInput] | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in PAY_APP_STATUSES:
            raise ValueError(f"status must be one of {sorted(PAY_APP_STATUSES)}")
        return v


class PayApplicationResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    application_number: int
    period_to: date
    contractor_info: dict
    architect_info: dict
    original_contract_sum: Decimal
    net_change_by_cos: Decimal
    contract_sum_to_date: Decimal
    total_completed_and_stored: Decimal
    retainage_pct: Decimal
    retainage_work_completed: Decimal
    retainage_stored_materials: Decimal
    total_retainage: Decimal
    total_earned_less_retainage: Decimal
    less_previous_certificates: Decimal
    current_payment_due: Decimal
    balance_to_finish_including_retainage: Decimal
    status: str
    submitted_by: uuid.UUID | None = None
    certified_by: uuid.UUID | None = None
    submitted_at: datetime | None = None
    certified_at: datetime | None = None
    paid_at: datetime | None = None
    line_items: list[PayAppLineItemResponse]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PayApplicationSummary(BaseModel):
    """Lighter response for list views (no line items)."""

    id: uuid.UUID
    project_id: uuid.UUID
    application_number: int
    period_to: date
    contract_sum_to_date: Decimal
    total_completed_and_stored: Decimal
    current_payment_due: Decimal
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PayApplicationListResponse(BaseModel):
    data: list[PayApplicationSummary]
    meta: PaginationMeta
