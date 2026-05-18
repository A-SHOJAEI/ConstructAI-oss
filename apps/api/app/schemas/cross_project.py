"""Pydantic schemas for cross-project analytics endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CostPatternFilters(BaseModel):
    """Filters for cost pattern detection."""

    project_type: str | None = None
    csi_division: str | None = None
    min_projects: int = Field(default=2, ge=1, le=100)


class NLQueryRequest(BaseModel):
    """Natural language cross-project question."""

    question: str = Field(..., min_length=5, max_length=1000)


class RFIPatternFilters(BaseModel):
    """Filters for RFI pattern detection."""

    building_type: str | None = None


class CostTrendFilters(BaseModel):
    """Filters for cost trend analysis."""

    csi_division: str | None = None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class CostPatternItem(BaseModel):
    """A detected cost pattern across projects."""

    csi_division: str
    description: str
    average_variance_pct: float
    project_count: int
    project_type: str | None = None
    confidence: float


class CostPatternResponse(BaseModel):
    """Response for cost pattern detection."""

    patterns: list[CostPatternItem]
    count: int
    org_id: uuid.UUID


class ScheduleAccuracyByGroup(BaseModel):
    """Schedule accuracy grouped by type or size."""

    count: int
    average_variance_pct: float
    on_time_rate: float


class ScheduleAccuracyResponse(BaseModel):
    """Response for schedule accuracy analysis."""

    total_projects: int
    average_duration_variance_pct: float
    on_time_rate: float
    by_project_type: dict[str, ScheduleAccuracyByGroup] = Field(default_factory=dict)
    by_project_size: dict[str, ScheduleAccuracyByGroup] = Field(default_factory=dict)
    common_delay_causes: list[dict] = Field(default_factory=list)
    org_id: uuid.UUID


class RFIPatternItem(BaseModel):
    """A common RFI pattern across projects."""

    subject_cluster: str
    occurrence_count: int
    average_resolution_days: float
    most_common_keywords: list[str] = Field(default_factory=list)
    building_type: str | None = None


class RFIPatternResponse(BaseModel):
    """Response for RFI pattern detection."""

    patterns: list[RFIPatternItem]
    count: int
    org_id: uuid.UUID


class CostTrendItem(BaseModel):
    """Cost trend for a CSI division."""

    csi_division: str
    description: str
    trend_direction: str
    average_annual_change_pct: float
    data_points: list[dict] = Field(default_factory=list)
    project_count: int


class CostTrendResponse(BaseModel):
    """Response for cost trend analysis."""

    trends: list[CostTrendItem]
    count: int
    org_id: uuid.UUID


class RiskCorrelationItem(BaseModel):
    """Correlation between risk type and project outcomes."""

    risk_category: str
    occurrence_count: int
    avg_cost_impact_pct: float
    avg_schedule_impact_days: float
    projects_affected: int
    correlation_strength: str


class RiskCorrelationResponse(BaseModel):
    """Response for risk factor correlation."""

    correlations: list[RiskCorrelationItem]
    count: int
    org_id: uuid.UUID


class CrossProjectQueryResponse(BaseModel):
    """Response for natural language cross-project queries."""

    question: str
    answer: str
    confidence: float
    source_project_count: int
    supporting_data: dict = Field(default_factory=dict)
    cached: bool = False


class CachedInsightItem(BaseModel):
    """A cached cross-project insight."""

    id: str
    insight_type: str
    parameters: dict
    result: dict
    source_project_count: int
    confidence: float
    expires_at: str | None = None
    created_at: str | None = None
    is_expired: bool = False


class CachedInsightsResponse(BaseModel):
    """Response listing cached insights."""

    data: list[CachedInsightItem]
    count: int
    org_id: uuid.UUID
