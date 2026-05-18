"""Schemas for insurance and risk data export endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.schemas.pagination import PaginationMeta

EXPORT_TYPES = {"emr", "safety_summary", "loss_run", "risk_profile", "osha_300"}
EXPORT_FORMATS = {"csv", "pdf", "json"}

_INSURANCE_DISCLAIMER = (
    "These calculations are estimates only. "
    "Consult a licensed professional for compliance purposes."
)


# ---------------------------------------------------------------------------
# Safety Summary
# ---------------------------------------------------------------------------


class SafetySummaryRequest(BaseModel):
    project_id: uuid.UUID | None = None
    date_range_start: date
    date_range_end: date

    @field_validator("date_range_end")
    @classmethod
    def end_after_start(cls, v: date, info) -> date:
        start = info.data.get("date_range_start")
        if start and v < start:
            raise ValueError("date_range_end must be on or after date_range_start")
        return v


class SafetySummaryResponse(BaseModel):
    org_id: str
    project_id: str | None = None
    date_range_start: date
    date_range_end: date
    total_hours_worked: Decimal
    total_recordable_incidents: int
    trir: Decimal
    dart_incidents: int
    dart_rate: Decimal
    lost_time_injuries: int
    ltir: Decimal
    near_misses: int
    near_miss_frequency: Decimal
    severity_rate: Decimal
    lost_workdays: int
    incident_by_type: dict = Field(default_factory=dict)
    incident_by_body_part: dict = Field(default_factory=dict)
    incident_by_cause: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# EMR
# ---------------------------------------------------------------------------


class EMRCalculateRequest(BaseModel):
    payroll_by_class: dict[str, Decimal] = Field(
        description="NCCI class code to payroll amount mapping"
    )
    year: int = Field(ge=2000, le=2100)
    actual_losses: Decimal | None = Field(
        default=None,
        description="Override actual losses (auto-calculated from incidents if None)",
    )
    expected_losses: Decimal | None = Field(
        default=None,
        description="Override expected losses (auto-calculated from payroll if None)",
    )
    ballast_value: Decimal | None = None
    weighting_factor: Decimal | None = None


class EMRResultResponse(BaseModel):
    emr_value: Decimal
    actual_primary: Decimal
    actual_excess: Decimal
    expected_primary: Decimal
    expected_excess: Decimal
    weighting_factor: Decimal
    ballast_value: Decimal
    formula_numerator: Decimal
    formula_denominator: Decimal


class EMRExportResponse(BaseModel):
    emr_result: EMRResultResponse
    payroll_by_class: dict
    expected_losses_by_class: dict
    actual_losses_detail: list[dict]
    total_payroll: Decimal
    total_expected_losses: Decimal
    total_actual_losses: Decimal
    calculation_year: int
    compliance_status: str = "beta"
    disclaimer: str = _INSURANCE_DISCLAIMER


class EMRCalculationResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    calculation_year: int
    actual_losses: Decimal
    expected_losses: Decimal
    emr_value: Decimal
    payroll_by_class: dict
    loss_detail: dict | list
    naics_code: str | None = None
    created_at: datetime
    compliance_status: str = "beta"
    disclaimer: str = _INSURANCE_DISCLAIMER

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Loss Run
# ---------------------------------------------------------------------------


class LossRunEntryResponse(BaseModel):
    incident_date: date
    incident_type: str
    description: str
    medical_cost: Decimal
    indemnity_cost: Decimal
    property_cost: Decimal
    total_cost: Decimal
    status: str
    reserve_amount: Decimal
    claimant: str


class LossRunResponse(BaseModel):
    org_id: str
    date_range_start: date
    date_range_end: date
    entries: list[LossRunEntryResponse]
    total_medical: Decimal
    total_indemnity: Decimal
    total_property: Decimal
    total_incurred: Decimal
    total_reserved: Decimal
    open_claims: int
    closed_claims: int


# ---------------------------------------------------------------------------
# Risk Profile
# ---------------------------------------------------------------------------


class RiskProfileResponse(BaseModel):
    org_id: str
    project_id: str | None = None
    trir_trend: list[dict]
    top_risk_categories: list[dict]
    ppe_compliance_rate: Decimal
    training_hours: Decimal
    predictive_risk_scores: dict = Field(default_factory=dict)
    mitigation_effectiveness: dict = Field(default_factory=dict)
    emr_history: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# OSHA 300
# ---------------------------------------------------------------------------


class OSHA300EntryResponse(BaseModel):
    case_number: str
    employee_name: str
    job_title: str
    date_of_injury: date
    where_event_occurred: str
    description: str
    classified_as: str
    days_away: int
    days_restricted: int


class OSHA300LogResponse(BaseModel):
    establishment_name: str
    org_id: str
    year: int
    entries: list[OSHA300EntryResponse]
    total_deaths: int
    total_days_away_cases: int
    total_restricted_cases: int
    total_other_recordable: int
    total_days_away: int
    total_days_restricted: int


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    export_type: str
    format: str = "csv"
    project_id: uuid.UUID | None = None
    date_range_start: date
    date_range_end: date

    @field_validator("export_type")
    @classmethod
    def validate_export_type(cls, v: str) -> str:
        if v not in EXPORT_TYPES:
            raise ValueError(f"export_type must be one of {sorted(EXPORT_TYPES)}")
        return v

    @field_validator("format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        if v not in EXPORT_FORMATS:
            raise ValueError(f"format must be one of {sorted(EXPORT_FORMATS)}")
        return v


class InsuranceExportResponse(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID | None = None
    export_type: str
    date_range_start: date
    date_range_end: date
    export_data: dict
    file_url: str | None = None
    requested_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class InsuranceExportListResponse(BaseModel):
    data: list[InsuranceExportResponse]
    meta: PaginationMeta
    total: int = 0
