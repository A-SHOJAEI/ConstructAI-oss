from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta


class CalendarInfo(BaseModel):
    id: str
    name: str
    work_days: list[int]  # 0=Mon..6=Sun
    holidays: list[str] = Field(default_factory=list)
    hours_per_day: float = 8.0


class ScheduleActivityCreate(BaseModel):
    project_id: uuid.UUID
    baseline_id: uuid.UUID | None = None
    activity_code: str
    name: str
    duration_days: int
    start_date: date | None = None
    finish_date: date | None = None
    predecessors: list = Field(default_factory=list)
    resource_assignments: list = Field(default_factory=list)
    wbs_code: str | None = None
    calendar_id: str | None = None
    original_id: str | None = None
    wbs_path: str | None = None


class ScheduleActivityUpdate(BaseModel):
    """Partial update for a schedule activity."""

    name: str | None = None
    duration_days: int | None = None
    start_date: date | None = None
    finish_date: date | None = None
    predecessors: list | None = None
    resource_assignments: list | None = None
    wbs_code: str | None = None
    status: str | None = None
    pct_complete: Decimal | None = None


class ScheduleActivityResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    baseline_id: uuid.UUID | None = None
    activity_code: str
    name: str
    duration_days: int
    start_date: date | None = None
    finish_date: date | None = None
    early_start: date | None = None
    early_finish: date | None = None
    late_start: date | None = None
    late_finish: date | None = None
    total_float: int | None = None
    free_float: int | None = None
    is_critical: bool
    predecessors: list
    resource_assignments: list
    wbs_code: str | None = None
    calendar_id: str | None = None
    original_id: str | None = None
    wbs_path: str | None = None
    status: str
    actual_start: date | None = None
    actual_finish: date | None = None
    pct_complete: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScheduleActivityListResponse(BaseModel):
    data: list[ScheduleActivityResponse]
    total: int
    skip: int
    limit: int


class ScheduleBaselineResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version: int
    baseline_date: date
    total_duration_days: int | None = None
    critical_path_length: int | None = None
    dcma_score: Decimal | None = None
    dcma_results: dict | None = None
    source_file: str | None = None
    source_format: str | None = None
    calendars: list[CalendarInfo] = Field(default_factory=list)
    data_date: date | None = None
    activities: list[ScheduleActivityResponse] = []
    created_by: uuid.UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class BaselineListResponse(BaseModel):
    data: list[ScheduleBaselineResponse]
    meta: PaginationMeta


class DCMACheckRequest(BaseModel):
    project_id: uuid.UUID
    baseline_id: uuid.UUID


class DCMACheckItem(BaseModel):
    check_name: str
    status: Literal["pass", "fail", "warning"]
    score: float
    description: str
    threshold: float


class DCMACheckResponse(BaseModel):
    overall_score: float
    checks: list[DCMACheckItem]
    passed: int
    failed: int
    warning: int


class WeatherImpactRequest(BaseModel):
    project_id: uuid.UUID
    location: str
    start_date: date
    end_date: date


class WeatherImpactResponse(BaseModel):
    impact_days: int
    weather_events: list
    adjusted_end_date: date
    risk_level: str


class ScheduleImportResponse(BaseModel):
    baseline: ScheduleBaselineResponse
    activities_imported: int
    relationships_imported: int
    calendars_imported: int
    warnings: list[str] = Field(default_factory=list)
