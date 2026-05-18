"""Cost matching, BLS Producer Price Index integration, and cost enrichment.

Fetches real PPI / wage / employment data from the BLS v2 API using the
configured ``BLS_API_KEY``.  Synthetic fallback data has been **removed** --
if the BLS API is unreachable and no cache is available, functions raise
``BLSDataUnavailableError`` so callers can handle it explicitly.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class BLSDataUnavailableError(Exception):
    """Raised when BLS data cannot be retrieved and no cache exists."""


# ---------------------------------------------------------------------------
# BLS v2 API configuration
# ---------------------------------------------------------------------------

_BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# Maximum series per BLS v2 request (registered key)
_BLS_MAX_SERIES_PER_REQUEST = 50


def _get_bls_api_key() -> str | None:
    """Read the BLS API key from settings or environment."""
    try:
        from app.config import settings

        key = getattr(settings, "BLS_API_KEY", None)
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("BLS_API_KEY")


# ---------------------------------------------------------------------------
# BLS Series Catalog — PPI, Wages, Employment with CSI mapping
# ---------------------------------------------------------------------------

# Structure: series_id -> {description, csi_division, category, series_type}
BLS_SERIES_MAP: dict[str, dict] = {
    # ── Existing material PPIs (kept for backward compat) ──────────────
    "WPUIP2300001": {
        "description": "PPI - Concrete products",
        "csi_division": "03",
        "category": "concrete",
        "series_type": "ppi",
    },
    "WPU101": {
        "description": "PPI - Iron and steel",
        "csi_division": "05",
        "category": "structural_steel",
        "series_type": "ppi",
    },
    "WPU081": {
        "description": "PPI - Lumber",
        "csi_division": "06",
        "category": "lumber",
        "series_type": "ppi",
    },
    "WPU102502": {
        "description": "PPI - Copper and brass",
        "csi_division": "26",
        "category": "copper",
        "series_type": "ppi",
    },
    "WPU058": {
        "description": "PPI - Asphalt paving",
        "csi_division": "32",
        "category": "asphalt",
        "series_type": "ppi",
    },
    # ── New material PPIs ──────────────────────────────────────────────
    "PCU327312327312": {
        "description": "PPI - Ready-mix concrete",
        "csi_division": "03",
        "category": "ready_mix_concrete",
        "series_type": "ppi",
    },
    "PCU332111332111": {
        "description": "PPI - Iron and steel pipes and tubes",
        "csi_division": "22",
        "category": "steel_pipe",
        "series_type": "ppi",
    },
    "PCU332312332312": {
        "description": "PPI - Sheet metal work manufacturing",
        "csi_division": "23",
        "category": "sheet_metal",
        "series_type": "ppi",
    },
    "PCU332411332411": {
        "description": "PPI - HVAC and commercial refrigeration equipment",
        "csi_division": "23",
        "category": "hvac_equipment",
        "series_type": "ppi",
    },
    "PCU335921335921": {
        "description": "PPI - Electrical switchgear and switchboard apparatus",
        "csi_division": "26",
        "category": "electrical_switchgear",
        "series_type": "ppi",
    },
    "WPU0553": {
        "description": "PPI - Asphalt roofing and siding",
        "csi_division": "07",
        "category": "asphalt_roofing",
        "series_type": "ppi",
    },
    "WPU0913": {
        "description": "PPI - Paint and allied products",
        "csi_division": "09",
        "category": "paint",
        "series_type": "ppi",
    },
    "PCU238210238210": {
        "description": "PPI - Electrical contractors",
        "csi_division": "26",
        "category": "electrical_contractor",
        "series_type": "ppi",
    },
    "PCU238220238220": {
        "description": "PPI - Plumbing, heating, and AC contractors",
        "csi_division": "22",
        "category": "plumbing_contractor",
        "series_type": "ppi",
    },
    # ── Construction wages (CES hourly earnings) ──────────────────────
    "CEU2023610008": {
        "description": "Hourly earnings - Heavy/civil engineering construction",
        "csi_division": "31",
        "category": "wage_heavy_civil",
        "series_type": "wage",
    },
    "CEU2023620008": {
        "description": "Hourly earnings - Residential building construction",
        "csi_division": "06",
        "category": "wage_residential",
        "series_type": "wage",
    },
    "CEU2023800008": {
        "description": "Hourly earnings - Specialty trade contractors",
        "csi_division": None,
        "category": "wage_specialty_trade",
        "series_type": "wage",
    },
    "CEU2023811008": {
        "description": "Hourly earnings - Plumbing, HVAC, and AC contractors",
        "csi_division": "22",
        "category": "wage_plumbing_hvac",
        "series_type": "wage",
    },
    "CEU2023812008": {
        "description": "Hourly earnings - Electrical contractors",
        "csi_division": "26",
        "category": "wage_electrical",
        "series_type": "wage",
    },
    "CEU2023813008": {
        "description": "Hourly earnings - Finish carpentry contractors",
        "csi_division": "06",
        "category": "wage_finish_carpentry",
        "series_type": "wage",
    },
    # ── Construction employment (CES) ─────────────────────────────────
    "CES2000000001": {
        "description": "Total construction employment (thousands)",
        "csi_division": None,
        "category": "employment_total",
        "series_type": "employment",
    },
    "CES2023600001": {
        "description": "Heavy and civil engineering employment (thousands)",
        "csi_division": "31",
        "category": "employment_heavy_civil",
        "series_type": "employment",
    },
}

# Legacy alias: old code referenced _BLS_SERIES_MAP keyed by material name.
# Build from the new catalog for backward compatibility.
_BLS_SERIES_MAP: dict[str, str] = {
    v["category"]: k for k, v in BLS_SERIES_MAP.items() if v["series_type"] == "ppi"
}
_BLS_SERIES_MAP["default"] = "WPUIP2300001"

# Reverse lookup: category -> series_id (all types)
_CATEGORY_TO_BLS_SERIES: dict[str, str] = {v["category"]: k for k, v in BLS_SERIES_MAP.items()}

# Reverse lookup: CSI division -> list of series_ids
_CSI_TO_BLS_SERIES: dict[str, list[str]] = {}
for _sid, _meta in BLS_SERIES_MAP.items():
    _div = _meta.get("csi_division")
    if _div:
        _CSI_TO_BLS_SERIES.setdefault(_div, []).append(_sid)


# ---------------------------------------------------------------------------
# Module-level PPI cache
# ---------------------------------------------------------------------------

# series_id -> (result_dict, timestamp)
_ppi_cache: dict[str, tuple[dict, float]] = {}
_PPI_CACHE_TTL = int(os.environ.get("PPI_CACHE_TTL", "86400"))  # 24h default

# series_id -> (time_series_list, timestamp)  — for trend / enrichment
_ppi_history_cache: dict[str, tuple[list[dict], float]] = {}
_PPI_HISTORY_CACHE_TTL = int(os.environ.get("PPI_HISTORY_CACHE_TTL", "86400"))


# ---------------------------------------------------------------------------
# Material-specific uncertainty ranges (for Monte Carlo)
# ---------------------------------------------------------------------------

MATERIAL_UNCERTAINTY_RANGES: dict[str, tuple[float, float]] = {
    # (low_pct, high_pct) — expressed as fractions, e.g. 0.20 = ±20%
    # Material PPIs
    "concrete": (0.08, 0.12),
    "ready_mix_concrete": (0.08, 0.12),
    "structural_steel": (0.15, 0.25),
    "lumber": (0.20, 0.35),
    "copper": (0.15, 0.30),
    "asphalt": (0.10, 0.20),
    "steel_pipe": (0.12, 0.22),
    "sheet_metal": (0.10, 0.18),
    "hvac_equipment": (0.08, 0.15),
    "electrical_switchgear": (0.08, 0.15),
    "asphalt_roofing": (0.10, 0.18),
    "paint": (0.05, 0.12),
    "electrical_contractor": (0.08, 0.15),
    "plumbing_contractor": (0.08, 0.15),
    # Earthwork / site
    "demolition": (0.10, 0.25),
    "excavation": (0.10, 0.20),
    "piling": (0.12, 0.25),
    # Finishes
    "drywall": (0.06, 0.12),
    "painting": (0.05, 0.10),
    "ceramic_tile": (0.08, 0.15),
    "carpet": (0.06, 0.12),
    "terrazzo": (0.10, 0.18),
    # Masonry
    "masonry": (0.08, 0.15),
    "masonry_brick": (0.08, 0.15),
    "masonry_stone": (0.10, 0.20),
    # Openings
    "curtain_wall": (0.10, 0.18),
    "windows": (0.08, 0.15),
    "glass": (0.08, 0.15),
    # MEP
    "plumbing_rough": (0.08, 0.15),
    "hvac": (0.08, 0.15),
    "ductwork": (0.08, 0.15),
    "electrical_rough": (0.08, 0.15),
    "fire_sprinkler": (0.06, 0.12),
    # Thermal / moisture
    "roofing": (0.08, 0.15),
    "waterproofing": (0.06, 0.12),
    "insulation": (0.06, 0.12),
    "siding": (0.08, 0.15),
    "fireproofing": (0.06, 0.12),
    # Labor
    "labor": (0.05, 0.12),
    # Default
    "default": (0.10, 0.20),
}


def get_uncertainty_range(category: str) -> tuple[float, float]:
    """Return (low_pct, high_pct) uncertainty range for a material category.

    Used by Monte Carlo to set per-item min/max bounds instead of blanket ±20%.
    """
    cat_lower = category.lower()
    if cat_lower in MATERIAL_UNCERTAINTY_RANGES:
        return MATERIAL_UNCERTAINTY_RANGES[cat_lower]
    # Substring match
    for key, rng in MATERIAL_UNCERTAINTY_RANGES.items():
        if key in cat_lower or cat_lower in key:
            return rng
    return MATERIAL_UNCERTAINTY_RANGES["default"]


# ---------------------------------------------------------------------------
# Reference cost database (RS Means-style base costs) — 60+ items
# ---------------------------------------------------------------------------

REFERENCE_COSTS: dict[tuple[str, str], dict] = {
    # Division 02 - Existing Conditions
    ("demolition", "SF"): {"base_cost": 8.50, "description": "Selective demolition"},
    ("environmental_remediation", "SF"): {
        "base_cost": 45.0,
        "description": "Environmental remediation",
    },
    # Division 03 - Concrete
    ("concrete", "CY"): {"base_cost": 185.0, "description": "Ready-mix concrete 4000 PSI"},
    ("concrete_foundation", "CY"): {"base_cost": 210.0, "description": "Concrete foundation"},
    ("concrete_slab", "CY"): {"base_cost": 195.0, "description": "Concrete slab on grade"},
    ("precast", "SF"): {"base_cost": 185.0, "description": "Precast concrete panels"},
    ("rebar", "TON"): {"base_cost": 1250.0, "description": "Reinforcing steel #4-#7"},
    ("formwork", "SFCA"): {"base_cost": 8.50, "description": "Formwork for slabs"},
    # Division 04 - Masonry
    ("masonry", "SF"): {"base_cost": 22.0, "description": "CMU block wall"},
    ("masonry_brick", "SF"): {"base_cost": 28.0, "description": "Brick masonry veneer"},
    ("masonry_stone", "SF"): {"base_cost": 45.0, "description": "Natural stone masonry"},
    # Division 05 - Metals
    ("structural_steel", "TON"): {"base_cost": 3200.0, "description": "Structural steel W shapes"},
    ("misc_metals", "TON"): {
        "base_cost": 4200.0,
        "description": "Miscellaneous metals and supports",
    },
    ("metal_decking", "SF"): {"base_cost": 6.50, "description": "Metal floor and roof decking"},
    ("steel_joists", "TON"): {"base_cost": 2800.0, "description": "Open web steel joists"},
    ("metal_stairs", "FLT"): {"base_cost": 6500.0, "description": "Metal pan stairs per flight"},
    # Division 06 - Wood, Plastics, and Composites
    ("rough_carpentry", "BF"): {"base_cost": 8.50, "description": "Rough carpentry lumber framing"},
    ("finish_carpentry", "LF"): {"base_cost": 12.0, "description": "Finish carpentry and trim"},
    ("millwork", "LF"): {"base_cost": 35.0, "description": "Architectural millwork"},
    ("casework", "LF"): {"base_cost": 450.0, "description": "Custom architectural casework"},
    # Division 07 - Thermal and Moisture Protection
    ("roofing", "SQ"): {"base_cost": 450.0, "description": "Built-up roofing"},
    ("roofing_membrane", "SQ"): {"base_cost": 520.0, "description": "Single-ply membrane roofing"},
    ("waterproofing", "SF"): {
        "base_cost": 4.50,
        "description": "Below-grade waterproofing membrane",
    },
    ("insulation", "SF"): {"base_cost": 2.80, "description": "Thermal insulation board"},
    ("siding", "SF"): {"base_cost": 9.50, "description": "Exterior siding panels"},
    ("fireproofing", "SF"): {"base_cost": 3.25, "description": "Spray-applied fireproofing"},
    ("caulking_sealants", "LF"): {"base_cost": 3.75, "description": "Joint sealants and caulking"},
    # Division 08 - Openings
    ("doors_hollow_metal", "EA"): {
        "base_cost": 850.0,
        "description": "Hollow metal door and frame",
    },
    ("doors_wood", "EA"): {"base_cost": 650.0, "description": "Solid core wood door and frame"},
    ("curtain_wall", "SF"): {"base_cost": 85.0, "description": "Aluminum curtain wall system"},
    ("windows", "SF"): {"base_cost": 45.0, "description": "Aluminum frame windows"},
    ("glazing", "SF"): {"base_cost": 32.0, "description": "Insulated glass glazing"},
    ("hardware", "EA"): {"base_cost": 350.0, "description": "Finish hardware per door"},
    # Division 09 - Finishes
    ("drywall", "SF"): {"base_cost": 3.50, "description": "5/8 Type X drywall"},
    ("painting", "SF"): {"base_cost": 1.75, "description": "Interior latex 2 coats"},
    ("ceramic_tile", "SF"): {"base_cost": 14.0, "description": "Ceramic floor and wall tile"},
    ("carpet", "SF"): {"base_cost": 6.50, "description": "Carpet tile flooring"},
    ("acoustic_ceiling", "SF"): {"base_cost": 5.50, "description": "Acoustic ceiling tile system"},
    ("terrazzo", "SF"): {"base_cost": 28.0, "description": "Poured terrazzo flooring"},
    ("epoxy_flooring", "SF"): {"base_cost": 8.50, "description": "Epoxy resin flooring"},
    ("vinyl_flooring", "SF"): {"base_cost": 5.25, "description": "Luxury vinyl tile flooring"},
    # Division 10 - Specialties
    ("toilet_accessories", "SET"): {
        "base_cost": 2500.0,
        "description": "Toilet partition and accessories set",
    },
    ("signage", "EA"): {"base_cost": 150.0, "description": "Interior and exterior signage"},
    ("lockers", "EA"): {"base_cost": 450.0, "description": "Metal lockers"},
    # Division 11 - Equipment
    ("kitchen_equipment", "EA"): {
        "base_cost": 25000.0,
        "description": "Commercial kitchen equipment package",
    },
    # Division 12 - Furnishings
    ("window_treatment", "SF"): {"base_cost": 12.0, "description": "Window blinds and treatments"},
    # Division 14 - Conveying Equipment
    ("elevator_hydraulic", "EA"): {
        "base_cost": 65000.0,
        "description": "Hydraulic passenger elevator",
    },
    ("elevator_traction", "EA"): {
        "base_cost": 120000.0,
        "description": "Traction passenger elevator",
    },
    # Division 21 - Fire Suppression
    ("fire_sprinkler", "SF"): {"base_cost": 4.50, "description": "Wet pipe fire sprinkler system"},
    ("fire_alarm", "SF"): {"base_cost": 3.25, "description": "Fire alarm system per SF"},
    # Division 22 - Plumbing
    ("plumbing_rough", "SF"): {"base_cost": 12.0, "description": "Plumbing rough-in per SF"},
    ("plumbing_fixture", "EA"): {"base_cost": 1200.0, "description": "Plumbing fixture with trim"},
    ("steel_pipe", "LF"): {"base_cost": 28.0, "description": "Steel pipe 2-inch schedule 40"},
    # Division 23 - HVAC
    ("hvac", "TON"): {"base_cost": 4500.0, "description": "HVAC per ton cooling"},
    ("ductwork", "LB"): {"base_cost": 8.50, "description": "Sheet metal ductwork"},
    ("hvac_controls", "PT"): {"base_cost": 1800.0, "description": "HVAC controls per point"},
    # Division 26 - Electrical
    ("electrical_rough", "SF"): {"base_cost": 15.0, "description": "Electrical rough-in per SF"},
    ("electrical_panel", "EA"): {
        "base_cost": 3500.0,
        "description": "Electrical distribution panel",
    },
    ("lighting", "SF"): {"base_cost": 6.50, "description": "Interior lighting fixtures per SF"},
    ("electrical_switchgear", "EA"): {
        "base_cost": 45000.0,
        "description": "Main switchgear assembly",
    },
    ("generator", "KW"): {"base_cost": 450.0, "description": "Emergency generator per KW"},
    # Division 31 - Earthwork
    ("excavation", "CY"): {"base_cost": 12.0, "description": "Bulk excavation"},
    ("backfill", "CY"): {"base_cost": 18.0, "description": "Structural backfill"},
    ("grading", "SY"): {"base_cost": 3.50, "description": "Finish grading"},
    ("piling", "LF"): {"base_cost": 85.0, "description": "Driven steel H-piles"},
    # Division 32 - Exterior Improvements
    ("asphalt_paving", "SY"): {"base_cost": 4.50, "description": "Asphalt pavement 3-inch"},
    ("concrete_paving", "SY"): {"base_cost": 45.0, "description": "Concrete pavement 6-inch"},
    ("landscaping", "SF"): {"base_cost": 8.0, "description": "Landscape planting and turf"},
    ("fencing", "LF"): {"base_cost": 35.0, "description": "Chain link fencing 6-foot"},
    # Division 33 - Utilities
    ("water_main", "LF"): {"base_cost": 65.0, "description": "Water main piping 8-inch"},
    ("sewer_main", "LF"): {"base_cost": 85.0, "description": "Sanitary sewer main 8-inch"},
    ("storm_drainage", "LF"): {"base_cost": 55.0, "description": "Storm drainage pipe 12-inch"},
    ("gas_piping", "LF"): {"base_cost": 42.0, "description": "Natural gas piping 2-inch"},
    # Labor
    ("labor_carpenter", "HR"): {"base_cost": 65.0, "description": "Carpenter journeyman"},
    ("labor_electrician", "HR"): {"base_cost": 75.0, "description": "Electrician journeyman"},
    ("labor_plumber", "HR"): {"base_cost": 72.0, "description": "Plumber journeyman"},
    ("labor_ironworker", "HR"): {"base_cost": 70.0, "description": "Ironworker journeyman"},
    ("labor_general", "HR"): {"base_cost": 45.0, "description": "General laborer"},
    ("labor_operator", "HR"): {"base_cost": 68.0, "description": "Heavy equipment operator"},
    ("labor_painter", "HR"): {"base_cost": 55.0, "description": "Painter journeyman"},
    ("labor_sheet_metal", "HR"): {
        "base_cost": 72.0,
        "description": "Sheet metal worker journeyman",
    },
    ("labor_hvac_tech", "HR"): {"base_cost": 74.0, "description": "HVAC technician journeyman"},
}

# Convert all base_cost values to Decimal for monetary precision
for _key, _val in REFERENCE_COSTS.items():
    if "base_cost" in _val:
        _val["base_cost"] = Decimal(str(_val["base_cost"]))

# Regional cost adjustment factors
REGION_FACTORS: dict[str, float] = {
    "national": 1.0,
    "northeast": 1.15,
    "southeast": 0.90,
    "midwest": 0.95,
    "west": 1.10,
    "northwest": 1.05,
}

# ---------------------------------------------------------------------------
# CSI code -> category mapping (40+ entries)
# ---------------------------------------------------------------------------

_CSI_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    # Division 02
    "02 41 00": ("demolition", "SF"),
    # Division 03
    "03 11 00": ("concrete_foundation", "CY"),
    "03 30 00": ("concrete", "CY"),
    "03 40 00": ("precast", "SF"),
    # Division 04
    "04 21 00": ("masonry_brick", "SF"),
    "04 43 00": ("masonry_stone", "SF"),
    # Division 05
    "05 12 00": ("structural_steel", "TON"),
    "05 21 00": ("steel_joists", "TON"),
    "05 50 00": ("misc_metals", "TON"),
    "05 51 00": ("metal_stairs", "FLT"),
    # Division 06
    "06 10 00": ("rough_carpentry", "BF"),
    "06 20 00": ("finish_carpentry", "LF"),
    "06 40 00": ("casework", "LF"),
    # Division 07
    "07 10 00": ("waterproofing", "SF"),
    "07 21 00": ("insulation", "SF"),
    "07 46 00": ("siding", "SF"),
    "07 50 00": ("roofing", "SQ"),
    "07 52 00": ("roofing_membrane", "SQ"),
    "07 81 00": ("fireproofing", "SF"),
    "07 92 00": ("caulking_sealants", "LF"),
    # Division 08
    "08 11 00": ("doors_hollow_metal", "EA"),
    "08 14 00": ("doors_wood", "EA"),
    "08 44 00": ("curtain_wall", "SF"),
    "08 50 00": ("windows", "SF"),
    "08 80 00": ("glazing", "SF"),
    # Division 09
    "09 29 00": ("drywall", "SF"),
    "09 30 00": ("ceramic_tile", "SF"),
    "09 65 00": ("vinyl_flooring", "SF"),
    "09 67 00": ("epoxy_flooring", "SF"),
    "09 68 00": ("carpet", "SF"),
    "09 91 00": ("painting", "SF"),
    # Division 21
    "21 13 00": ("fire_sprinkler", "SF"),
    # Division 22
    "22 05 00": ("plumbing_rough", "SF"),
    "22 40 00": ("plumbing_fixture", "EA"),
    # Division 23
    "23 31 00": ("ductwork", "LB"),
    "23 09 00": ("hvac_controls", "PT"),
    # Division 26
    "26 05 00": ("electrical_rough", "SF"),
    "26 24 00": ("electrical_panel", "EA"),
    "26 36 00": ("electrical_switchgear", "EA"),
    "26 51 00": ("lighting", "SF"),
    # Division 31
    "31 23 00": ("excavation", "CY"),
    "31 62 00": ("piling", "LF"),
    # Division 32
    "32 12 00": ("asphalt_paving", "SY"),
    "32 13 00": ("concrete_paving", "SY"),
    "32 31 00": ("fencing", "LF"),
    # Division 33
    "33 11 00": ("water_main", "LF"),
    "33 31 00": ("sewer_main", "LF"),
}

# Pre-computed word sets for keyword matching (built once at import time)
_REFERENCE_WORD_INDEX: dict[tuple[str, str], set[str]] = {}
for _key, _ref in REFERENCE_COSTS.items():
    _cat_name, _ = _key
    _words = set(_cat_name.replace("_", " ").lower().split())
    _words |= set(_ref["description"].lower().split())
    _REFERENCE_WORD_INDEX[_key] = _words


# ---------------------------------------------------------------------------
# Database cost item queries (expanded DDC CWICR data)
# ---------------------------------------------------------------------------


async def _query_db_by_csi(
    csi_code: str,
    db: AsyncSession | None = None,
) -> list[dict] | None:
    """Query cost_items table by CSI code prefix.

    Returns list of matching cost item dicts, or None if no DB session.
    """
    if db is None:
        return None

    from sqlalchemy import select as sa_select

    from app.models.estimating import CostItem

    # Match by exact CSI code or prefix (e.g., "03 30 00" or "03")
    stmt = (
        sa_select(CostItem)
        .where(CostItem.csi_code.isnot(None))
        .where(CostItem.csi_code.startswith(csi_code.strip()[:5]))
        .limit(50)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        return None

    return [
        {
            "category": row.category,
            "description": row.description,
            "unit": row.unit,
            "base_cost": float(row.base_unit_cost),
            "material_cost": float(row.material_cost) if row.material_cost else None,
            "labor_cost": float(row.labor_cost) if row.labor_cost else None,
            "equipment_cost": float(row.equipment_cost) if row.equipment_cost else None,
            "csi_code": row.csi_code,
            "data_source": row.data_source,
            "crew_size": float(row.crew_size) if row.crew_size else None,
            "manhours_per_unit": float(row.manhours_per_unit) if row.manhours_per_unit else None,
            "uncertainty_min": float(row.uncertainty_min) if row.uncertainty_min else None,
            "uncertainty_max": float(row.uncertainty_max) if row.uncertainty_max else None,
        }
        for row in rows
    ]


async def _query_db_by_category(
    category: str,
    unit: str | None = None,
    db: AsyncSession | None = None,
) -> dict | None:
    """Query cost_items table by category (and optionally unit).

    Returns the best-matching cost item dict, or None.
    """
    if db is None:
        return None

    from sqlalchemy import select as sa_select

    from app.models.estimating import CostItem

    stmt = sa_select(CostItem).where(CostItem.category == category.lower())
    if unit:
        stmt = stmt.where(CostItem.unit == unit.upper())
    stmt = stmt.limit(1)

    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        return None

    return {
        "category": row.category,
        "description": row.description,
        "unit": row.unit,
        "base_cost": float(row.base_unit_cost),
        "material_cost": float(row.material_cost) if row.material_cost else None,
        "labor_cost": float(row.labor_cost) if row.labor_cost else None,
        "equipment_cost": float(row.equipment_cost) if row.equipment_cost else None,
        "csi_code": row.csi_code,
        "data_source": row.data_source,
        "crew_size": float(row.crew_size) if row.crew_size else None,
        "manhours_per_unit": float(row.manhours_per_unit) if row.manhours_per_unit else None,
        "uncertainty_min": float(row.uncertainty_min) if row.uncertainty_min else None,
        "uncertainty_max": float(row.uncertainty_max) if row.uncertainty_max else None,
    }


async def _search_db_by_description(
    description: str,
    csi_code: str = "",
    db: AsyncSession | None = None,
    limit: int = 10,
) -> list[dict] | None:
    """Search cost_items by description keywords and optional CSI prefix.

    Returns scored list of matches from DB, or None if no DB session.
    """
    if db is None:
        return None

    from sqlalchemy import or_
    from sqlalchemy import select as sa_select

    from app.models.estimating import CostItem

    desc_words = description.lower().split()
    # Remove stop words
    stop_words = {"the", "a", "an", "and", "or", "for", "per", "of", "in", "to", "on", "at", "by"}
    keywords = [w for w in desc_words if w not in stop_words and len(w) > 2]

    if not keywords and not csi_code:
        return None

    stmt = sa_select(CostItem).where(CostItem.data_source != "manual")

    # Filter by CSI prefix if available
    if csi_code and len(csi_code.strip()) >= 2:
        csi_prefix = csi_code.strip()[:2]
        stmt = stmt.where(CostItem.csi_code.isnot(None))
        stmt = stmt.where(CostItem.csi_code.startswith(csi_prefix))

    # Keyword filter: match any keyword in description
    if keywords:
        keyword_filters = [CostItem.description.ilike(f"%{kw}%") for kw in keywords[:5]]
        stmt = stmt.where(or_(*keyword_filters))

    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        return None

    # Score results
    scored: list[dict[str, Any]] = []
    for row in rows:
        score = 0
        row_desc_lower = row.description.lower()

        # CSI match bonus
        if csi_code and row.csi_code and row.csi_code.startswith(csi_code.strip()[:5]):
            score += 100
        elif csi_code and row.csi_code and row.csi_code[:2] == csi_code.strip()[:2]:
            score += 50

        # Keyword matches
        for kw in keywords:
            if kw in row_desc_lower:
                score += 10

        scored.append(
            {
                "category": row.category,
                "description": row.description,
                "unit": row.unit,
                "base_cost": float(row.base_unit_cost),
                "material_cost": float(row.material_cost) if row.material_cost else None,
                "labor_cost": float(row.labor_cost) if row.labor_cost else None,
                "equipment_cost": float(row.equipment_cost) if row.equipment_cost else None,
                "csi_code": row.csi_code,
                "data_source": row.data_source,
                "score": score,
                "crew_size": float(row.crew_size) if row.crew_size else None,
                "manhours_per_unit": float(row.manhours_per_unit)
                if row.manhours_per_unit
                else None,
                "uncertainty_min": float(row.uncertainty_min) if row.uncertainty_min else None,
                "uncertainty_max": float(row.uncertainty_max) if row.uncertainty_max else None,
            }
        )

    def _score_key(item: dict[str, Any]) -> float:
        s = item.get("score")
        if s is None:
            return 0.0
        return float(s)

    scored.sort(key=_score_key, reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_series_id(category: str) -> str:
    """Return the BLS PPI series ID for a material category."""
    cat_lower = category.lower()
    # Check direct match in legacy map
    if cat_lower in _BLS_SERIES_MAP:
        return _BLS_SERIES_MAP[cat_lower]
    # Check full catalog by category
    if cat_lower in _CATEGORY_TO_BLS_SERIES:
        return _CATEGORY_TO_BLS_SERIES[cat_lower]
    # Substring containment
    for key, series in _BLS_SERIES_MAP.items():
        if key != "default" and key in cat_lower:
            return series
    return _BLS_SERIES_MAP["default"]


def _score_match(description: str, csi_code: str, ref_key: tuple[str, str]) -> int:
    """Score how well a description matches a reference cost entry.

    Scoring rules:
        - Exact CSI prefix match (first 2 digits of CSI division): +100
        - Category name exact match in description: +50
        - Per overlapping word between description and reference description: +10

    Returns the total score. A score >= 10 is considered a viable match.
    """
    cat_name, _ = ref_key
    score = 0

    desc_lower = description.lower()
    cat_lower = cat_name.lower()

    # 1. CSI prefix match
    if csi_code:
        csi_prefix = csi_code.strip()[:2]
        for mapped_csi, (mapped_cat, _) in _CSI_CATEGORY_MAP.items():
            if mapped_cat == cat_name and mapped_csi.strip()[:2] == csi_prefix:
                score += 100
                break

    # 2. Category name exact match in description
    if cat_lower.replace("_", " ") in desc_lower or cat_lower in desc_lower:
        score += 50

    # 3. Word overlap
    desc_words = set(desc_lower.replace("_", " ").split())
    ref_words = _REFERENCE_WORD_INDEX.get(ref_key, set())
    overlap = desc_words & ref_words
    overlap -= {"the", "a", "an", "and", "or", "for", "per", "of", "in", "to", "on"}
    score += len(overlap) * 10

    return score


# ---------------------------------------------------------------------------
# BLS v2 API — batch fetching
# ---------------------------------------------------------------------------


async def _fetch_bls_batch(
    series_ids: list[str],
    start_year: int | None = None,
    end_year: int | None = None,
) -> dict[str, list[dict]]:
    """Fetch multiple BLS series in a single v2 API call (up to 50).

    Returns dict mapping series_id -> list of observation dicts
    ``{year, period, periodName, value}``.

    Raises on network / API error.
    """
    api_key = _get_bls_api_key()
    current_year = date.today().year
    sy = start_year or (current_year - 10)
    ey = end_year or current_year

    payload: dict = {
        "seriesid": series_ids[:_BLS_MAX_SERIES_PER_REQUEST],
        "startyear": str(sy),
        "endyear": str(ey),
    }
    if api_key:
        payload["registrationkey"] = api_key

    headers = {"Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(_BLS_API_URL, json=payload, headers=headers)
        response.raise_for_status()

    data = response.json()

    if data.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError(f"BLS API returned status: {data.get('status')}")

    result: dict[str, list[dict]] = {}
    for series in data.get("Results", {}).get("series", []):
        sid = series.get("seriesID", "")
        result[sid] = series.get("data", [])

    return result


async def fetch_bls_history(
    series_id: str,
    years: int = 10,
) -> list[dict]:
    """Fetch monthly BLS observations for a series (sorted chronologically).

    Returns list of ``{date, value}`` dicts.  Uses a 24-hour cache.

    Raises ``BLSDataUnavailableError`` if API fails and no cache exists.
    """
    now = time.time()
    cached = _ppi_history_cache.get(series_id)
    if cached is not None:
        result, ts = cached
        if now - ts < _PPI_HISTORY_CACHE_TTL:
            return result

    current_year = date.today().year
    try:
        batch = await _fetch_bls_batch(
            [series_id],
            start_year=current_year - years,
            end_year=current_year,
        )
        observations = batch.get(series_id, [])
        results: list[dict] = []
        for entry in observations:
            year = entry["year"]
            period = entry["period"]
            if not period.startswith("M"):
                continue
            month = period.replace("M", "").zfill(2)
            results.append(
                {
                    "date": f"{year}-{month}-01",
                    "value": float(entry["value"]),
                }
            )
        results.sort(key=lambda d: d["date"])

        if results:
            _ppi_history_cache[series_id] = (results, now)
            return results

    except Exception as exc:
        logger.warning("BLS history fetch failed for %s: %s", series_id, exc)

    # Stale cache
    if cached is not None:
        logger.warning("Returning stale BLS history cache for %s", series_id)
        return cached[0]

    raise BLSDataUnavailableError(
        f"BLS API unavailable and no cached history for series {series_id}"
    )


# ---------------------------------------------------------------------------
# Regional factor integration
# ---------------------------------------------------------------------------


def _get_regional_adjustment(
    region: str = "national",
    location: dict | None = None,
) -> tuple[float, dict | None]:
    """Resolve a regional cost factor from location or legacy region string.

    Returns (composite_factor, info_dict_or_None).
    When a location dict is provided, uses the metro-level factor service.
    Otherwise falls back to the legacy REGION_FACTORS dict.
    """
    if location:
        from app.services.estimating.regional_factors import (
            AppliedRegionalFactor,
            get_regional_factor,
        )

        rf: AppliedRegionalFactor = get_regional_factor(
            city=location.get("city"),
            state=location.get("state"),
            zip_code=location.get("zip_code"),
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
        )
        info = {
            "metro": rf.metro,
            "state_abbr": rf.state_abbr,
            "material_factor": rf.material_factor,
            "labor_factor": rf.labor_factor,
            "equipment_factor": rf.equipment_factor,
            "composite_factor": rf.composite_factor,
            "is_fallback": rf.is_fallback,
            "distance_km": rf.distance_km,
            "warning": rf.warning,
        }
        return rf.composite_factor, info

    factor = REGION_FACTORS.get(region.lower(), 1.0)
    return factor, None


def _apply_component_factors(
    material_cost: float | None,
    labor_cost: float | None,
    equipment_cost: float | None,
    ppi_factor: float,
    location: dict | None = None,
    composite_factor: float = 1.0,
) -> dict:
    """Apply per-component regional factors when a location dict is provided.

    If location is given, uses the per-component factors (material, labor,
    equipment) instead of the single composite factor.
    """
    result: dict = {}
    if location:
        from app.services.estimating.regional_factors import get_regional_factor

        rf = get_regional_factor(
            city=location.get("city"),
            state=location.get("state"),
            zip_code=location.get("zip_code"),
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
        )
        if material_cost is not None:
            result["material_cost"] = round(material_cost * rf.material_factor * ppi_factor, 2)
        if labor_cost is not None:
            result["labor_cost"] = round(labor_cost * rf.labor_factor * ppi_factor, 2)
        if equipment_cost is not None:
            result["equipment_cost"] = round(equipment_cost * rf.equipment_factor * ppi_factor, 2)
    else:
        if material_cost is not None:
            result["material_cost"] = round(material_cost * composite_factor * ppi_factor, 2)
        if labor_cost is not None:
            result["labor_cost"] = round(labor_cost * composite_factor * ppi_factor, 2)
        if equipment_cost is not None:
            result["equipment_cost"] = round(equipment_cost * composite_factor * ppi_factor, 2)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_current_cost(
    category: str,
    description: str,
    unit: str,
    region: str = "national",
    db: AsyncSession | None = None,
    *,
    location: dict | None = None,
) -> dict:
    """Look up current unit cost from database with BLS PPI adjustment.

    Checks the expanded cost_items DB first (if a session is provided),
    then falls back to the hardcoded REFERENCE_COSTS.

    Args:
        location: Optional dict with city, state, zip_code, latitude,
            longitude for metro-level regional factor lookup.  Falls back
            to the legacy ``region`` string if not provided.

    Returns dict with: unit_cost, adjusted_cost, ppi_factor, data_source,
    effective_date, regional_factor_info, and optional cost breakdown fields.
    """
    region_factor, regional_info = _get_regional_adjustment(region, location)

    # 1. Try expanded DB first
    db_item = await _query_db_by_category(category, unit, db=db)
    if db_item and db_item["base_cost"] > 0:
        base_cost = db_item["base_cost"]
        series_id = _resolve_series_id(category)

        try:
            ppi_data = await fetch_bls_ppi(series_id)
            ppi_factor = ppi_data.get("ppi_factor", 1.0)
        except BLSDataUnavailableError:
            ppi_factor = 1.0

        adjusted_cost = (
            Decimal(str(base_cost)) * Decimal(str(region_factor)) * Decimal(str(ppi_factor))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        result = {
            "unit_cost": base_cost,
            "adjusted_cost": adjusted_cost,
            "ppi_factor": ppi_factor,
            "data_source": db_item["data_source"],
            "effective_date": date.today().isoformat(),
        }
        if regional_info:
            result["regional_factor"] = regional_info

        # Include cost breakdown with per-component factors when location available
        component_costs = _apply_component_factors(
            db_item.get("material_cost"),
            db_item.get("labor_cost"),
            db_item.get("equipment_cost"),
            ppi_factor,
            location=location,
            composite_factor=float(region_factor),
        )
        result.update(component_costs)

        if db_item.get("crew_size") is not None:
            result["crew_size"] = db_item["crew_size"]
        if db_item.get("manhours_per_unit") is not None:
            result["manhours_per_unit"] = db_item["manhours_per_unit"]
        return result

    # 2. Fall back to hardcoded REFERENCE_COSTS
    key = (category.lower(), unit.upper())
    ref = REFERENCE_COSTS.get(key)

    if ref is None:
        logger.warning("No reference cost for category=%s unit=%s", category, unit)
        return {
            "unit_cost": 0.0,
            "adjusted_cost": 0.0,
            "ppi_factor": 1.0,
            "data_source": "none",
            "effective_date": date.today().isoformat(),
        }

    base_cost = ref["base_cost"]
    base_cost_f = float(base_cost)
    series_id = _resolve_series_id(category)

    try:
        ppi_data = await fetch_bls_ppi(series_id)
        ppi_factor = ppi_data.get("ppi_factor", 1.0)
    except BLSDataUnavailableError:
        ppi_factor = 1.0

    adjusted_cost = round(base_cost_f * float(region_factor) * ppi_factor, 2)

    result = {
        "unit_cost": base_cost_f,
        "adjusted_cost": adjusted_cost,
        "ppi_factor": ppi_factor,
        "data_source": "reference_costs",
        "effective_date": date.today().isoformat(),
    }
    if regional_info:
        result["regional_factor"] = regional_info
    return result


async def fetch_bls_ppi(series_id: str) -> dict:
    """Fetch latest Producer Price Index from BLS API.

    Returns dict with: series_id, latest_value, latest_period, base_value,
    ppi_factor.

    Uses a 24-hour in-memory cache.  When the BLS API is unreachable and
    no stale cache exists, raises ``BLSDataUnavailableError``.
    """
    # 1. Check in-memory cache first
    now = time.time()
    cached = _ppi_cache.get(series_id)
    if cached is not None:
        result, ts = cached
        if now - ts < _PPI_CACHE_TTL:
            logger.debug("PPI cache hit for %s (age=%.0fs)", series_id, now - ts)
            return result

    # 2. Attempt real BLS API call
    try:
        result = await _fetch_bls_ppi_from_api(series_id)
        _ppi_cache[series_id] = (result, now)
        logger.debug("BLS PPI for %s: factor=%.3f (live)", series_id, result["ppi_factor"])
        return result
    except Exception as exc:
        logger.warning("BLS API call failed for %s: %s", series_id, exc)

    # 3. Return stale cache if available, otherwise raise
    if cached is not None:
        logger.warning("Returning stale cached PPI for %s", series_id)
        result, _ = cached
        _ppi_cache[series_id] = (result, now)  # refresh TTL to avoid retry storm
        return result

    raise BLSDataUnavailableError(
        f"BLS API unavailable and no cached PPI data for series {series_id}"
    )


async def _fetch_bls_ppi_from_api(series_id: str) -> dict:
    """Call the BLS v2 API and parse the PPI response.

    Uses v2 (authenticated) endpoint with ``BLS_API_KEY``.

    Raises on any failure so the caller can handle gracefully.
    """
    api_key = _get_bls_api_key()
    current_year = date.today().year

    payload: dict = {
        "seriesid": [series_id],
        "startyear": str(current_year - 2),
        "endyear": str(current_year),
    }
    if api_key:
        payload["registrationkey"] = api_key

    headers = {"Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(_BLS_API_URL, json=payload, headers=headers)
        response.raise_for_status()

    data = response.json()

    if data.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError(f"BLS API returned status: {data.get('status')}")

    series_data = data.get("Results", {}).get("series", [])
    if not series_data:
        raise ValueError("BLS API returned no series data")

    observations = series_data[0].get("data", [])
    if not observations:
        raise ValueError("BLS API returned no data points")

    # Observations are sorted most-recent first by the API
    latest = observations[0]
    latest_value = float(latest["value"])
    latest_year = latest["year"]
    latest_period = latest["periodName"]

    # BLS convention: base value = 100.0
    _BLS_BASE_VALUE = 100.0
    base_value = _BLS_BASE_VALUE

    ppi_factor = round(latest_value / base_value, 3) if base_value != 0 else 1.0

    return {
        "series_id": series_id,
        "latest_value": latest_value,
        "latest_period": f"{latest_year}-{latest_period}",
        "base_value": base_value,
        "ppi_factor": ppi_factor,
    }


# ---------------------------------------------------------------------------
# Cost Item Enrichment
# ---------------------------------------------------------------------------


async def enrich_cost_item(
    category: str,
    unit: str,
    region: str = "national",
    db: AsyncSession | None = None,
    *,
    location: dict | None = None,
) -> dict:
    """Return enriched cost data for a single item.

    Args:
        location: Optional dict with city, state, zip_code, latitude,
            longitude for metro-level regional factor lookup.

    Returns dict with:
        - unit_cost: base cost
        - adjusted_cost: PPI + region adjusted cost
        - ppi_factor: current PPI adjustment
        - trend_12m: "rising" | "falling" | "stable"
        - trend_pct_12m: float (e.g. 5.2 means +5.2% over 12 months)
        - uncertainty_low: float (e.g. 0.08 = ±8%)
        - uncertainty_high: float (e.g. 0.12 = ±12%)
        - cost_min: adjusted_cost * (1 - uncertainty_low)
        - cost_max: adjusted_cost * (1 + uncertainty_high)
        - data_source: str
        - effective_date: str
        - regional_factor: dict (if location provided)
    """
    # Try DB first, then hardcoded REFERENCE_COSTS
    db_item = await _query_db_by_category(category, unit, db=db)
    key = (category.lower(), unit.upper())
    ref = REFERENCE_COSTS.get(key)

    if db_item and db_item["base_cost"] > 0:
        base_cost = float(db_item["base_cost"])
        data_source = db_item["data_source"]
        unc_low = db_item.get("uncertainty_min") or get_uncertainty_range(category)[0]
        unc_high = db_item.get("uncertainty_max") or get_uncertainty_range(category)[1]
    elif ref is not None:
        base_cost = float(ref["base_cost"])
        data_source = "reference_costs"
        unc_low, unc_high = get_uncertainty_range(category)
    else:
        return {
            "unit_cost": 0.0,
            "adjusted_cost": 0.0,
            "ppi_factor": 1.0,
            "trend_12m": "stable",
            "trend_pct_12m": 0.0,
            "uncertainty_low": 0.10,
            "uncertainty_high": 0.20,
            "cost_min": 0.0,
            "cost_max": 0.0,
            "data_source": "none",
            "effective_date": date.today().isoformat(),
        }

    region_factor, regional_info = _get_regional_adjustment(region, location)
    series_id = _resolve_series_id(category)

    # PPI adjustment
    try:
        ppi_data = await fetch_bls_ppi(series_id)
        ppi_factor = ppi_data.get("ppi_factor", 1.0)
    except BLSDataUnavailableError:
        ppi_factor = 1.0

    adjusted_cost = round(base_cost * float(region_factor) * ppi_factor, 2)

    # 12-month trend
    trend_12m = "stable"
    trend_pct_12m = 0.0
    try:
        history = await fetch_bls_history(series_id, years=2)
        if len(history) >= 13:
            recent_value = history[-1]["value"]
            year_ago_value = history[-13]["value"]
            if year_ago_value > 0:
                trend_pct_12m = round(((recent_value - year_ago_value) / year_ago_value) * 100.0, 1)
                if trend_pct_12m > 2.0:
                    trend_12m = "rising"
                elif trend_pct_12m < -2.0:
                    trend_12m = "falling"
    except BLSDataUnavailableError:
        pass

    result = {
        "unit_cost": base_cost,
        "adjusted_cost": adjusted_cost,
        "ppi_factor": ppi_factor,
        "trend_12m": trend_12m,
        "trend_pct_12m": trend_pct_12m,
        "uncertainty_low": unc_low,
        "uncertainty_high": unc_high,
        "cost_min": round(adjusted_cost * (1 - unc_low), 2),
        "cost_max": round(adjusted_cost * (1 + unc_high), 2),
        "data_source": data_source,
        "effective_date": date.today().isoformat(),
    }
    if regional_info:
        result["regional_factor"] = regional_info

    # Add cost breakdown from DB items with per-component factors
    if db_item:
        component_costs = _apply_component_factors(
            db_item.get("material_cost"),
            db_item.get("labor_cost"),
            db_item.get("equipment_cost"),
            ppi_factor,
            location=location,
            composite_factor=float(region_factor),
        )
        result.update(component_costs)

        if db_item.get("crew_size") is not None:
            result["crew_size"] = db_item["crew_size"]
        if db_item.get("manhours_per_unit") is not None:
            result["manhours_per_unit"] = db_item["manhours_per_unit"]

    return result


# ---------------------------------------------------------------------------
# Cost matching (public)
# ---------------------------------------------------------------------------


async def match_costs(
    quantities: list[dict],
    region: str = "national",
    db: AsyncSession | None = None,
    *,
    location: dict | None = None,
) -> list[dict]:
    """Match extracted quantities to cost database entries.

    Takes quantity list from quantity_extractor and returns enriched list
    with unit_cost, total_cost, data_source fields added.

    Args:
        location: Optional dict with city, state, zip_code, latitude,
            longitude for metro-level regional factor lookup.

    If a DB session is provided, also searches the expanded cost_items table
    (DDC CWICR data) for matches beyond the hardcoded REFERENCE_COSTS.
    """
    results: list[dict] = []

    for item in quantities:
        enriched = {**item}
        csi_code = item.get("csi_code", "")
        quantity = float(item.get("quantity", 0))
        description = item.get("description", "")

        # Try to find a cost match via CSI code first (hardcoded map)
        cost_info = None
        category_match = _CSI_CATEGORY_MAP.get(csi_code)
        if category_match:
            category, preferred_unit = category_match
            cost_info = await get_current_cost(
                category,
                description,
                preferred_unit,
                region,
                db=db,
                location=location,
            )

        # If no CSI match from hardcoded map, try DB search by CSI + keywords
        if (cost_info is None or cost_info["unit_cost"] == 0.0) and db is not None:
            db_matches = await _search_db_by_description(description, csi_code, db=db)
            if db_matches:
                best = db_matches[0]
                if best.get("score", 0) >= 10:
                    cost_info = await get_current_cost(
                        best["category"],
                        description,
                        best["unit"],
                        region,
                        db=db,
                        location=location,
                    )

        # If still no match, try scored keyword matching against REFERENCE_COSTS
        if cost_info is None or cost_info["unit_cost"] == 0.0:
            best_score = 0
            best_key: tuple[str, str] | None = None

            for ref_key in REFERENCE_COSTS:
                score = _score_match(description, csi_code, ref_key)
                if score > best_score:
                    best_score = score
                    best_key = ref_key

            if best_key is not None and best_score >= 10:
                cat, cat_unit = best_key
                cost_info = await get_current_cost(
                    cat,
                    description,
                    cat_unit,
                    region,
                    location=location,
                )
            else:
                cost_info = {
                    "unit_cost": 0.0,
                    "adjusted_cost": 0.0,
                    "ppi_factor": 1.0,
                    "data_source": "unmatched",
                    "effective_date": "",
                }

        unit_cost = cost_info["adjusted_cost"]
        enriched["unit_cost"] = unit_cost
        enriched["total_cost"] = round(unit_cost * quantity, 2)
        enriched["data_source"] = cost_info["data_source"]

        # Include cost breakdown and regional factor info
        for field in (
            "material_cost",
            "labor_cost",
            "equipment_cost",
            "crew_size",
            "manhours_per_unit",
            "regional_factor",
        ):
            if field in cost_info:
                enriched[field] = cost_info[field]

        results.append(enriched)

    total = sum(r["total_cost"] for r in results)
    logger.info(
        "Matched costs for %d line items, total=%.2f, region=%s",
        len(results),
        total,
        region,
    )
    return results
