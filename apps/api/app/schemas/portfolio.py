"""Executive portfolio dashboard schemas."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field


class ProjectHealthIndicator(BaseModel):
    project_id: uuid.UUID
    project_name: str
    spi: float | None = None
    cpi: float | None = None
    safety_score: float | None = None
    quality_score: float | None = None
    overall_health: str = "unknown"
    active_risks: int = 0
    open_rfis: int = 0
    overdue_submittals: int = 0


class PortfolioSummary(BaseModel):
    projects: list[ProjectHealthIndicator]
    total_projects: int = 0
    projects_on_track: int = 0
    projects_at_risk: int = 0
    projects_critical: int = 0
    portfolio_spi: float | None = None
    portfolio_cpi: float | None = None


class ProjectMetricValue(BaseModel):
    project_id: uuid.UUID
    project_name: str
    value: float
    recorded_date: date | None = None


class CrossProjectBenchmark(BaseModel):
    metric_name: str
    unit: str
    values: list[ProjectMetricValue] = Field(
        default_factory=list,
    )
    industry_average: float | None = None


class PortfolioBenchmarkResponse(BaseModel):
    benchmarks: list[CrossProjectBenchmark]


class PortfolioMapProject(BaseModel):
    project_id: uuid.UUID
    project_name: str
    latitude: float | None = None
    longitude: float | None = None
    health_color: str = "gray"
    spi: float | None = None
    cpi: float | None = None


class PortfolioMapResponse(BaseModel):
    projects: list[PortfolioMapProject]
