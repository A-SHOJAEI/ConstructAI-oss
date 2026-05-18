from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class SafetyAlertResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    camera_id: uuid.UUID | None
    zone_id: uuid.UUID | None
    priority: str
    alert_type: str
    description: str
    detections: list
    frame_s3_key: str | None
    video_clip_s3_key: str | None
    confidence: float
    is_acknowledged: bool
    is_false_positive: bool | None
    acknowledged_by: uuid.UUID | None
    response_notes: str | None
    osha_reference: str | None
    created_at: datetime
    acknowledged_at: datetime | None

    model_config = {"from_attributes": True}


class AlertAcknowledgeRequest(BaseModel):
    is_false_positive: bool = False
    notes: str | None = Field(default=None, max_length=2000)


class AlertListResponse(BaseModel):
    data: list[SafetyAlertResponse]
    total: int


class SafetyStatsResponse(BaseModel):
    total_alerts: int
    alerts_by_priority: dict[str, int]
    alerts_by_type: dict[str, int]
    acknowledged_count: int
    false_positive_count: int
    period: str


class DetectionEvent(BaseModel):
    camera_id: uuid.UUID
    class_name: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: list[int] = Field(min_length=4, max_length=4)
    track_id: int | None = None
    violation_type: str | None = None
    zone_id: uuid.UUID | None = None
    timestamp: str

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, v: list[int]) -> list[int]:
        if any(not isinstance(x, int) or x < 0 for x in v):
            raise ValueError("bbox values must be non-negative integers")
        return v
