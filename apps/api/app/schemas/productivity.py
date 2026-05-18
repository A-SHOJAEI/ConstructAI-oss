"""Pydantic schemas for productivity tracking endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta


class DailyLogCreate(BaseModel):
    project_id: uuid.UUID
    log_date: date
    weather: dict = Field(default_factory=dict)
    crew_count: int = 0
    work_hours: Decimal = Decimal("0")
    activities_completed: list[dict] = Field(default_factory=list)
    delays: list[dict] = Field(default_factory=list)
    notes: str | None = None


class DailyLogResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    log_date: date
    weather: dict
    crew_count: int
    work_hours: Decimal
    activities_completed: list
    delays: list
    notes: str | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DailyLogListResponse(BaseModel):
    data: list[DailyLogResponse]
    meta: PaginationMeta


class CrewProductivityCreate(BaseModel):
    project_id: uuid.UUID
    trade: str
    crew_size: int
    work_date: date
    planned_units: Decimal
    actual_units: Decimal
    unit_of_measure: str
    conditions: dict = Field(default_factory=dict)


class CrewProductivityResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    trade: str
    crew_size: int
    work_date: date
    planned_units: Decimal
    actual_units: Decimal
    unit_of_measure: str
    productivity_rate: Decimal | None = None
    pf_ratio: Decimal | None = None
    conditions: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class CrewProductivityListResponse(BaseModel):
    data: list[CrewProductivityResponse]
    meta: PaginationMeta


class EquipmentTelemetryCreate(BaseModel):
    project_id: uuid.UUID
    equipment_id: str
    equipment_type: str
    timestamp: datetime
    engine_hours: Decimal | None = None
    fuel_consumption: Decimal | None = None
    idle_time_hours: Decimal | None = None
    utilization_pct: Decimal | None = None
    location_data: dict = Field(default_factory=dict)
    raw_payload: dict = Field(default_factory=dict)


class EquipmentTelemetryResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    equipment_id: str
    equipment_type: str
    timestamp: datetime
    engine_hours: Decimal | None = None
    fuel_consumption: Decimal | None = None
    idle_time_hours: Decimal | None = None
    utilization_pct: Decimal | None = None
    location_data: dict
    raw_payload: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class EquipmentTelemetryListResponse(BaseModel):
    data: list[EquipmentTelemetryResponse]
    meta: PaginationMeta


class ActivityRecognitionResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    camera_id: str
    activity_type: str
    confidence: Decimal
    start_time: datetime
    end_time: datetime | None = None
    duration_seconds: int | None = None
    worker_count: int | None = None
    zone_id: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProductivityForecastResponse(BaseModel):
    project_id: uuid.UUID
    trade: str
    forecast_dates: list[date]
    predicted_rates: list[float]
    confidence_intervals: list[dict]
    trend: str


# ---------------------------------------------------------------------------
# Daily Log v2 schemas
# ---------------------------------------------------------------------------


class ManpowerEntry(BaseModel):
    trade: str
    headcount: int = 0
    hours: Decimal = Decimal("0")


class EquipmentEntry(BaseModel):
    equipment_type: str
    equipment_id: str | None = None
    hours_used: Decimal = Decimal("0")
    notes: str | None = None


class DeliveryEntry(BaseModel):
    description: str
    supplier: str | None = None
    tracking_number: str | None = None
    received_by: str | None = None


class VisitorEntry(BaseModel):
    name: str
    company: str | None = None
    purpose: str | None = None
    time_in: str | None = None
    time_out: str | None = None


class PhotoEntry(BaseModel):
    file_path: str | None = None
    file_name: str | None = None
    caption: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    taken_at: str | None = None


class DailyLogCreateV2(BaseModel):
    log_date: date
    weather: dict = Field(default_factory=dict)
    crew_count: int = 0
    work_hours: Decimal = Decimal("0")
    work_narrative: str | None = None
    manpower_by_trade: list[ManpowerEntry] = Field(default_factory=list)
    equipment_entries: list[EquipmentEntry] = Field(default_factory=list)
    deliveries: list[DeliveryEntry] = Field(default_factory=list)
    visitors: list[VisitorEntry] = Field(default_factory=list)
    photos: list[PhotoEntry] = Field(default_factory=list)
    activities_completed: list[dict] = Field(default_factory=list)
    delays: list[dict] = Field(default_factory=list)
    notes: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None
    safety_incidents: str | None = None
    safety_topic_discussed: str | None = None
    weather_delay_hours: Decimal | None = None


class DailyLogUpdateV2(BaseModel):
    weather: dict | None = None
    crew_count: int | None = None
    work_hours: Decimal | None = None
    work_narrative: str | None = None
    manpower_by_trade: list[ManpowerEntry] | None = None
    equipment_entries: list[EquipmentEntry] | None = None
    deliveries: list[DeliveryEntry] | None = None
    visitors: list[VisitorEntry] | None = None
    photos: list[PhotoEntry] | None = None
    activities_completed: list[dict] | None = None
    delays: list[dict] | None = None
    notes: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None
    safety_incidents: str | None = None
    safety_topic_discussed: str | None = None
    weather_delay_hours: Decimal | None = None


class DailyLogDetailResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    log_date: date
    status: str
    weather: dict
    crew_count: int
    work_hours: Decimal
    work_narrative: str | None = None
    manpower_by_trade: list
    equipment_entries: list
    deliveries: list
    visitors: list
    photos: list
    activities_completed: list
    delays: list
    notes: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None
    safety_incidents: str | None = None
    safety_topic_discussed: str | None = None
    weather_delay_hours: Decimal | None = None
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    submitted_at: datetime | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DailyLogDetailListResponse(BaseModel):
    data: list[DailyLogDetailResponse]
    meta: PaginationMeta


class DailyLogWeeklySummary(BaseModel):
    week_start: date
    week_end: date
    total_logs: int
    total_crew_count: int
    total_work_hours: Decimal
    manpower_summary: dict
    weather_summary: list[dict]
    delay_summary: list[dict]
