"""Pydantic schemas for Bid/No-Bid Decision Intelligence."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
BidStatus = Literal["evaluating", "pursuing", "declined", "won", "lost"]
DeliveryMethod = Literal["hard_bid", "negotiated", "design_build", "cmar", "ipd"]
Recommendation = Literal["strong_pursue", "pursue", "conditional", "decline"]

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class BidOpportunityCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    owner_name: str | None = None
    project_type: str | None = None
    delivery_method: DeliveryMethod | None = None
    estimated_value: float | None = Field(None, ge=0)
    location: str | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    bid_due_date: date | None = None
    description: str | None = None
    metadata_json: dict | None = None


class BidOpportunityUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=500)
    owner_name: str | None = None
    project_type: str | None = None
    delivery_method: DeliveryMethod | None = None
    estimated_value: float | None = Field(None, ge=0)
    location: str | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    bid_due_date: date | None = None
    description: str | None = None
    status: BidStatus | None = None
    metadata_json: dict | None = None


class RecordDecisionRequest(BaseModel):
    decision: Literal["pursue", "decline"]
    notes: str | None = None


class RecordOutcomeRequest(BaseModel):
    outcome: Literal["won", "lost"]
    actual_margin: float | None = None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class BidOpportunityResponse(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    owner_name: str | None = None
    project_type: str | None = None
    delivery_method: str | None = None
    estimated_value: float | None = None
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    bid_due_date: date | None = None
    description: str | None = None
    status: str
    outcome: str | None = None
    actual_margin: float | None = None
    metadata_json: dict = {}
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FactorScoreDetail(BaseModel):
    score: float
    weight: float
    weighted_score: float
    reasoning: str


class BidDecisionResponse(BaseModel):
    id: UUID
    opportunity_id: UUID
    decided_by: UUID | None = None
    ai_score: int
    ai_recommendation: str | None = None
    ai_reasoning: str | None = None
    human_decision: str | None = None
    human_notes: str | None = None
    factor_scores: dict = {}
    win_probability: float | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class BidOpportunityWithDecision(BidOpportunityResponse):
    latest_decision: BidDecisionResponse | None = None


class BidOpportunityListResponse(BaseModel):
    data: list[BidOpportunityResponse]
    meta: PaginationMeta


class BidAnalyticsResponse(BaseModel):
    total_opportunities: int
    total_won: int
    total_lost: int
    overall_win_rate: float
    avg_ai_score: float | None = None
    by_project_type: dict[str, dict] = {}
    by_delivery_method: dict[str, dict] = {}


class CSVRowError(BaseModel):
    row: int
    field: str | None = None
    message: str


class BidCSVUploadResponse(BaseModel):
    imported: int
    errors: list[CSVRowError]
    warnings: list[str]
    total_rows: int
