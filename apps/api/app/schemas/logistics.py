from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.pagination import PaginationMeta


class SiteLayoutCreate(BaseModel):
    project_id: uuid.UUID
    name: str
    layout_data: dict
    constraints: dict = Field(default_factory=dict)


class SiteLayoutResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    layout_data: dict
    optimization_score: Decimal | None = None
    safety_score: Decimal | None = None
    efficiency_score: Decimal | None = None
    constraints: dict
    pareto_rank: int | None = None
    generation: int | None = None
    status: str
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SiteLayoutListResponse(BaseModel):
    data: list[SiteLayoutResponse]
    meta: PaginationMeta


class DeliveryRouteCreate(BaseModel):
    project_id: uuid.UUID
    route_date: date
    vehicle_id: str | None = None
    stops: dict
    constraints: dict = Field(default_factory=dict)


class DeliveryRouteResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    route_date: date
    vehicle_id: str | None = None
    stops: dict
    total_distance_km: Decimal | None = None
    total_duration_minutes: int | None = None
    total_cost: Decimal | None = None
    optimization_status: str
    constraints: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OptimizeSiteRequest(BaseModel):
    project_id: uuid.UUID
    facilities: list[dict]
    site_boundary: dict
    constraints: dict


class OptimizeSiteResponse(BaseModel):
    layouts: list[SiteLayoutResponse]
    pareto_front: list[dict]
    generations: int


class RouteOptimizeRequest(BaseModel):
    project_id: uuid.UUID
    deliveries: list[dict]
    vehicles: list[dict]
    depot: dict
    date: date


class RouteOptimizeResponse(BaseModel):
    routes: list[DeliveryRouteResponse]
    total_cost: float
    total_distance: float
    unassigned: list


class SimulationRequest(BaseModel):
    project_id: uuid.UUID
    scenario: dict
    duration_days: int


class SimulationResponse(BaseModel):
    timeline: list
    bottlenecks: list
    utilization: dict
    recommendations: list[str]
