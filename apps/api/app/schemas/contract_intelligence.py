"""Pydantic schemas for contract intelligence endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class ContractDocumentCreate(BaseModel):
    """Create a new contract document record."""

    contract_type: str = Field(
        ..., description="One of: prime, subcontract, purchase_order, consulting"
    )
    title: str = Field(default="Untitled Contract", max_length=500)
    parties: list[dict] = Field(default_factory=list)
    effective_date: date | None = None
    expiration_date: date | None = None
    value: Decimal | None = Field(default=None, ge=0)

    @field_validator("contract_type")
    @classmethod
    def validate_contract_type(cls, v: str) -> str:
        allowed = {"prime", "subcontract", "purchase_order", "consulting"}
        if v not in allowed:
            msg = f"contract_type must be one of {allowed}"
            raise ValueError(msg)
        return v


class ContractUploadAndParse(BaseModel):
    """Request body for uploading contract text and triggering extraction."""

    contract_type: str = Field(
        default="prime",
        description="One of: prime, subcontract, purchase_order, consulting",
    )
    title: str = Field(default="Untitled Contract", max_length=500)
    document_text: str = Field(..., min_length=50, max_length=200_000)
    parties: list[dict] = Field(default_factory=list)
    effective_date: date | None = None
    expiration_date: date | None = None
    value: Decimal | None = Field(default=None, ge=0)

    @field_validator("contract_type")
    @classmethod
    def validate_contract_type(cls, v: str) -> str:
        allowed = {"prime", "subcontract", "purchase_order", "consulting"}
        if v not in allowed:
            msg = f"contract_type must be one of {allowed}"
            raise ValueError(msg)
        return v


class ContractCompareRequest(BaseModel):
    """Request to compare two contracts."""

    contract_a_id: uuid.UUID
    contract_b_id: uuid.UUID


class DeviationCheckRequest(BaseModel):
    """Request to check a contract against standard terms."""

    contract_id: uuid.UUID
    custom_standards: dict | None = Field(
        default=None,
        description="Optional custom standard terms to override defaults",
    )


class ApplyToProjectRequest(BaseModel):
    """Request to apply contract settings to a project."""

    contract_document_id: uuid.UUID


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ClauseResponse(BaseModel):
    """A single extracted contract clause."""

    id: uuid.UUID
    contract_document_id: uuid.UUID
    clause_type: str
    clause_text: str
    parsed_value: dict
    section_reference: str | None = None
    confidence: float
    created_at: datetime

    model_config = {"from_attributes": True}


class ContractDocumentResponse(BaseModel):
    """Contract document metadata."""

    id: uuid.UUID
    project_id: uuid.UUID
    document_id: uuid.UUID | None = None
    contract_type: str
    title: str
    parties: list[dict]
    effective_date: date | None = None
    expiration_date: date | None = None
    value: Decimal | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ContractDocumentListResponse(BaseModel):
    """List of contracts for a project."""

    data: list[ContractDocumentResponse]
    count: int


class ClauseListResponse(BaseModel):
    """List of extracted clauses."""

    data: list[ClauseResponse]
    count: int
    contract_document_id: uuid.UUID


class DeviationItem(BaseModel):
    """A single deviation from standard terms."""

    clause_type: str
    description: str
    severity: str
    contract_value: object = None
    standard_value: object = None
    recommendation: str = ""


class DeviationCheckResponse(BaseModel):
    """Result of checking a contract against standard terms."""

    contract_id: uuid.UUID
    deviations: list[DeviationItem]
    deviation_count: int
    critical_count: int
    high_count: int


class ComparisonDiffItem(BaseModel):
    """A single difference between two contracts."""

    clause_type: str
    contract_a: dict | None = None
    contract_b: dict | None = None
    clause_text: str | None = None
    parsed_value: dict | None = None
    section_reference: str | None = None
    changed_fields: list[dict] | None = None


class ContractComparisonResponse(BaseModel):
    """Result of comparing two contracts."""

    id: uuid.UUID
    contract_a_id: uuid.UUID
    contract_b_id: uuid.UUID
    additions: list[ComparisonDiffItem]
    removals: list[ComparisonDiffItem]
    changes: list[ComparisonDiffItem]
    summary: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ApplyToProjectResponse(BaseModel):
    """Result of applying contract terms to a project."""

    applied: bool
    settings_updated: dict
    contract_document_id: str
    project_id: str
    reason: str | None = None


class UploadAndParseResponse(BaseModel):
    """Result of uploading and parsing a contract."""

    contract: ContractDocumentResponse
    clauses: list[ClauseResponse]
    clause_count: int
    deviations: list[DeviationItem]
    deviation_count: int
