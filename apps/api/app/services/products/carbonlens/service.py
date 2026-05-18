"""CarbonLens service — LEED v5 embodied carbon (GWP) tracking.

Provides embodied-carbon inventory management, EPD tracking, GWP
calculations, what-if scenario modelling, and LEED MRp2/MRc2 report
generation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import openai
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.carbon import (
    CarbonMaterialInventory,
    CarbonReport,
    EpdRecord,
    ProjectCarbonConfig,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Baseline GWP per material type (kgCO2e per unit)
MATERIAL_BASELINES: dict[str, dict] = {
    "ready-mix concrete 3000psi": {"gwp": 300.0, "unit": "CY"},
    "ready-mix concrete 4000psi": {"gwp": 350.0, "unit": "CY"},
    "ready-mix concrete 5000psi": {"gwp": 400.0, "unit": "CY"},
    "low-carbon concrete": {"gwp": 220.0, "unit": "CY"},
    "structural steel w-shapes": {"gwp": 1950.0, "unit": "TON"},
    "recycled structural steel": {"gwp": 1200.0, "unit": "TON"},
    "steel rebar": {"gwp": 1100.0, "unit": "TON"},
    "cmu block": {"gwp": 180.0, "unit": "TON"},
    "dimensional lumber": {"gwp": -350.0, "unit": "TON"},  # Carbon sequestering
    "glulam beam": {"gwp": -300.0, "unit": "TON"},
    "clt panel": {"gwp": -280.0, "unit": "TON"},
    "metal wall panels": {"gwp": 45.0, "unit": "SF"},
    "aluminum curtain wall": {"gwp": 55.0, "unit": "SF"},
    "brick veneer": {"gwp": 25.0, "unit": "SF"},
    "rigid insulation xps": {"gwp": 8.5, "unit": "SF"},
    "rigid insulation eps": {"gwp": 3.5, "unit": "SF"},
    "spray foam insulation": {"gwp": 6.0, "unit": "SF"},
    "gypsum board": {"gwp": 2.8, "unit": "SF"},
    "asphalt paving": {"gwp": 50.0, "unit": "TON"},
    "concrete paving": {"gwp": 320.0, "unit": "CY"},
}

# Baseline GWP per building type (kgCO2e per SF)
BUILDING_TYPE_BASELINES: dict[str, float] = {
    "office": 35.0,
    "residential": 28.0,
    "education": 32.0,
    "healthcare": 45.0,
    "retail": 25.0,
    "industrial": 20.0,
    "mixed_use": 30.0,
}

# CSI division to material category mapping
CSI_CATEGORY_MAP: dict[str, str] = {
    "03": "structure",
    "04": "structure",
    "05": "structure",
    "06": "structure",
    "07": "enclosure",
    "08": "enclosure",
    "09": "enclosure",
    "32": "hardscape",
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def auto_categorize_material(
    csi_division: str | None = None,
    material_type: str | None = None,
) -> str:
    """Return 'structure', 'enclosure', or 'hardscape' for a material.

    Uses CSI division first, then falls back to keyword matching on the
    material type string.
    """
    if csi_division:
        div = csi_division.lstrip("0").zfill(2)[:2]
        if div in CSI_CATEGORY_MAP:
            return CSI_CATEGORY_MAP[div]

    if material_type:
        mt_lower = material_type.lower()
        structure_keywords = [
            "concrete",
            "steel",
            "rebar",
            "lumber",
            "glulam",
            "clt",
            "cmu",
            "masonry",
            "wood",
        ]
        enclosure_keywords = [
            "curtain wall",
            "insulation",
            "gypsum",
            "wall panel",
            "brick",
            "roofing",
            "window",
            "glazing",
            "cladding",
        ]
        hardscape_keywords = [
            "paving",
            "asphalt",
            "curb",
            "sidewalk",
            "landscape",
        ]
        if any(kw in mt_lower for kw in structure_keywords):
            return "structure"
        if any(kw in mt_lower for kw in enclosure_keywords):
            return "enclosure"
        if any(kw in mt_lower for kw in hardscape_keywords):
            return "hardscape"

    return "structure"


def _lookup_baseline_gwp(material_type: str) -> dict | None:
    """Look up baseline GWP from the MATERIAL_BASELINES dictionary."""
    key = material_type.lower().strip()
    return MATERIAL_BASELINES.get(key)


def _calc_improvement_pct(
    actual_gwp: float,
    baseline_gwp: float,
) -> float:
    """Percentage improvement vs baseline (positive = improvement)."""
    if baseline_gwp == 0:
        return 0.0
    return round((1.0 - actual_gwp / baseline_gwp) * 100, 2)


# ---------------------------------------------------------------------------
# Project configuration
# ---------------------------------------------------------------------------


async def configure_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    config_data: dict,
) -> ProjectCarbonConfig:
    """Create or update the carbon configuration for a project."""
    result = await db.execute(
        select(ProjectCarbonConfig).where(
            ProjectCarbonConfig.project_id == project_id,
        )
    )
    config = result.scalar_one_or_none()

    if config is None:
        config = ProjectCarbonConfig(
            project_id=project_id,
            organization_id=org_id,
        )
        db.add(config)

    # Apply provided fields
    for field in (
        "leed_version",
        "building_area_sf",
        "target_certification",
        "baseline_gwp_kgco2e",
        "scope_inclusions",
    ):
        value = config_data.get(field)
        if value is not None:
            setattr(config, field, value)

    # Auto-calculate baseline if area is set but baseline is not
    if config.building_area_sf and not config.baseline_gwp_kgco2e:
        baseline_per_sf = BUILDING_TYPE_BASELINES.get("office", 35.0)
        config.baseline_gwp_kgco2e = Decimal(str(float(config.building_area_sf) * baseline_per_sf))

    config.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(config)
    return config


# ---------------------------------------------------------------------------
# Material inventory
# ---------------------------------------------------------------------------


async def add_material(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    data: dict,
) -> CarbonMaterialInventory:
    """Add a material to the carbon inventory.

    Auto-looks up baseline GWP from MATERIAL_BASELINES, calculates total_gwp,
    and computes improvement_pct vs baseline.
    """
    material_type: str = data["material_type"]
    quantity = data.get("quantity") or 0
    csi_division = data.get("csi_division")

    # Auto-categorize
    category = data.get("material_category") or auto_categorize_material(
        csi_division=csi_division,
        material_type=material_type,
    )

    # Lookup baseline GWP
    baseline = _lookup_baseline_gwp(material_type)
    baseline_gwp_per_unit = baseline["gwp"] if baseline else None
    unit = data.get("unit") or (baseline["unit"] if baseline else None)

    # GWP per unit — from data or baseline
    gwp_per_unit = data.get("gwp_per_unit")
    if gwp_per_unit is None and baseline_gwp_per_unit is not None:
        gwp_per_unit = baseline_gwp_per_unit

    # Calculate totals
    total_gwp: float | None = None
    if gwp_per_unit is not None and quantity:
        total_gwp = float(gwp_per_unit) * float(quantity)

    improvement_pct: float | None = None
    if gwp_per_unit is not None and baseline_gwp_per_unit is not None:
        improvement_pct = _calc_improvement_pct(float(gwp_per_unit), float(baseline_gwp_per_unit))

    material = CarbonMaterialInventory(
        project_id=project_id,
        organization_id=org_id,
        material_category=category,
        material_type=material_type,
        csi_division=csi_division,
        quantity=Decimal(str(quantity)) if quantity else None,
        unit=unit,
        supplier=data.get("supplier"),
        manufacturer=data.get("manufacturer"),
        product_name=data.get("product_name"),
        epd_id=data.get("epd_id"),
        gwp_per_unit=Decimal(str(gwp_per_unit)) if gwp_per_unit is not None else None,
        total_gwp=Decimal(str(total_gwp)) if total_gwp is not None else None,
        baseline_gwp_per_unit=(
            Decimal(str(baseline_gwp_per_unit)) if baseline_gwp_per_unit is not None else None
        ),
        improvement_pct=(Decimal(str(improvement_pct)) if improvement_pct is not None else None),
    )
    db.add(material)
    await db.flush()
    await db.refresh(material)
    return material


async def update_material(
    db: AsyncSession,
    material_id: uuid.UUID,
    project_id: uuid.UUID,
    updates: dict,
) -> CarbonMaterialInventory:
    """Update fields on a material, recalculating totals."""
    result = await db.execute(
        select(CarbonMaterialInventory).where(
            CarbonMaterialInventory.id == material_id,
            CarbonMaterialInventory.project_id == project_id,
        )
    )
    material = result.scalar_one_or_none()
    if material is None:
        raise ValueError(f"Material {material_id} not found in project {project_id}")

    for field in ("quantity", "supplier", "procurement_status", "epd_id"):
        value = updates.get(field)
        if value is not None:
            setattr(material, field, value)

    # Recalculate totals if quantity changed
    if material.gwp_per_unit is not None and material.quantity is not None:
        material.total_gwp = Decimal(str(float(material.gwp_per_unit) * float(material.quantity)))
    if material.gwp_per_unit is not None and material.baseline_gwp_per_unit is not None:
        material.improvement_pct = Decimal(
            str(
                _calc_improvement_pct(
                    float(material.gwp_per_unit),
                    float(material.baseline_gwp_per_unit),
                )
            )
        )

    material.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(material)
    return material


async def list_materials(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> list[CarbonMaterialInventory]:
    """Return all carbon material inventory items for a project."""
    result = await db.execute(
        select(CarbonMaterialInventory)
        .where(CarbonMaterialInventory.project_id == project_id)
        .order_by(CarbonMaterialInventory.created_at)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# EPD management
# ---------------------------------------------------------------------------


async def upload_epd(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    supplier: str | None,
    product_name: str | None,
    pdf_s3_key: str,
) -> EpdRecord:
    """Create an EPD record (PDF already uploaded to S3)."""
    epd = EpdRecord(
        project_id=project_id,
        organization_id=org_id,
        supplier=supplier,
        product_name=product_name,
        pdf_s3_key=pdf_s3_key,
    )
    db.add(epd)
    await db.flush()
    await db.refresh(epd)
    return epd


async def parse_epd(
    db: AsyncSession,
    epd_id: uuid.UUID,
    text_content: str,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
) -> EpdRecord:
    """Extract GWP data from EPD text using LLM, with empty fallback.

    Attempts to use the OpenAI API to extract structured data from the
    EPD text.  On any failure, stores an empty extraction so the EPD
    record is still usable.
    """
    result = await db.execute(
        select(EpdRecord).where(
            EpdRecord.id == epd_id,
            EpdRecord.project_id == project_id,
        )
    )
    epd = result.scalar_one_or_none()
    if epd is None:
        raise ValueError(f"EPD {epd_id} not found in project {project_id}")

    extracted: dict = {}
    try:
        client = openai.AsyncOpenAI()
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract EPD data from the following text. Return JSON "
                        "with keys: gwp_a1_a3 (float, kgCO2e), declared_unit "
                        "(string), manufacturer (string), product_name (string), "
                        "epd_number (string), epd_program_operator (string), "
                        "valid_from (YYYY-MM-DD), valid_to (YYYY-MM-DD). "
                        "Only include keys you can confidently extract."
                    ),
                },
                {"role": "user", "content": text_content[:8000]},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        import json

        extracted = json.loads(response.choices[0].message.content or "{}")
        logger.info("EPD %s: LLM extraction succeeded with keys: %s", epd_id, list(extracted))
    except Exception:
        logger.warning("EPD %s: LLM extraction failed, using empty extraction", epd_id)

    epd.ai_extracted_data = extracted

    # Apply extracted values to EPD fields
    if "gwp_a1_a3" in extracted:
        import contextlib

        with contextlib.suppress(Exception):
            epd.gwp_a1_a3 = Decimal(str(extracted["gwp_a1_a3"]))
    for field in (
        "declared_unit",
        "manufacturer",
        "product_name",
        "epd_number",
        "epd_program_operator",
    ):
        if field in extracted and not getattr(epd, field, None):
            setattr(epd, field, str(extracted[field]))

    epd.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(epd)
    return epd


async def verify_epd(
    db: AsyncSession,
    epd_id: uuid.UUID,
    user_id: uuid.UUID,
) -> EpdRecord:
    """Mark an EPD as verified by a user."""
    result = await db.execute(select(EpdRecord).where(EpdRecord.id == epd_id))
    epd = result.scalar_one_or_none()
    if epd is None:
        raise ValueError(f"EPD {epd_id} not found")

    epd.verification_status = "verified"
    epd.verified_by = user_id
    epd.verified_at = datetime.now(UTC)
    epd.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(epd)
    return epd


async def list_epds(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> list[EpdRecord]:
    """Return all EPD records for a project."""
    result = await db.execute(
        select(EpdRecord).where(EpdRecord.project_id == project_id).order_by(EpdRecord.created_at)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# GWP calculations
# ---------------------------------------------------------------------------


async def calculate_gwp(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Calculate aggregate GWP for a project.

    Returns:
        dict with keys: total_gwp_kgco2e, gwp_per_sf, baseline_comparison_pct,
        hotspot_materials, category_breakdown.
    """
    materials = await list_materials(db, project_id)

    # Sum total GWP
    total_gwp = sum(float(m.total_gwp) for m in materials if m.total_gwp is not None)

    # GWP per SF
    config_result = await db.execute(
        select(ProjectCarbonConfig).where(
            ProjectCarbonConfig.project_id == project_id,
        )
    )
    config = config_result.scalar_one_or_none()
    gwp_per_sf: float | None = None
    baseline_comparison_pct = 0.0
    if config and config.building_area_sf:
        area = float(config.building_area_sf)
        if area > 0:
            gwp_per_sf = round(total_gwp / area, 4)
    if config and config.baseline_gwp_kgco2e:
        baseline = float(config.baseline_gwp_kgco2e)
        if baseline != 0:
            baseline_comparison_pct = round(((total_gwp - baseline) / abs(baseline)) * 100, 2)

    # Hotspot materials — top 3 by absolute total_gwp
    sorted_mats = sorted(
        [m for m in materials if m.total_gwp is not None],
        key=lambda m: abs(float(m.total_gwp or 0)),
        reverse=True,
    )
    hotspot_materials = [
        {
            "id": str(m.id),
            "material_type": m.material_type,
            "total_gwp": float(m.total_gwp or 0),
            "percentage": round(float(m.total_gwp or 0) / total_gwp * 100, 1)
            if total_gwp != 0
            else 0,
        }
        for m in sorted_mats[:3]
    ]

    # Mark hotspots on the materials
    hotspot_ids = {h["id"] for h in hotspot_materials}
    for m in materials:
        is_hotspot = str(m.id) in hotspot_ids
        if m.is_carbon_hotspot != is_hotspot:
            await db.execute(
                update(CarbonMaterialInventory)
                .where(CarbonMaterialInventory.id == m.id)
                .values(is_carbon_hotspot=is_hotspot)
            )

    # Category breakdown
    category_totals: dict[str, float] = {}
    for m in materials:
        if m.total_gwp is not None:
            cat = m.material_category
            category_totals[cat] = category_totals.get(cat, 0) + float(m.total_gwp)

    category_breakdown = [
        {
            "category": cat,
            "total_gwp": gwp,
            "percentage": round(gwp / total_gwp * 100, 1) if total_gwp != 0 else 0,
        }
        for cat, gwp in sorted(category_totals.items(), key=lambda x: abs(x[1]), reverse=True)
    ]

    return {
        "total_gwp_kgco2e": round(total_gwp, 4),
        "gwp_per_sf": gwp_per_sf,
        "baseline_comparison_pct": baseline_comparison_pct,
        "hotspot_materials": hotspot_materials,
        "category_breakdown": category_breakdown,
    }


async def model_scenario(
    db: AsyncSession,
    project_id: uuid.UUID,
    material_id: uuid.UUID,
    new_gwp_per_unit: float,
) -> dict:
    """What-if: recalculate project GWP if one material's GWP changes.

    Returns:
        dict with original_gwp, scenario_gwp, delta_pct.
    """
    # Current totals
    gwp_data = await calculate_gwp(db, project_id)
    original_gwp = gwp_data["total_gwp_kgco2e"]

    # Find the target material
    result = await db.execute(
        select(CarbonMaterialInventory).where(
            CarbonMaterialInventory.id == material_id,
            CarbonMaterialInventory.project_id == project_id,
        )
    )
    material = result.scalar_one_or_none()
    if material is None:
        raise ValueError(f"Material {material_id} not found in project {project_id}")

    # Calculate scenario GWP
    old_total = float(material.total_gwp) if material.total_gwp is not None else 0
    qty = float(material.quantity) if material.quantity is not None else 0
    new_total = new_gwp_per_unit * qty

    scenario_gwp = original_gwp - old_total + new_total
    delta_pct = 0.0
    if original_gwp != 0:
        delta_pct = round(((scenario_gwp - original_gwp) / abs(original_gwp)) * 100, 2)

    return {
        "original_gwp": round(original_gwp, 4),
        "scenario_gwp": round(scenario_gwp, 4),
        "delta_pct": delta_pct,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


async def generate_mrp2_report(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> CarbonReport:
    """Generate a LEED MRp2 prerequisite report.

    Checks:
    1. BOM completeness (materials exist)
    2. EPD coverage >= 90%
    3. Hotspot analysis
    4. Mitigation narrative (LLM with template fallback)
    """
    gwp_data = await calculate_gwp(db, project_id)
    materials = await list_materials(db, project_id)

    # EPD coverage
    materials_with_epd = sum(1 for m in materials if m.epd_id is not None)
    epd_coverage_pct = round(materials_with_epd / len(materials) * 100, 2) if materials else 0

    # LEED credits assessment
    leed_credits: dict = {}
    # MRp2 prerequisite: requires BOM + EPD disclosure
    leed_credits["MRp2_bom_complete"] = len(materials) > 0
    leed_credits["MRp2_epd_coverage"] = epd_coverage_pct >= 90
    leed_credits["MRp2_prerequisite_met"] = (
        leed_credits["MRp2_bom_complete"] and leed_credits["MRp2_epd_coverage"]
    )
    # MRc2 credit: % reduction vs baseline
    baseline_pct = gwp_data["baseline_comparison_pct"]
    if baseline_pct <= -10:
        leed_credits["MRc2_points"] = 2
    elif baseline_pct <= -5:
        leed_credits["MRc2_points"] = 1
    else:
        leed_credits["MRc2_points"] = 0

    # Mitigation narrative
    mitigation = _generate_mitigation_narrative(gwp_data, materials, epd_coverage_pct)

    report = CarbonReport(
        project_id=project_id,
        organization_id=org_id,
        report_type="mrp2_prerequisite",
        total_gwp_kgco2e=Decimal(str(gwp_data["total_gwp_kgco2e"])),
        gwp_per_sf=(
            Decimal(str(gwp_data["gwp_per_sf"])) if gwp_data["gwp_per_sf"] is not None else None
        ),
        baseline_comparison_pct=Decimal(str(gwp_data["baseline_comparison_pct"])),
        hotspot_materials=gwp_data["hotspot_materials"],
        category_breakdown=gwp_data["category_breakdown"],
        epd_coverage_pct=Decimal(str(epd_coverage_pct)),
        mitigation_narrative=mitigation,
        leed_credits_achieved=leed_credits,
    )
    db.add(report)
    await db.flush()
    await db.refresh(report)
    return report


def _generate_mitigation_narrative(
    gwp_data: dict,
    _materials: list[CarbonMaterialInventory],
    epd_coverage_pct: float,
) -> str:
    """Generate a mitigation narrative (template fallback, no LLM needed)."""
    hotspots = gwp_data.get("hotspot_materials", [])
    total_gwp = gwp_data["total_gwp_kgco2e"]
    baseline_pct = gwp_data["baseline_comparison_pct"]

    parts: list[str] = []
    parts.append(
        f"Total project embodied carbon: {total_gwp:,.0f} kgCO2e "
        f"({baseline_pct:+.1f}% vs baseline)."
    )

    if hotspots:
        hotspot_names = ", ".join(h["material_type"] for h in hotspots)
        parts.append(f"Carbon hotspots identified: {hotspot_names}.")

    parts.append(f"EPD coverage: {epd_coverage_pct:.0f}% of materials have EPDs.")

    # Recommendations
    if baseline_pct > 0:
        parts.append(
            "Recommended actions: consider low-carbon concrete mixes, "
            "recycled steel, or mass timber substitutions for hotspot "
            "materials to reduce embodied carbon below the baseline."
        )
    else:
        parts.append(
            "Project demonstrates embodied carbon reduction vs baseline. "
            "Continue procurement of specified low-carbon materials."
        )

    if epd_coverage_pct < 90:
        parts.append(
            f"EPD coverage gap: {100 - epd_coverage_pct:.0f}% of materials "
            "lack product-specific EPDs. Request EPDs from suppliers for "
            "remaining materials to meet MRp2 prerequisite."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Dashboard aggregation
# ---------------------------------------------------------------------------


async def get_dashboard(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict:
    """Aggregate carbon dashboard data for the project.

    Returns:
        dict with keys: total_gwp_tco2e, baseline_pct, epd_coverage_pct,
        hotspots, category_breakdown, material_count.
    """
    gwp_data = await calculate_gwp(db, project_id)
    materials = await list_materials(db, project_id)

    total_gwp_kgco2e = gwp_data["total_gwp_kgco2e"]
    total_gwp_tco2e = round(total_gwp_kgco2e / 1000.0, 4)

    materials_with_epd = sum(1 for m in materials if m.epd_id is not None)
    epd_coverage_pct = round(materials_with_epd / len(materials) * 100, 2) if materials else 0

    return {
        "total_gwp_tco2e": total_gwp_tco2e,
        "baseline_pct": gwp_data["baseline_comparison_pct"],
        "epd_coverage_pct": epd_coverage_pct,
        "hotspots": gwp_data["hotspot_materials"],
        "category_breakdown": gwp_data["category_breakdown"],
        "material_count": len(materials),
    }
