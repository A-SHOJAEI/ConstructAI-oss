from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.schemas.pagination import PaginationMeta


def _validate_json_dict(v: dict, *, max_depth: int = 4, field_name: str = "field") -> dict:
    """Validate that a value is a flat-ish dict (not a list, string, or excessively nested).

    Rejects non-dict types and dicts nested deeper than *max_depth* levels.
    """
    if not isinstance(v, dict):
        raise ValueError(f"{field_name} must be a JSON object (dict), got {type(v).__name__}")

    def _check_depth(obj: object, depth: int) -> None:
        if depth > max_depth:
            raise ValueError(f"{field_name} is nested too deeply (max {max_depth} levels)")
        if isinstance(obj, dict):
            for val in obj.values():
                _check_depth(val, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _check_depth(item, depth + 1)

    _check_depth(v, 1)
    return v


class LocationInput(BaseModel):
    """Project location for regional cost factor lookup."""

    city: str | None = None
    state: str | None = Field(
        default=None, description="Two-letter state abbreviation (e.g. CA, NY)"
    )
    zip_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class RegionalFactorResponse(BaseModel):
    """Regional cost factor transparency in API responses."""

    metro: str
    state_abbr: str
    material_factor: float
    labor_factor: float
    equipment_factor: float
    composite_factor: float
    is_fallback: bool
    distance_km: float | None = None
    warning: str | None = None


class CostItemCreate(BaseModel):
    category: str
    description: str
    unit: str
    base_unit_cost: Decimal
    region: str | None = None
    bls_series_id: str | None = None
    data_source: str = "manual"
    effective_date: date


class CostItemResponse(BaseModel):
    id: uuid.UUID
    category: str
    description: str
    unit: str
    base_unit_cost: Decimal
    region: str | None = None
    bls_series_id: str | None = None
    data_source: str
    effective_date: date
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EstimateLineItemResponse(BaseModel):
    id: uuid.UUID
    estimate_id: uuid.UUID
    cost_item_id: uuid.UUID | None = None
    csi_code: str | None = None
    description: str
    quantity: Decimal
    unit: str
    unit_cost: Decimal
    total_cost: Decimal
    source: str
    confidence: Decimal | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CostEstimateCreate(BaseModel):
    project_id: uuid.UUID
    name: str
    estimate_type: str = Field(pattern=r"^(conceptual|schematic|detailed|final)$")
    status: str = "draft"
    contingency_pct: Decimal = Field(default=Decimal("10.0"), ge=0, le=50)
    assumptions: dict = Field(default_factory=dict)
    location: LocationInput | None = None

    @field_validator("assumptions")
    @classmethod
    def validate_assumptions(cls, v: dict) -> dict:
        return _validate_json_dict(v, max_depth=4, field_name="assumptions")


class CostEstimateResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    estimate_type: str
    status: str
    total_cost: Decimal | None = None
    contingency_pct: Decimal
    confidence_low: Decimal | None = None
    confidence_high: Decimal | None = None
    monte_carlo_p50: Decimal | None = None
    monte_carlo_p80: Decimal | None = None
    assumptions: dict
    created_by: uuid.UUID | None = None
    line_items: list[EstimateLineItemResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EstimateListResponse(BaseModel):
    data: list[CostEstimateResponse]
    meta: PaginationMeta


class MonteCarloRequest(BaseModel):
    project_id: uuid.UUID
    estimate_id: uuid.UUID
    num_simulations: int = Field(default=10000, ge=100, le=100000)
    location: LocationInput | None = None


class MonteCarloResponse(BaseModel):
    p10: float
    p50: float
    p80: float
    p90: float
    mean: float
    std_dev: float
    histogram_data: list[float]
    regional_factor: RegionalFactorResponse | None = None


class RunEstimateRequest(BaseModel):
    project_id: uuid.UUID
    estimate_type: str = Field(pattern=r"^(conceptual|schematic|detailed|final)$")
    include_monte_carlo: bool = False


class RunEstimateResponse(BaseModel):
    estimate: CostEstimateResponse
    monte_carlo: MonteCarloResponse | None = None
