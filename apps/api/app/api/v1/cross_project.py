"""Cross-project analytics API endpoints.

All routes are org-scoped: ``/orgs/{org_id}/insights/...``

SECURITY (C-10): All queries are scoped by org_id. The authenticated user's
org_id must match the path parameter to prevent cross-tenant data access.
"""

from __future__ import annotations

import logging
import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_permission
from app.models.user import User
from app.schemas.cross_project import (
    CachedInsightItem,
    CachedInsightsResponse,
    CostPatternFilters,
    CostPatternItem,
    CostPatternResponse,
    CostTrendItem,
    CostTrendResponse,
    CrossProjectQueryResponse,
    NLQueryRequest,
    RFIPatternItem,
    RFIPatternResponse,
    RiskCorrelationItem,
    RiskCorrelationResponse,
    ScheduleAccuracyByGroup,
    ScheduleAccuracyResponse,
)
from app.services.memory.cross_project_analytics import (
    analyze_cost_trends,
    analyze_schedule_accuracy,
    correlate_risk_factors,
    detect_cost_patterns,
    find_rfi_patterns,
    get_cached_insights,
    query_cross_project,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Tenant isolation check
# ---------------------------------------------------------------------------


def _verify_org_access(org_id: uuid.UUID, current_user: User) -> None:
    """Ensure the authenticated user belongs to the requested org.

    SECURITY (C-10): Prevents cross-tenant data access.
    """
    if str(current_user.org_id) != str(org_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )


# ---------------------------------------------------------------------------
# Cost patterns
# ---------------------------------------------------------------------------


@router.post(
    "/{org_id}/insights/costs",
    response_model=CostPatternResponse,
)
async def detect_cost_patterns_endpoint(
    org_id: uuid.UUID,
    body: CostPatternFilters | None = None,
    current_user: User = Depends(require_permission("insights", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Detect cost patterns by comparing estimates vs actuals across projects."""
    _verify_org_access(org_id, current_user)

    filters = body.model_dump() if body else {}
    patterns = await detect_cost_patterns(db, org_id, filters)

    return CostPatternResponse(
        patterns=[
            CostPatternItem(
                csi_division=p.csi_division,
                description=p.description,
                average_variance_pct=p.average_variance_pct,
                project_count=p.project_count,
                project_type=p.project_type,
                confidence=p.confidence,
            )
            for p in patterns
        ],
        count=len(patterns),
        org_id=org_id,
    )


# ---------------------------------------------------------------------------
# Schedule accuracy
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/insights/schedule-accuracy",
    response_model=ScheduleAccuracyResponse,
)
async def analyze_schedule_accuracy_endpoint(
    org_id: uuid.UUID,
    current_user: User = Depends(require_permission("insights", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Analyze schedule accuracy across all org projects."""
    _verify_org_access(org_id, current_user)

    report = await analyze_schedule_accuracy(db, org_id)

    return ScheduleAccuracyResponse(
        total_projects=report.total_projects,
        average_duration_variance_pct=report.average_duration_variance_pct,
        on_time_rate=report.on_time_rate,
        by_project_type={
            k: ScheduleAccuracyByGroup(**v) for k, v in report.by_project_type.items()
        },
        by_project_size={
            k: ScheduleAccuracyByGroup(**v) for k, v in report.by_project_size.items()
        },
        common_delay_causes=report.common_delay_causes,
        org_id=org_id,
    )


# ---------------------------------------------------------------------------
# RFI patterns
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/insights/rfi-patterns",
    response_model=RFIPatternResponse,
)
async def find_rfi_patterns_endpoint(
    org_id: uuid.UUID,
    building_type: str | None = Query(None),
    current_user: User = Depends(require_permission("insights", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Cluster RFIs by subject keywords across projects."""
    _verify_org_access(org_id, current_user)

    patterns = await find_rfi_patterns(db, org_id, building_type)

    return RFIPatternResponse(
        patterns=[
            RFIPatternItem(
                subject_cluster=p.subject_cluster,
                occurrence_count=p.occurrence_count,
                average_resolution_days=p.average_resolution_days,
                most_common_keywords=p.most_common_keywords,
                building_type=p.building_type,
            )
            for p in patterns
        ],
        count=len(patterns),
        org_id=org_id,
    )


# ---------------------------------------------------------------------------
# Cost trends
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/insights/cost-trends",
    response_model=CostTrendResponse,
)
async def analyze_cost_trends_endpoint(
    org_id: uuid.UUID,
    csi_division: str | None = Query(None),
    current_user: User = Depends(require_permission("insights", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Track actual cost trends by CSI division across projects over time."""
    _verify_org_access(org_id, current_user)

    trends = await analyze_cost_trends(db, org_id, csi_division)

    return CostTrendResponse(
        trends=[
            CostTrendItem(
                csi_division=t.csi_division,
                description=t.description,
                trend_direction=t.trend_direction,
                average_annual_change_pct=t.average_annual_change_pct,
                data_points=t.data_points,
                project_count=t.project_count,
            )
            for t in trends
        ],
        count=len(trends),
        org_id=org_id,
    )


# ---------------------------------------------------------------------------
# Risk correlations
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/insights/risk-correlations",
    response_model=RiskCorrelationResponse,
)
async def correlate_risk_factors_endpoint(
    org_id: uuid.UUID,
    current_user: User = Depends(require_permission("insights", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Correlate risk register entries against schedule/cost variances."""
    _verify_org_access(org_id, current_user)

    correlations = await correlate_risk_factors(db, org_id)

    return RiskCorrelationResponse(
        correlations=[
            RiskCorrelationItem(
                risk_category=c.risk_category,
                occurrence_count=c.occurrence_count,
                avg_cost_impact_pct=c.avg_cost_impact_pct,
                avg_schedule_impact_days=c.avg_schedule_impact_days,
                projects_affected=c.projects_affected,
                correlation_strength=c.correlation_strength,
            )
            for c in correlations
        ],
        count=len(correlations),
        org_id=org_id,
    )


# ---------------------------------------------------------------------------
# Natural language query
# ---------------------------------------------------------------------------


@router.post(
    "/{org_id}/insights/query",
    response_model=CrossProjectQueryResponse,
)
async def query_cross_project_endpoint(
    org_id: uuid.UUID,
    body: NLQueryRequest,
    current_user: User = Depends(require_permission("insights", "read")),
    db: AsyncSession = Depends(get_db),
):
    """Answer a natural language question about org performance using AI."""
    _verify_org_access(org_id, current_user)

    answer = await query_cross_project(db, org_id, body.question)

    return CrossProjectQueryResponse(
        question=answer.question,
        answer=answer.answer,
        confidence=answer.confidence,
        source_project_count=answer.source_project_count,
        supporting_data=answer.supporting_data,
        cached=answer.cached,
    )


# ---------------------------------------------------------------------------
# Cached insights
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/insights/cached",
    response_model=CachedInsightsResponse,
)
async def list_cached_insights(
    org_id: uuid.UUID,
    insight_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_permission("insights", "read")),
    db: AsyncSession = Depends(get_db),
):
    """List cached cross-project insights for the organization."""
    _verify_org_access(org_id, current_user)

    cached = await get_cached_insights(db, org_id, insight_type, limit)

    return CachedInsightsResponse(
        data=cast(list[CachedInsightItem], cached),
        count=len(cached),
        org_id=org_id,
    )
