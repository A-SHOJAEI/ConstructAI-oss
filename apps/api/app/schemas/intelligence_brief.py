"""Pydantic schemas for intelligence brief endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta


class ActionItem(BaseModel):
    """A single action item from the intelligence brief."""

    action: str
    responsible: str
    due_by: str
    reason: str


class IntelligenceBriefResponse(BaseModel):
    """Full intelligence brief response."""

    id: uuid.UUID
    project_id: uuid.UUID
    report_date: date
    overall_health_score: int = Field(ge=0, le=100)
    project_status: str  # GREEN / YELLOW / RED
    schedule_health_score: int = Field(ge=0, le=100)
    cost_health_score: int = Field(ge=0, le=100)
    risk_score: int = Field(ge=0, le=100)
    productivity_score: int = Field(ge=0, le=100)
    executive_summary: str
    schedule_intelligence: dict
    cost_intelligence: dict
    risk_intelligence: dict
    productivity_intelligence: dict
    action_items: list[dict]
    metrics_dashboard: dict
    narrative_report: str
    guardrails_result: dict
    pdf_url: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class IntelligenceBriefSummary(BaseModel):
    """Lightweight summary for list views."""

    id: uuid.UUID
    project_id: uuid.UUID
    report_date: date
    overall_health_score: int
    project_status: str
    executive_summary: str
    created_at: datetime

    model_config = {"from_attributes": True}


class IntelligenceBriefListResponse(BaseModel):
    """Paginated list of intelligence briefs."""

    data: list[IntelligenceBriefSummary]
    meta: PaginationMeta
