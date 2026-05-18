"""Schemas for certified payroll and prevailing wage endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.schemas.pagination import PaginationMeta

COMPLIANCE_STATUSES = {"compliant", "underpayment", "review"}
REPORT_STATUSES = {"draft", "certified", "submitted"}
DATA_SOURCES = {"davis_bacon", "state", "custom"}

_PAYROLL_DISCLAIMER = (
    "These calculations are estimates only. "
    "Consult a licensed professional for compliance purposes."
)


# ---------------------------------------------------------------------------
# Prevailing Wage Rate
# ---------------------------------------------------------------------------


class PrevailingWageRateResponse(BaseModel):
    id: uuid.UUID
    location_state: str
    location_county: str | None = None
    trade: str
    base_rate: Decimal
    fringe_rate: Decimal
    total_rate: Decimal
    effective_date: date
    expiration_date: date | None = None
    determination_number: str | None = None
    data_source: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Payroll Records
# ---------------------------------------------------------------------------


class PayrollRecordCreate(BaseModel):
    worker_name: str = Field(min_length=1, max_length=200)
    worker_id: str | None = Field(default=None, max_length=20)
    trade: str = Field(min_length=1, max_length=100)
    classification: str = Field(min_length=1, max_length=100)
    pay_period_start: date
    pay_period_end: date
    hours_straight: Decimal = Field(ge=0, default=Decimal("0"))
    hours_overtime: Decimal = Field(ge=0, default=Decimal("0"))
    hours_other: Decimal = Field(ge=0, default=Decimal("0"))
    rate_straight: Decimal = Field(ge=0)
    rate_overtime: Decimal | None = None
    fringe_rate: Decimal = Field(ge=0, default=Decimal("0"))
    fringe_breakdown: dict | None = None
    deductions: dict = Field(default_factory=dict)
    net_pay: Decimal | None = None
    state: str | None = Field(default=None, max_length=2)
    county: str | None = None

    @field_validator("pay_period_end")
    @classmethod
    def end_after_start(cls, v: date, info) -> date:
        start = info.data.get("pay_period_start")
        if start and v < start:
            raise ValueError("pay_period_end must be on or after pay_period_start")
        return v


class PayrollRecordBatchCreate(BaseModel):
    records: list[PayrollRecordCreate] = Field(min_length=1, max_length=500)


class PayrollRecordResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    worker_name: str
    worker_id: str | None = None
    trade: str
    classification: str
    pay_period_start: date
    pay_period_end: date
    hours_straight: Decimal
    hours_overtime: Decimal
    hours_other: Decimal
    rate_straight: Decimal
    rate_overtime: Decimal
    gross_pay: Decimal
    deductions: dict
    net_pay: Decimal
    fringe_benefits: dict
    prevailing_wage_rate_id: uuid.UUID | None = None
    compliance_status: str
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PayrollRecordListResponse(BaseModel):
    data: list[PayrollRecordResponse]
    meta: PaginationMeta


# ---------------------------------------------------------------------------
# Certified Payroll Reports
# ---------------------------------------------------------------------------


class CertifiedReportGenerateRequest(BaseModel):
    pay_period_start: date
    pay_period_end: date
    contractor_name: str = Field(min_length=1, max_length=200)
    contractor_address: str | None = None
    contract_number: str | None = None
    signer_name: str | None = None
    signer_title: str | None = None


class CertifiedPayrollReportResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    report_number: str
    pay_period_start: date
    pay_period_end: date
    contractor_name: str
    contractor_address: str | None = None
    project_name: str
    contract_number: str | None = None
    payroll_records: list | dict
    total_gross_pay: Decimal
    total_fringe: Decimal
    status: str
    certified_by: uuid.UUID | None = None
    certified_at: datetime | None = None
    submission_reference: str | None = None
    created_at: datetime
    compliance_status: str = "beta"
    disclaimer: str = _PAYROLL_DISCLAIMER

    model_config = {"from_attributes": True}


class CertifiedPayrollReportSummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    report_number: str
    pay_period_start: date
    pay_period_end: date
    contractor_name: str
    total_gross_pay: Decimal
    total_fringe: Decimal
    status: str
    certified_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CertifiedPayrollReportListResponse(BaseModel):
    data: list[CertifiedPayrollReportSummary]
    meta: PaginationMeta
    total: int = 0


# ---------------------------------------------------------------------------
# Compliance Summary
# ---------------------------------------------------------------------------


class ComplianceSummaryResponse(BaseModel):
    total_records: int
    compliant_records: int
    underpayment_records: int
    review_records: int
    compliance_rate: Decimal
    total_underpayment: Decimal
    trades_with_issues: list[str]
    compliance_status: str = "beta"
    disclaimer: str = _PAYROLL_DISCLAIMER
