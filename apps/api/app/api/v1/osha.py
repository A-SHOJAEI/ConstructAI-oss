"""OSHA enforcement data API endpoints.

Requires authentication to prevent unauthenticated data scraping.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.osha import (
    OshaLookupResponse,
    OshaLookupResult,
    OshaStandardStat,
    OshaStatsResponse,
)
from app.services.safety.osha_lookup import get_violation_stats, lookup_contractor

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/lookup", response_model=OshaLookupResponse)
async def osha_lookup(
    company_name: str = Query(..., min_length=2, description="Company name to search"),
    state: str | None = Query(None, max_length=2, description="Two-letter state code"),
    threshold: float = Query(0.6, ge=0.0, le=1.0, description="Minimum match score"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OshaLookupResponse:
    """Fuzzy-match a contractor name against OSHA inspection records."""
    results = await lookup_contractor(
        db,
        company_name,
        state=state.upper() if state else None,
        threshold=threshold,
        limit=limit,
    )

    return OshaLookupResponse(
        query=company_name,
        state_filter=state.upper() if state else None,
        results=[OshaLookupResult(**r) for r in results],
        result_count=len(results),
    )


@router.get("/stats", response_model=OshaStatsResponse)
async def osha_stats(
    state: str | None = Query(None, max_length=2, description="Two-letter state code"),
    naics: str | None = Query(None, description="NAICS prefix (e.g. 236)"),
    since_years: int = Query(5, ge=1, le=20, description="Look-back period in years"),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OshaStatsResponse:
    """Aggregate OSHA violation statistics by standard."""
    stats = await get_violation_stats(
        db,
        state=state.upper() if state else None,
        naics_prefix=naics,
        since_years=since_years,
    )

    return OshaStatsResponse(
        state=stats["state"],
        naics_prefix=stats["naics_prefix"],
        since_date=stats["since_date"],
        total_inspections=stats["total_inspections"],
        total_violations=stats["total_violations"],
        top_standards=[OshaStandardStat(**s) for s in stats["top_standards"]],
    )
