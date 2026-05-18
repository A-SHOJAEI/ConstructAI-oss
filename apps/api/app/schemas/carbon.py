"""Pydantic schemas for CarbonLens endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CarbonConfigUpdate(BaseModel):
    """Update project carbon configuration."""

    leed_version: str | None = None
    building_area_sf: float | None = None
    target_certification: str | None = None
    baseline_gwp_kgco2e: float | None = None
    scope_inclusions: list[str] | None = None


class MaterialCreate(BaseModel):
    """Create a material in the carbon inventory."""

    material_category: str
    material_type: str
    csi_division: str | None = None
    quantity: float
    unit: str
    supplier: str | None = None
    manufacturer: str | None = None
    product_name: str | None = None
    epd_id: uuid.UUID | None = None


class MaterialUpdate(BaseModel):
    """Partial update for a carbon material."""

    quantity: float | None = None
    supplier: str | None = None
    procurement_status: str | None = None
    epd_id: uuid.UUID | None = None


class EpdUploadRequest(BaseModel):
    """Upload an EPD record (PDF already in S3)."""

    supplier: str | None = None
    product_name: str | None = None
    pdf_s3_key: str


class EpdVerifyRequest(BaseModel):
    """Verify or reject an EPD."""

    verified: bool


class ScenarioInput(BaseModel):
    """What-if scenario: substitute a material's GWP."""

    material_id: uuid.UUID
    new_gwp_per_unit: float


class ReportGenerateRequest(BaseModel):
    """Generate a carbon report."""

    report_type: str = "mrp2_prerequisite"


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class CarbonConfigResponse(BaseModel):
    """Carbon configuration response."""

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    leed_version: str
    building_area_sf: float | None = None
    target_certification: str | None = None
    baseline_gwp_kgco2e: float | None = None
    scope_inclusions: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MaterialResponse(BaseModel):
    """Carbon material inventory item response."""

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    material_category: str
    material_type: str
    csi_division: str | None = None
    quantity: float | None = None
    unit: str | None = None
    supplier: str | None = None
    manufacturer: str | None = None
    product_name: str | None = None
    epd_id: uuid.UUID | None = None
    gwp_per_unit: float | None = None
    total_gwp: float | None = None
    baseline_gwp_per_unit: float | None = None
    improvement_pct: float | None = None
    is_carbon_hotspot: bool = False
    procurement_status: str = "specified"
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EpdResponse(BaseModel):
    """EPD record response."""

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    supplier: str | None = None
    manufacturer: str | None = None
    product_name: str | None = None
    epd_program_operator: str | None = None
    epd_number: str | None = None
    epd_type: str = "product_specific"
    gwp_a1_a3: float | None = None
    declared_unit: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    pdf_s3_key: str | None = None
    verification_status: str = "pending"
    ai_extracted_data: dict = Field(default_factory=dict)
    verified_by: uuid.UUID | None = None
    verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CarbonReportResponse(BaseModel):
    """Generated carbon report response."""

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    report_type: str
    total_gwp_kgco2e: float | None = None
    gwp_per_sf: float | None = None
    baseline_comparison_pct: float | None = None
    hotspot_materials: list[dict] = Field(default_factory=list)
    category_breakdown: list[dict] = Field(default_factory=list)
    epd_coverage_pct: float | None = None
    mitigation_narrative: str | None = None
    leed_credits_achieved: dict = Field(default_factory=dict)
    pdf_s3_key: str | None = None
    generated_at: datetime

    model_config = {"from_attributes": True}


class GwpCalculationResponse(BaseModel):
    """Real-time GWP calculation result."""

    total_gwp_kgco2e: float
    gwp_per_sf: float | None = None
    baseline_comparison_pct: float
    hotspot_materials: list[dict] = Field(default_factory=list)
    category_breakdown: list[dict] = Field(default_factory=list)


class ScenarioResponse(BaseModel):
    """What-if scenario result."""

    original_gwp: float
    scenario_gwp: float
    delta_pct: float


class CarbonDashboardResponse(BaseModel):
    """Aggregated carbon dashboard data."""

    total_gwp_tco2e: float
    baseline_pct: float
    epd_coverage_pct: float
    hotspots: list[dict] = Field(default_factory=list)
    category_breakdown: list[dict] = Field(default_factory=list)
    material_count: int
