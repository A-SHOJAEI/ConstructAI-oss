"""Pydantic schemas for the HeatShield product."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class HeatConfigUpdate(BaseModel):
    """Partial update for heat monitoring configuration."""

    zip_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    threshold_initial_f: float | None = None
    threshold_high_heat_f: float | None = None
    notification_contacts: list[dict] | None = None
    crew_start_time: str | None = None
    monitoring_enabled: bool | None = None


class ManualReadingCreate(BaseModel):
    """Manual weather/heat reading from field personnel."""

    temperature_f: float
    humidity_pct: float | None = None
    wind_speed_mph: float | None = None


class WorkerCreate(BaseModel):
    """Register a worker for acclimatization tracking."""

    worker_id: str
    worker_name: str
    supervisor_id: uuid.UUID | None = None


class WorkerUpdate(BaseModel):
    """Update worker acclimatization fields."""

    status: str | None = None
    last_work_date: date | None = None


class BreakLogCreate(BaseModel):
    """Log a rest/water break."""

    break_date: date
    scheduled_time: str | None = None
    actual_start: str
    actual_end: str
    duration_minutes: int
    location_compliant: bool = True
    workers_present: int = 0
    gps_lat: float | None = None
    gps_lng: float | None = None
    exception_reason: str | None = None


class IncidentCreate(BaseModel):
    """Report a heat-related incident."""

    worker_id: str | None = None
    worker_name: str | None = None
    incident_date: date
    incident_time: str | None = None
    symptoms: list[str] = Field(default_factory=list)
    heat_index_at_incident: float | None = None
    acclimatization_day: int | None = None
    actions_taken: str | None = None
    medical_response: str = "none"
    osha_recordable: bool = False
    photos: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class HeatConfigResponse(BaseModel):
    """Heat monitoring configuration response."""

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    zip_code: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    threshold_initial_f: Decimal
    threshold_high_heat_f: Decimal
    notification_contacts: list[dict]
    crew_start_time: str
    monitoring_enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class HeatReadingResponse(BaseModel):
    """Heat condition reading response."""

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    timestamp: datetime
    temperature_f: Decimal | None = None
    heat_index_f: Decimal | None = None
    wbgt_f: Decimal | None = None
    humidity_pct: Decimal | None = None
    wind_speed_mph: Decimal | None = None
    data_source: str
    threshold_level: str
    protocol_activated: bool
    notified_users: list[dict]

    model_config = {"from_attributes": True}


class WorkerAcclimatizationResponse(BaseModel):
    """Worker acclimatization tracking response."""

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    worker_id: str
    worker_name: str
    start_date: date
    acclimatization_day: int
    max_exposure_hours: Decimal
    status: str
    last_work_date: date | None = None
    supervisor_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BreakLogResponse(BaseModel):
    """Rest break log response."""

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    break_date: date
    scheduled_time: str | None = None
    actual_start: str | None = None
    actual_end: str | None = None
    duration_minutes: int | None = None
    location_compliant: bool
    logged_by: uuid.UUID | None = None
    workers_present: int
    gps_lat: Decimal | None = None
    gps_lng: Decimal | None = None
    exception_flag: bool
    exception_reason: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class IncidentResponse(BaseModel):
    """Heat incident report response."""

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    worker_id: str | None = None
    worker_name: str | None = None
    incident_date: date
    incident_time: str | None = None
    symptoms: list[str]
    heat_index_at_incident: Decimal | None = None
    acclimatization_day: int | None = None
    actions_taken: str | None = None
    medical_response: str
    outcome: str | None = None
    root_cause: str | None = None
    osha_recordable: bool
    photos: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class HeatPlanResponse(BaseModel):
    """Generated HIIPP response."""

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    version: int
    plan_content: dict
    pdf_s3_key: str | None = None
    generated_at: datetime

    model_config = {"from_attributes": True}


class BreakScheduleItem(BaseModel):
    """A single scheduled break in the daily break schedule."""

    scheduled_time: str
    threshold_level: str
    duration_minutes: int
    status: str = "scheduled"  # 'scheduled', 'logged', 'missed'


class HeatDashboardResponse(BaseModel):
    """Aggregated heat compliance dashboard."""

    current_conditions: dict | None = None
    threshold_level: str
    workers: dict  # total, acclimatizing, acclimatized, reset
    today_breaks: list[BreakScheduleItem]
    recent_incidents: list[IncidentResponse]
    break_compliance_rate: float
