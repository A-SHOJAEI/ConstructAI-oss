"""Pydantic schemas for drone/UAV data integration endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Flight log schemas
# ---------------------------------------------------------------------------


class FlightLogCreateRequest(BaseModel):
    """Request to create a drone flight log."""

    drone_id: str | None = None
    flight_date: datetime
    duration_minutes: int | None = Field(default=None, ge=0)
    area_covered_sf: Decimal | None = Field(default=None, ge=0)
    altitude_ft: Decimal | None = Field(default=None, ge=0)
    flight_path: list[dict] | None = Field(
        default=None, description="List of {lat, lon, alt} waypoints"
    )
    weather_conditions: dict | None = None
    notes: str | None = None


class FlightLogResponse(BaseModel):
    """Response for a drone flight log."""

    id: uuid.UUID
    project_id: uuid.UUID
    drone_id: str | None = None
    flight_date: datetime
    duration_minutes: int | None = None
    area_covered_sf: Decimal | None = None
    altitude_ft: Decimal | None = None
    flight_path: list | None = None
    weather_conditions: dict | None = None
    operator_id: uuid.UUID | None = None
    notes: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class FlightLogListResponse(BaseModel):
    """Paginated list of flight logs."""

    data: list[FlightLogResponse]
    total: int
    skip: int
    limit: int


# ---------------------------------------------------------------------------
# Capture schemas
# ---------------------------------------------------------------------------


class CaptureResponse(BaseModel):
    """Response for a drone capture."""

    id: uuid.UUID
    flight_id: uuid.UUID
    capture_type: str
    s3_key: str
    file_size_bytes: int
    resolution: str | None = None
    bounds: dict | None = None
    point_count: int | None = None
    metadata: dict = Field(default_factory=dict, alias="metadata_")
    processing_status: str
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}


class CaptureListResponse(BaseModel):
    """List of captures for a flight."""

    data: list[CaptureResponse]
    count: int


# ---------------------------------------------------------------------------
# Earthwork volume schemas
# ---------------------------------------------------------------------------


class EarthworkCalculateRequest(BaseModel):
    """Request to calculate earthwork volume from point data."""

    zone_name: str = Field(..., min_length=1, max_length=255)
    points: list[list[float]] = Field(
        ...,
        min_length=3,
        description="Nx3 array of [x, y, z] coordinates in feet",
    )
    grid_spacing_ft: float = Field(default=5.0, gt=0, le=100)
    reference_elevation_ft: float = Field(default=0.0)
    method: str = Field(default="grid")
    capture_id: uuid.UUID | None = None
    notes: str | None = None

    @field_validator("method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        if v not in ("grid", "cross_section"):
            raise ValueError("method must be 'grid' or 'cross_section'")
        return v

    @field_validator("points")
    @classmethod
    def validate_points(cls, v: list) -> list:
        for i, pt in enumerate(v):
            if len(pt) != 3:
                raise ValueError(
                    f"Point at index {i} must have exactly 3 values [x, y, z], got {len(pt)}"
                )
        return v


class EarthworkCompareRequest(BaseModel):
    """Request to compare earthwork volumes between two captures."""

    capture_id_before: uuid.UUID
    capture_id_after: uuid.UUID
    zone_name: str = Field(..., min_length=1, max_length=255)


class EarthworkVolumeResponse(BaseModel):
    """Response for an earthwork volume calculation."""

    id: uuid.UUID
    project_id: uuid.UUID
    capture_id: uuid.UUID | None = None
    calculation_date: date
    zone_name: str
    cut_volume_cy: Decimal
    fill_volume_cy: Decimal
    net_volume_cy: Decimal
    surface_area_sf: Decimal | None = None
    reference_elevation_ft: Decimal | None = None
    method: str
    confidence: Decimal
    notes: str | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class EarthworkVolumeListResponse(BaseModel):
    """List of earthwork volume records."""

    data: list[EarthworkVolumeResponse]
    count: int


class VolumeComparisonResponse(BaseModel):
    """Response for earthwork volume comparison between two captures."""

    zone_name: str
    before_date: str
    after_date: str
    before_cut_cy: Decimal
    before_fill_cy: Decimal
    after_cut_cy: Decimal
    after_fill_cy: Decimal
    delta_cut_cy: Decimal
    delta_fill_cy: Decimal
    delta_net_cy: Decimal
    progress_pct: float


# ---------------------------------------------------------------------------
# Flight summary schemas
# ---------------------------------------------------------------------------


class FlightSummaryResponse(BaseModel):
    """Aggregate summary of drone flights."""

    project_id: str
    total_flights: int
    total_area_covered_sf: Decimal
    total_flight_minutes: int
    captures_by_type: dict[str, int] = Field(default_factory=dict)
    date_range: dict[str, str | None] = Field(default_factory=dict)
