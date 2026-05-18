"""Pydantic schemas for ambient field intelligence endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Field Ping
# ---------------------------------------------------------------------------


class FieldPingInput(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=255)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    accuracy_m: float | None = Field(default=None, ge=0)
    altitude_m: float | None = None
    trade: str | None = Field(default=None, max_length=100)
    timestamp: datetime


class FieldPingBatchRequest(BaseModel):
    pings: list[FieldPingInput] = Field(..., max_length=500)


# ---------------------------------------------------------------------------
# Equipment Telemetry
# ---------------------------------------------------------------------------


class TelemetryInput(BaseModel):
    equipment_id: str = Field(..., min_length=1, max_length=255)
    equipment_type: str | None = Field(default=None, max_length=100)
    status: str = Field(default="idle", pattern=r"^(idle|running|off)$")
    fuel_level_pct: float | None = Field(default=None, ge=0, le=100)
    engine_hours: float | None = Field(default=None, ge=0)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    raw_payload: dict = Field(default_factory=dict)
    timestamp: datetime


class TelemetryBatchRequest(BaseModel):
    telemetry: list[TelemetryInput] = Field(..., max_length=500)


# ---------------------------------------------------------------------------
# Badge Events
# ---------------------------------------------------------------------------


class BadgeEventInput(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=255)
    worker_name: str | None = Field(default=None, max_length=255)
    trade: str | None = Field(default=None, max_length=100)
    event_type: str = Field(..., pattern=r"^(check_in|check_out|break_start|break_end)$")
    gate_id: str | None = Field(default=None, max_length=100)
    timestamp: datetime


class BadgeEventBatchRequest(BaseModel):
    events: list[BadgeEventInput] = Field(..., max_length=500)


# ---------------------------------------------------------------------------
# Shared response schemas
# ---------------------------------------------------------------------------


class IngestResponse(BaseModel):
    count_inserted: int
    count_submitted: int
    count_skipped: int
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Snapshot response
# ---------------------------------------------------------------------------


class AmbientSnapshotResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    snapshot_date: date
    workforce_summary: dict
    equipment_summary: dict
    site_activity: dict
    zone_activity: list
    data_quality: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Aggregation request
# ---------------------------------------------------------------------------


class AggregateRequest(BaseModel):
    snapshot_date: date


class GenerateReportRequest(BaseModel):
    snapshot_date: date
