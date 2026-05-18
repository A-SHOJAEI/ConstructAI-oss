"""Pydantic schemas for WageGuard prevailing wage compliance."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class WageConfigUpdate(BaseModel):
    """Partial update for project wage configuration."""

    wage_determination_id: uuid.UUID | None = None
    project_type: str | None = None
    apprenticeship_required: bool | None = None
    apprenticeship_pct: float | None = None
    ira_credit_multiplier: float | None = None


class PayrollCreate(BaseModel):
    """Create a new certified payroll record."""

    contractor_name: str
    week_ending: date


class PayrollLineItemCreate(BaseModel):
    """Add a worker line item to a payroll."""

    worker_name: str
    worker_last4_ssn: str | None = Field(default=None, max_length=4)
    classification: str | None = None
    is_apprentice: bool = False
    apprentice_program: str | None = None
    hours_straight: float
    hours_overtime: float = 0
    rate_paid: float
    fringe_paid: float = 0


class PayrollStatusUpdate(BaseModel):
    """Update payroll status."""

    status: str
    review_notes: str | None = None


class ClassificationMapRequest(BaseModel):
    """Request to map a company classification to Davis-Bacon."""

    company_classification: str
    project_type: str = "building"


class SubInviteRequest(BaseModel):
    """Invite a subcontractor to submit payrolls."""

    email: str
    contractor_name: str


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class WageDeterminationResponse(BaseModel):
    """Wage determination with classification details."""

    id: uuid.UUID
    sam_gov_id: str | None = None
    state: str
    county: str
    project_type: str
    effective_date: date | None = None
    classifications: list[dict]

    model_config = {"from_attributes": True}


class WageConfigResponse(BaseModel):
    """Project wage configuration response."""

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    wage_determination_id: uuid.UUID | None = None
    project_type: str
    apprenticeship_required: bool
    apprenticeship_pct: Decimal
    ira_credit_multiplier: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PayrollLineItemResponse(BaseModel):
    """Payroll line item response."""

    id: uuid.UUID
    payroll_id: uuid.UUID
    worker_name: str
    classification: str | None = None
    is_apprentice: bool
    apprentice_program: str | None = None
    hours_straight: Decimal
    hours_overtime: Decimal
    rate_paid: Decimal | None = None
    fringe_paid: Decimal | None = None
    prevailing_rate: Decimal | None = None
    prevailing_fringe: Decimal | None = None
    compliant: bool | None = None
    deficiency_amount: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}


class ComplianceFlag(BaseModel):
    """A single compliance issue found during validation."""

    type: str
    description: str
    severity: str


class PayrollResponse(BaseModel):
    """Certified payroll response."""

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    contractor_name: str | None = None
    week_ending: date
    payroll_number: int
    status: str
    total_hours: Decimal
    total_gross_pay: Decimal
    compliance_flags: list[dict]
    certified_at: datetime | None = None
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    review_notes: str | None = None
    wh347_pdf_s3_key: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ValidationResult(BaseModel):
    """Result of payroll compliance validation."""

    payroll_id: uuid.UUID
    compliant: bool
    flags: list[ComplianceFlag]


class ApprenticeshipStatusResponse(BaseModel):
    """Apprenticeship compliance status for a project."""

    total_labor_hours: float
    apprentice_hours: float
    apprentice_pct: float
    required_pct: float
    compliant: bool
    hours_deficit: float
    projected_compliance_date: date | None = None


class ClassificationMapResponse(BaseModel):
    """Result of classification mapping."""

    suggested_davis_bacon: str
    confidence: float


class AuditPackageResponse(BaseModel):
    """Summary of wage compliance for audit purposes."""

    payroll_count: int
    total_line_items: int
    apprenticeship_status: dict
    compliance_issues: int
    sub_count: int
