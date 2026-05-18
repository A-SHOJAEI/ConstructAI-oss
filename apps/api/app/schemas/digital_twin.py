"""Pydantic schemas for digital twin endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Sensor schemas
# ---------------------------------------------------------------------------


class SensorRegisterRequest(BaseModel):
    """Request to register a new IoT sensor on a twin."""

    sensor_id: str = Field(..., min_length=1, max_length=255)
    sensor_type: str = Field(
        ...,
        description="One of: temperature, humidity, strain, vibration, concrete_cure, dust, noise",
    )
    location_xyz: dict[str, float] = Field(
        ..., description="Sensor position in model space: {x, y, z}"
    )
    element_id: str | None = Field(
        default=None, description="IFC element this sensor is attached to"
    )

    @field_validator("sensor_type")
    @classmethod
    def validate_sensor_type(cls, v: str) -> str:
        valid = {"temperature", "humidity", "strain", "vibration", "concrete_cure", "dust", "noise"}
        if v not in valid:
            raise ValueError(
                f"Invalid sensor_type '{v}'. Must be one of: {', '.join(sorted(valid))}"
            )
        return v

    @field_validator("location_xyz")
    @classmethod
    def validate_location(cls, v: dict) -> dict:
        for key in ("x", "y", "z"):
            if key not in v:
                raise ValueError(f"location_xyz must contain '{key}'")
        return v


class SensorReadingRequest(BaseModel):
    """A single sensor reading for ingestion."""

    sensor_id: str = Field(..., min_length=1)
    value: float
    unit: str = Field(..., min_length=1, max_length=20)
    timestamp: datetime | None = None


class SensorBatchRequest(BaseModel):
    """Batch of sensor readings."""

    readings: list[SensorReadingRequest] = Field(..., min_length=1, max_length=500)


class SensorReadingResponse(BaseModel):
    """Response for the latest reading of a sensor."""

    value: float
    unit: str
    timestamp: str


class SensorResponse(BaseModel):
    """Response for a registered sensor."""

    id: uuid.UUID
    sensor_id: str
    sensor_type: str
    location_xyz: dict
    element_id: str | None = None
    latest_reading: dict | None = None
    last_updated: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SensorAnomalyResponse(BaseModel):
    """A detected sensor anomaly."""

    sensor_id: str
    sensor_type: str
    value: float
    unit: str
    level: str  # warning or alert
    threshold: float
    message: str
    element_id: str | None = None


# ---------------------------------------------------------------------------
# Twin model schemas
# ---------------------------------------------------------------------------


class DigitalTwinResponse(BaseModel):
    """Response for a digital twin model."""

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    source_type: str
    s3_key: str
    file_size_bytes: int
    element_count: int | None = None
    coordinate_system: str | None = None
    bounds: dict | None = None
    metadata: dict = Field(default_factory=dict, alias="metadata_")
    status: str
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}


class DigitalTwinListResponse(BaseModel):
    """List of digital twin models."""

    data: list[DigitalTwinResponse]
    count: int


# ---------------------------------------------------------------------------
# Snapshot schemas
# ---------------------------------------------------------------------------


class SnapshotCreateRequest(BaseModel):
    """Request to create a twin snapshot."""

    schedule_overlay: dict[str, float] | None = Field(
        default=None, description="Activity ID to percent complete mapping"
    )
    photo_urls: list[str] | None = None
    notes: str | None = None


class TwinSnapshotResponse(BaseModel):
    """Response for a twin snapshot."""

    id: uuid.UUID
    twin_id: uuid.UUID
    snapshot_date: datetime
    sensor_readings: dict
    schedule_overlay: dict
    photo_overlay_urls: list | None = None
    notes: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TwinSnapshotListResponse(BaseModel):
    """List of twin snapshots."""

    data: list[TwinSnapshotResponse]
    count: int


# ---------------------------------------------------------------------------
# Twin state (combined response)
# ---------------------------------------------------------------------------


class TwinStateResponse(BaseModel):
    """Full current state of a digital twin."""

    twin_id: str
    project_id: str
    name: str
    source_type: str
    status: str
    element_count: int | None = None
    bounds: dict | None = None
    coordinate_system: str | None = None
    metadata: dict = Field(default_factory=dict)
    sensors: list[dict] = Field(default_factory=list)
    anomalies: list[SensorAnomalyResponse] = Field(default_factory=list)
    latest_snapshot: dict | None = None
