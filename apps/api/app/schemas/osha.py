"""Pydantic schemas for OSHA enforcement data endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class OshaLookupResult(BaseModel):
    """A single fuzzy-match result from OSHA inspections."""

    activity_nr: str
    establishment_name: str
    site_city: str | None = None
    site_state: str | None = None
    match_score: float
    open_date: str | None = None
    close_date: str | None = None
    total_penalty: float = 0.0
    insp_type: str | None = None


class OshaLookupResponse(BaseModel):
    """Response for contractor OSHA lookup."""

    query: str
    state_filter: str | None = None
    results: list[OshaLookupResult]
    result_count: int


class OshaStandardStat(BaseModel):
    """Violation statistics for a single OSHA standard."""

    standard: str
    title: str | None = None
    category: str | None = None
    count: int
    willful_count: int = 0
    repeat_count: int = 0
    total_penalty: float = 0.0


class OshaStatsResponse(BaseModel):
    """Aggregated OSHA violation statistics."""

    state: str | None = None
    naics_prefix: str | None = None
    since_date: str
    total_inspections: int
    total_violations: int
    top_standards: list[OshaStandardStat]
