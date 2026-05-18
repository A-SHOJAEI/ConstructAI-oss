from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ZoneCreate(BaseModel):
    camera_id: uuid.UUID
    project_id: uuid.UUID
    name: str
    zone_type: Literal[
        "restricted",
        "ppe_required",
        "equipment_only",
        "pedestrian_only",
        "crane_swing",
        "excavation",
        "general",
    ]
    polygon_points: list[list[float]]
    ppe_requirements: list[str] = []
    severity_override: (
        Literal["P1_critical", "P2_high", "P3_medium", "P4_low", "P5_info"] | None
    ) = None
    schedule_active: dict = {}


class ZoneResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    project_id: uuid.UUID
    name: str
    zone_type: str
    polygon_points: list
    ppe_requirements: list
    severity_override: str | None = None
    is_active: bool
    schedule_active: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ZoneUpdate(BaseModel):
    name: str | None = None
    zone_type: (
        Literal[
            "restricted",
            "ppe_required",
            "equipment_only",
            "pedestrian_only",
            "crane_swing",
            "excavation",
            "general",
        ]
        | None
    ) = None
    polygon_points: list[list[float]] | None = None
    ppe_requirements: list[str] | None = None
    severity_override: (
        Literal["P1_critical", "P2_high", "P3_medium", "P4_low", "P5_info"] | None
    ) = None
    is_active: bool | None = None
    schedule_active: dict | None = None


class ZoneListResponse(BaseModel):
    data: list[ZoneResponse]
