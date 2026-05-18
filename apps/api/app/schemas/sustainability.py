"""Pydantic schemas for sustainability and LEED v5 tracking endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Carbon Factor schemas
# ---------------------------------------------------------------------------


class CarbonFactorResponse(BaseModel):
    """Response schema for a carbon factor entry."""

    csi_code: str
    material_name: str
    embodied_carbon_kgco2e: float
    unit: str
    data_source: str
    gwp_category: str
    notes: str = ""


class CarbonFactorListResponse(BaseModel):
    """Paginated list of carbon factors."""

    data: list[CarbonFactorResponse]
    total: int


# ---------------------------------------------------------------------------
# Embodied Carbon calculation schemas
# ---------------------------------------------------------------------------


class CarbonCalcLineItem(BaseModel):
    """A single line item for embodied carbon calculation."""

    csi_code: str
    description: str = ""
    quantity: float
    unit: str


class CarbonCalcRequest(BaseModel):
    """Request to calculate embodied carbon for a set of line items."""

    line_items: list[CarbonCalcLineItem]
    gross_area_sf: float | None = None


class CarbonItemResult(BaseModel):
    """Carbon result for a single line item."""

    csi_code: str
    description: str
    quantity: float
    unit: str
    carbon_factor_kgco2e: float
    factor_unit: str
    total_kgco2e: float
    data_source: str


class EmbodiedCarbonResponse(BaseModel):
    """Response from embodied carbon calculation."""

    total_kgco2e: float
    total_tonco2e: float
    carbon_per_sf: float | None = None
    by_division: dict[str, float]
    by_item: list[CarbonItemResult]
    item_count: int
    unmatched_items: list[str]
    gross_area_sf: float | None = None


# ---------------------------------------------------------------------------
# LEED Credit schemas
# ---------------------------------------------------------------------------


class LEEDCreditResponse(BaseModel):
    """Response for a single LEED credit evaluation."""

    credit_id: str
    credit_name: str
    category: str
    max_points: int
    status: str  # achievable / partial / not_achievable
    earned_points: int
    reasoning: str
    requirements: list[str]
    evidence: list[str] = Field(default_factory=list)


class LEEDEvalRequest(BaseModel):
    """Request to evaluate LEED credits."""

    project_type: str = "commercial"
    gross_area_sf: float | None = None
    total_material_cost: float = 0.0
    epd_product_count: int = 0
    material_ingredient_count: int = 0
    optimized_ingredient_count: int = 0
    waste_diversion_pct: float = 0.0
    energy_reduction_pct: float = 0.0
    renewable_energy_pct: float = 0.0
    site_assessment_complete: bool = False
    rainwater_percentile_managed: int = 0
    high_albedo_site_pct: float = 0.0
    cool_roof_pct: float = 0.0
    outdoor_water_reduction_pct: float = 0.0
    indoor_water_reduction_pct: float = 0.0
    low_emitting_categories: int = 0
    daylight_area_pct: float = 0.0
    recycled_content_pct: float = 0.0


class LEEDEvalResponse(BaseModel):
    """Response from LEED credit evaluation."""

    credits: list[LEEDCreditResponse]
    total_earned_points: int
    max_possible_points: int
    certification_level: str  # certified / silver / gold / platinum / none


# ---------------------------------------------------------------------------
# Salvaged & Recycled Materials schemas
# ---------------------------------------------------------------------------


class SalvagedMaterialEntry(BaseModel):
    """A salvaged/reused material entry."""

    description: str
    cost: float
    csi_code: str | None = None
    source: str | None = None
    weight_tons: float | None = None


class SalvagedMaterialsUpdate(BaseModel):
    """Update salvaged materials for a project."""

    salvaged_materials: list[SalvagedMaterialEntry]


class RecycledContentUpdate(BaseModel):
    """Update recycled content percentage for a project."""

    recycled_content_pct: float = Field(ge=0.0, le=100.0)


# ---------------------------------------------------------------------------
# Dashboard schemas
# ---------------------------------------------------------------------------


class SustainabilityDashboardResponse(BaseModel):
    """Full sustainability dashboard response."""

    project_id: str
    total_embodied_carbon_kgco2e: float
    carbon_per_sf: float | None = None
    baseline_comparison_pct: float | None = None
    embodied_carbon: EmbodiedCarbonResponse
    leed_credits: list[LEEDCreditResponse]
    salvaged_materials: list[dict]
    recycled_content_pct: float
    total_leed_points: int
    max_possible_points: int
    calculated_at: str


class ProjectSustainabilityResponse(BaseModel):
    """Stored sustainability record response."""

    id: uuid.UUID
    project_id: uuid.UUID
    total_embodied_carbon_kgco2e: Decimal
    carbon_per_sf: Decimal | None = None
    salvaged_materials: list
    recycled_content_pct: Decimal
    leed_credits: list
    energy_data: dict | None = None
    baseline_comparison_pct: Decimal | None = None
    last_calculated: datetime
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
