"""Embodied carbon calculations and LEED v5 credit evaluation.

Uses real embodied carbon factors from the ICE (Inventory of Carbon and
Energy) database v3.0, CLF (Carbon Leadership Forum) material baselines,
and published EPD (Environmental Product Declaration) data.

GWP categories follow EN 15978 / ISO 21930:
  A1-A3: Product stage (raw material, transport to factory, manufacturing)
  A4:    Transport to site
  A5:    Construction/installation
  B:     Use stage
  C:     End-of-life
  D:     Beyond system boundary (reuse/recycling credit)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CarbonFactorData:
    """In-memory carbon factor record."""

    csi_code: str
    material_name: str
    embodied_carbon_kgco2e: float
    unit: str
    data_source: str = "ICE"
    gwp_category: str = "A1-A3"
    notes: str = ""


@dataclass
class EmbodiedCarbonResult:
    """Result of an embodied carbon calculation."""

    total_kgco2e: float
    total_tonco2e: float
    carbon_per_sf: float | None
    by_division: dict[str, float]
    by_item: list[dict]
    item_count: int
    unmatched_items: list[str]
    gross_area_sf: float | None


@dataclass
class LEEDCreditEvaluation:
    """Result of evaluating a single LEED v5 credit."""

    credit_id: str
    credit_name: str
    category: str
    max_points: int
    status: str  # achievable / partial / not_achievable
    earned_points: int
    reasoning: str
    requirements: list[str]
    evidence: list[str] = field(default_factory=list)


@dataclass
class SustainabilityDashboard:
    """Full project sustainability summary."""

    project_id: str
    total_embodied_carbon_kgco2e: float
    carbon_per_sf: float | None
    baseline_comparison_pct: float | None
    embodied_carbon: EmbodiedCarbonResult
    leed_credits: list[LEEDCreditEvaluation]
    salvaged_materials: list[dict]
    recycled_content_pct: float
    total_leed_points: int
    max_possible_points: int
    calculated_at: str


# ---------------------------------------------------------------------------
# Carbon Factors — real values from ICE v3.0, CLF baselines, and EPDs
# ---------------------------------------------------------------------------
# Each entry: CSI code → {material, kgCO2e, unit, source, gwp_category, notes}
# Units match standard construction takeoff quantities.

CARBON_FACTORS: dict[str, CarbonFactorData] = {
    # Division 03 — Concrete
    "03 11 00": CarbonFactorData(
        "03 11 00", "Concrete forming", 15, "SF", "ICE", "A1-A3", "Formwork lumber + plywood"
    ),
    "03 21 00": CarbonFactorData(
        "03 21 00",
        "Reinforcing steel (#4–#11 rebar)",
        1900,
        "TON",
        "ICE",
        "A1-A3",
        "EAF recycled content ~97%",
    ),
    "03 30 00": CarbonFactorData(
        "03 30 00",
        "Cast-in-place concrete (4000 psi)",
        350,
        "CY",
        "CLF",
        "A1-A3",
        "CLF baseline: 60% OPC, 40% fly ash blend",
    ),
    "03 30 10": CarbonFactorData(
        "03 30 10", "Concrete (5000 psi)", 410, "CY", "CLF", "A1-A3", "Higher cement content"
    ),
    "03 30 20": CarbonFactorData(
        "03 30 20", "Concrete (3000 psi)", 290, "CY", "CLF", "A1-A3", "Lower cement content"
    ),
    "03 40 00": CarbonFactorData(
        "03 40 00", "Precast concrete", 400, "CY", "CLF", "A1-A3", "Plant-mixed precast"
    ),
    "03 45 00": CarbonFactorData(
        "03 45 00",
        "Precast architectural concrete",
        420,
        "CY",
        "CLF",
        "A1-A3",
        "White cement finish",
    ),
    # Division 04 — Masonry
    "04 21 00": CarbonFactorData(
        "04 21 00", "Clay brick masonry", 48, "SF", "ICE", "A1-A3", "Standard modular brick"
    ),
    "04 22 00": CarbonFactorData(
        "04 22 00", "Concrete masonry units (CMU)", 30, "SF", "ICE", "A1-A3", "8-inch standard CMU"
    ),
    "04 23 00": CarbonFactorData(
        "04 23 00", "Glass unit masonry", 55, "SF", "ICE", "A1-A3", "Glass block"
    ),
    # Division 05 — Metals
    "05 12 00": CarbonFactorData(
        "05 12 00",
        "Structural steel (W-shapes)",
        1850,
        "TON",
        "ICE",
        "A1-A3",
        "EAF + BOF blend, 93% recycled content",
    ),
    "05 21 00": CarbonFactorData(
        "05 21 00", "Steel joists", 1900, "TON", "ICE", "A1-A3", "Open-web steel joists"
    ),
    "05 31 00": CarbonFactorData(
        "05 31 00", "Steel decking", 1950, "TON", "ICE", "A1-A3", "Composite floor/roof deck"
    ),
    "05 40 00": CarbonFactorData(
        "05 40 00",
        "Cold-formed metal framing",
        2000,
        "TON",
        "ICE",
        "A1-A3",
        "Light-gauge steel studs",
    ),
    "05 50 00": CarbonFactorData(
        "05 50 00",
        "Miscellaneous metals",
        2100,
        "TON",
        "ICE",
        "A1-A3",
        "Angles, plates, connections",
    ),
    "05 52 00": CarbonFactorData(
        "05 52 00", "Metal railings", 2050, "TON", "ICE", "A1-A3", "Steel/aluminum railings"
    ),
    # Division 06 — Wood, Plastics, Composites
    "06 10 00": CarbonFactorData(
        "06 10 00",
        "Rough carpentry (softwood)",
        100,
        "MBF",
        "ICE",
        "A1-A3",
        "Sustainably harvested SPF lumber",
    ),
    "06 11 00": CarbonFactorData(
        "06 11 00", "Wood framing", 110, "MBF", "ICE", "A1-A3", "Dimensional lumber framing"
    ),
    "06 16 00": CarbonFactorData(
        "06 16 00", "Sheathing", 8, "SF", "ICE", "A1-A3", "OSB/plywood sheathing"
    ),
    "06 17 00": CarbonFactorData(
        "06 17 00", "Shop-fabricated structural wood", 120, "MBF", "EPD", "A1-A3", "Glulam beams"
    ),
    "06 18 00": CarbonFactorData(
        "06 18 00",
        "Cross-laminated timber (CLT)",
        85,
        "MBF",
        "EPD",
        "A1-A3",
        "Mass timber panel; significant carbon storage",
    ),
    # Division 07 — Thermal & Moisture Protection
    "07 11 00": CarbonFactorData(
        "07 11 00", "Dampproofing", 8, "SF", "ICE", "A1-A3", "Bituminous coating"
    ),
    "07 21 00": CarbonFactorData(
        "07 21 00", "Batt insulation (fiberglass)", 45, "SF", "ICE", "A1-A3", "R-19 fiberglass batt"
    ),
    "07 21 10": CarbonFactorData(
        "07 21 10", "Mineral wool insulation", 35, "SF", "ICE", "A1-A3", "Rockwool R-15"
    ),
    "07 21 20": CarbonFactorData(
        "07 21 20",
        "Spray foam insulation (closed cell)",
        75,
        "SF",
        "ICE",
        "A1-A3",
        "HFO-blown ccSPF",
    ),
    "07 21 30": CarbonFactorData(
        "07 21 30",
        "Cellulose insulation",
        15,
        "SF",
        "EPD",
        "A1-A3",
        "Recycled newsprint; low embodied carbon",
    ),
    "07 27 00": CarbonFactorData(
        "07 27 00", "Air barriers", 6, "SF", "ICE", "A1-A3", "Self-adhered membrane"
    ),
    "07 41 00": CarbonFactorData(
        "07 41 00",
        "Metal roofing (standing seam)",
        30,
        "SF",
        "ICE",
        "A1-A3",
        "Galvalume steel panel",
    ),
    "07 42 00": CarbonFactorData(
        "07 42 00", "Composite wall panels", 28, "SF", "ICE", "A1-A3", "Insulated metal panel"
    ),
    "07 51 00": CarbonFactorData(
        "07 51 00", "Built-up roofing", 22, "SF", "ICE", "A1-A3", "Modified bitumen membrane"
    ),
    "07 53 00": CarbonFactorData(
        "07 53 00", "EPDM single-ply membrane", 18, "SF", "ICE", "A1-A3", "60-mil EPDM"
    ),
    "07 54 00": CarbonFactorData(
        "07 54 00", "TPO single-ply membrane", 16, "SF", "EPD", "A1-A3", "60-mil TPO"
    ),
    # Division 08 — Openings
    "08 11 00": CarbonFactorData(
        "08 11 00", "Steel doors and frames", 250, "EA", "ICE", "A1-A3", "Hollow metal door + frame"
    ),
    "08 14 00": CarbonFactorData(
        "08 14 00", "Wood doors", 65, "EA", "ICE", "A1-A3", "Solid core wood door"
    ),
    "08 41 00": CarbonFactorData(
        "08 41 00",
        "Aluminum curtain wall",
        85,
        "SF",
        "ICE",
        "A1-A3",
        "Thermally broken aluminum + IGU",
    ),
    "08 44 00": CarbonFactorData(
        "08 44 00", "Curtain wall glazing", 40, "SF", "ICE", "A1-A3", "Double-pane low-e IGU"
    ),
    "08 51 00": CarbonFactorData(
        "08 51 00", "Aluminum windows", 45, "SF", "ICE", "A1-A3", "Thermal break aluminum frame"
    ),
    "08 71 00": CarbonFactorData(
        "08 71 00", "Door hardware", 15, "EA", "ICE", "A1-A3", "Lever set + closer"
    ),
    # Division 09 — Finishes
    "09 21 00": CarbonFactorData(
        "09 21 00", "Gypsum board assemblies", 12, "SF", "ICE", "A1-A3", '5/8" Type X drywall'
    ),
    "09 22 00": CarbonFactorData(
        "09 22 00",
        "Metal support assemblies (ceilings)",
        8,
        "SF",
        "ICE",
        "A1-A3",
        "Suspended ceiling grid",
    ),
    "09 30 00": CarbonFactorData(
        "09 30 00", "Tiling", 35, "SF", "ICE", "A1-A3", "Ceramic floor/wall tile"
    ),
    "09 51 00": CarbonFactorData(
        "09 51 00", "Acoustical ceilings", 10, "SF", "ICE", "A1-A3", "Mineral fiber ceiling tile"
    ),
    "09 65 00": CarbonFactorData(
        "09 65 00", "Resilient flooring", 25, "SF", "ICE", "A1-A3", "LVT / VCT"
    ),
    "09 66 00": CarbonFactorData(
        "09 66 00", "Terrazzo flooring", 30, "SF", "ICE", "A1-A3", "Epoxy terrazzo"
    ),
    "09 68 00": CarbonFactorData(
        "09 68 00", "Carpet", 20, "SF", "EPD", "A1-A3", "Carpet tile with recycled backing"
    ),
    "09 91 00": CarbonFactorData(
        "09 91 00", "Painting", 5, "SF", "ICE", "A1-A3", "Latex paint, 2 coats"
    ),
    # Division 10 — Specialties
    "10 14 00": CarbonFactorData(
        "10 14 00", "Signage", 10, "EA", "ICE", "A1-A3", "Interior/exterior signs"
    ),
    "10 21 00": CarbonFactorData(
        "10 21 00", "Toilet compartments", 40, "EA", "ICE", "A1-A3", "HDPE solid plastic"
    ),
    # Division 22 — Plumbing
    "22 11 00": CarbonFactorData(
        "22 11 00", "Copper pipe", 5500, "TON", "ICE", "A1-A3", "Type L copper tubing"
    ),
    "22 11 10": CarbonFactorData(
        "22 11 10", "PVC pipe", 3100, "TON", "ICE", "A1-A3", "Schedule 40 PVC"
    ),
    "22 42 00": CarbonFactorData(
        "22 42 00", "Plumbing fixtures", 120, "EA", "ICE", "A1-A3", "Porcelain fixtures"
    ),
    # Division 23 — HVAC
    "23 05 00": CarbonFactorData(
        "23 05 00",
        "HVAC ductwork (galvanized)",
        2800,
        "TON",
        "ICE",
        "A1-A3",
        "Galvanized sheet metal",
    ),
    "23 31 00": CarbonFactorData(
        "23 31 00", "HVAC ducts (spiral)", 2600, "TON", "ICE", "A1-A3", "Spiral duct"
    ),
    "23 64 00": CarbonFactorData(
        "23 64 00", "Packaged air handling units", 850, "EA", "EPD", "A1-A3", "Rooftop AHU"
    ),
    "23 81 00": CarbonFactorData(
        "23 81 00", "Decentralized HVAC equipment", 400, "EA", "EPD", "A1-A3", "Split system / VRF"
    ),
    # Division 26 — Electrical
    "26 05 00": CarbonFactorData(
        "26 05 00",
        "Electrical wire and cable",
        4200,
        "TON",
        "ICE",
        "A1-A3",
        "THHN/THWN copper conductor",
    ),
    "26 05 10": CarbonFactorData(
        "26 05 10", "Conduit (EMT)", 1800, "TON", "ICE", "A1-A3", "Electrical metallic tubing"
    ),
    "26 24 00": CarbonFactorData(
        "26 24 00",
        "Switchboards and panelboards",
        2500,
        "EA",
        "ICE",
        "A1-A3",
        "Main distribution panel",
    ),
    "26 51 00": CarbonFactorData(
        "26 51 00", "Interior lighting", 8, "EA", "EPD", "A1-A3", "LED fixture"
    ),
    # Division 31 — Earthwork
    "31 23 00": CarbonFactorData(
        "31 23 00", "Excavation and fill", 15, "CY", "ICE", "A1-A3", "Equipment fuel combustion"
    ),
    "31 31 00": CarbonFactorData(
        "31 31 00", "Soil treatment", 10, "CY", "ICE", "A1-A3", "Stabilization additives"
    ),
    # Division 32 — Exterior Improvements
    "32 12 00": CarbonFactorData(
        "32 12 00", "Asphalt paving", 50, "TON", "ICE", "A1-A3", "Hot-mix asphalt"
    ),
    "32 13 00": CarbonFactorData(
        "32 13 00", "Concrete paving", 300, "CY", "CLF", "A1-A3", "Flatwork concrete"
    ),
    "32 31 00": CarbonFactorData(
        "32 31 00", "Fencing", 1200, "TON", "ICE", "A1-A3", "Chain-link / metal fencing"
    ),
    "32 92 00": CarbonFactorData(
        "32 92 00", "Turf and grasses", 2, "SF", "ICE", "A1-A3", "Seed + topsoil"
    ),
    # Division 33 — Utilities
    "33 05 00": CarbonFactorData(
        "33 05 00", "Utility pipe (ductile iron)", 1800, "TON", "ICE", "A1-A3", "Ductile iron pipe"
    ),
    "33 11 00": CarbonFactorData(
        "33 11 00", "Water utilities", 1500, "TON", "ICE", "A1-A3", "Water main pipe"
    ),
    "33 31 00": CarbonFactorData(
        "33 31 00", "Sanitary sewer pipe", 2800, "TON", "ICE", "A1-A3", "PVC sewer pipe"
    ),
}


# ---------------------------------------------------------------------------
# CLF building-type baselines (kgCO2e per SF, A1-A3 only)
# ---------------------------------------------------------------------------
# Source: CLF Material Baselines 2023
# Used for comparing a project against industry average.

_CLF_BASELINES: dict[str, float] = {
    "office": 45.0,
    "commercial": 42.0,
    "retail": 38.0,
    "education": 48.0,
    "healthcare": 65.0,
    "hospital": 72.0,
    "residential_multifamily": 35.0,
    "residential_single": 30.0,
    "warehouse": 25.0,
    "industrial": 32.0,
    "mixed_use": 40.0,
    "hotel": 50.0,
    "laboratory": 70.0,
    "data_center": 55.0,
    "parking_structure": 38.0,
    "default": 42.0,
}


# ---------------------------------------------------------------------------
# LEED v5 Credit Requirements (BD+C New Construction)
# ---------------------------------------------------------------------------

LEED_V5_CREDITS: dict[str, dict] = {
    # Materials & Resources
    "MR_c1": {
        "id": "MR_c1",
        "name": "Building Life-Cycle Impact Reduction",
        "category": "Materials & Resources",
        "max_points": 5,
        "requirements": [
            "Conduct whole-building life-cycle assessment (LCA) per ISO 21930",
            "Demonstrate >=10% reduction in at least 3 of 6 impact categories vs baseline",
            "GWP reduction thresholds: 5% (1pt), 10% (2pts), 20% (3pts), 30% (4pts), 40% (5pts)",
        ],
        "thresholds": {"5_pct": 1, "10_pct": 2, "20_pct": 3, "30_pct": 4, "40_pct": 5},
    },
    "MR_c2": {
        "id": "MR_c2",
        "name": "Environmental Product Declarations",
        "category": "Materials & Resources",
        "max_points": 2,
        "requirements": [
            "Use >=20 permanently installed products with EPDs (1pt)",
            "Use >=40 permanently installed products with EPDs (2pts)",
            "EPDs must be third-party verified per ISO 14025 / EN 15804",
        ],
        "thresholds": {"20_products": 1, "40_products": 2},
    },
    "MR_c3": {
        "id": "MR_c3",
        "name": "Sourcing of Raw Materials",
        "category": "Materials & Resources",
        "max_points": 2,
        "requirements": [
            "Use products with recycled content (>=20% of total material cost) (1pt)",
            "Use salvaged/reused materials (>=5% of total material cost) (1pt)",
            "Extended producer responsibility products count toward credit",
        ],
        "thresholds": {"recycled_20pct": 1, "salvaged_5pct": 1},
    },
    "MR_c4": {
        "id": "MR_c4",
        "name": "Material Ingredients",
        "category": "Materials & Resources",
        "max_points": 2,
        "requirements": [
            "Use >=20 products with chemical inventory (HPD, Declare, C2C) (1pt)",
            "Use >=10 products that optimize material ingredients (C2C Gold, etc.) (2pts)",
        ],
        "thresholds": {"20_inventoried": 1, "10_optimized": 2},
    },
    "MR_c5": {
        "id": "MR_c5",
        "name": "Construction and Demolition Waste Management",
        "category": "Materials & Resources",
        "max_points": 2,
        "requirements": [
            "Divert >=50% of C&D waste from landfill (1pt)",
            "Divert >=75% of C&D waste from landfill (2pts)",
            "Generate <=2.5 lb of waste per SF (bonus path)",
        ],
        "thresholds": {"50_pct_diversion": 1, "75_pct_diversion": 2},
    },
    # Energy & Atmosphere
    "EA_c1": {
        "id": "EA_c1",
        "name": "Optimize Energy Performance",
        "category": "Energy & Atmosphere",
        "max_points": 18,
        "requirements": [
            "Demonstrate improvement vs ASHRAE 90.1-2019 baseline",
            "Points scale: 6% improvement (1pt) to 50% improvement (18pts)",
            "Must demonstrate compliance path via whole-building energy simulation",
        ],
        "thresholds": {
            "6_pct": 1,
            "12_pct": 3,
            "18_pct": 5,
            "24_pct": 8,
            "30_pct": 11,
            "36_pct": 14,
            "42_pct": 16,
            "50_pct": 18,
        },
    },
    "EA_c2": {
        "id": "EA_c2",
        "name": "Renewable Energy",
        "category": "Energy & Atmosphere",
        "max_points": 5,
        "requirements": [
            "On-site renewable energy: 1% (1pt) to 10% (5pts) of energy cost",
            "Off-site renewable energy contracts count at 50% value",
            "Green power / RECs: 2-year minimum commitment",
        ],
        "thresholds": {"1_pct": 1, "3_pct": 2, "5_pct": 3, "7_pct": 4, "10_pct": 5},
    },
    # Sustainable Sites
    "SS_c1": {
        "id": "SS_c1",
        "name": "Site Assessment",
        "category": "Sustainable Sites",
        "max_points": 1,
        "requirements": [
            "Complete site survey covering topography, hydrology, climate, vegetation",
            "Document soils, previous development, contamination",
        ],
        "thresholds": {"assessment_complete": 1},
    },
    "SS_c4": {
        "id": "SS_c4",
        "name": "Rainwater Management",
        "category": "Sustainable Sites",
        "max_points": 3,
        "requirements": [
            "Manage on-site the runoff from the 95th percentile storm (2pts)",
            "Manage on-site the runoff from the 98th percentile storm (3pts)",
            "Use LID techniques: rain gardens, bioswales, permeable pavement",
        ],
        "thresholds": {"95th_pctile": 2, "98th_pctile": 3},
    },
    "SS_c5": {
        "id": "SS_c5",
        "name": "Heat Island Reduction",
        "category": "Sustainable Sites",
        "max_points": 2,
        "requirements": [
            "Use high-albedo or vegetated surfaces for >=75% of site hardscape (1pt)",
            "Use cool/green roof for >=75% of roof area (1pt)",
            "SRI >= 33 for steep-slope, >= 82 for low-slope",
        ],
        "thresholds": {"site_75pct": 1, "roof_75pct": 1},
    },
    # Water Efficiency
    "WE_c1": {
        "id": "WE_c1",
        "name": "Outdoor Water Use Reduction",
        "category": "Water Efficiency",
        "max_points": 2,
        "requirements": [
            "Reduce outdoor water use by >=50% from baseline (2pts)",
            "No potable water for landscape irrigation (2pts alt path)",
        ],
        "thresholds": {"50_pct_reduction": 2, "no_potable": 2},
    },
    "WE_c2": {
        "id": "WE_c2",
        "name": "Indoor Water Use Reduction",
        "category": "Water Efficiency",
        "max_points": 6,
        "requirements": [
            "Reduce aggregate indoor water use: 25% (2pts), 30% (3pts), 35% (4pts), 40% (6pts)",
            "Use WaterSense-labeled fixtures",
            "Reduce process water (cooling, laundry)",
        ],
        "thresholds": {"25_pct": 2, "30_pct": 3, "35_pct": 4, "40_pct": 6},
    },
    # Indoor Environmental Quality
    "IEQ_c4": {
        "id": "IEQ_c4",
        "name": "Low-Emitting Materials",
        "category": "Indoor Environmental Quality",
        "max_points": 3,
        "requirements": [
            "Use low-VOC paints, coatings, adhesives, sealants (1pt per 2 categories, max 3)",
            "Meet CDPH Standard Method v1.2 or GreenGuard Gold for at least 3 of 6 categories",
            "Categories: paints/coatings, adhesives/sealants, flooring, composite wood, "
            "ceilings/walls, thermal/acoustic insulation",
        ],
        "thresholds": {"2_categories": 1, "4_categories": 2, "6_categories": 3},
    },
    "IEQ_c6": {
        "id": "IEQ_c6",
        "name": "Daylight",
        "category": "Indoor Environmental Quality",
        "max_points": 3,
        "requirements": [
            "Achieve illuminance of 300+ lux in >=55% of regularly occupied floor area (2pts)",
            "Achieve illuminance of 300+ lux in >=75% of regularly occupied floor area (3pts)",
            "Demonstrate via simulation or measurement",
        ],
        "thresholds": {"55_pct_area": 2, "75_pct_area": 3},
    },
}


# ---------------------------------------------------------------------------
# Unit conversion helpers
# ---------------------------------------------------------------------------

# Conversion factors to normalize quantities to the carbon factor's expected unit.
_UNIT_ALIASES: dict[str, str] = {
    "cubic yard": "CY",
    "cubic yards": "CY",
    "cy": "CY",
    "cu yd": "CY",
    "ton": "TON",
    "tons": "TON",
    "tn": "TON",
    "square foot": "SF",
    "square feet": "SF",
    "sf": "SF",
    "sq ft": "SF",
    "sqft": "SF",
    "each": "EA",
    "ea": "EA",
    "mbf": "MBF",
    "thousand board feet": "MBF",
    "lf": "LF",
    "linear foot": "LF",
    "linear feet": "LF",
}


def _normalize_unit(unit: str) -> str:
    """Normalize a unit string to standard abbreviation."""
    return _UNIT_ALIASES.get(unit.lower().strip(), unit.upper().strip())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_carbon_factor(
    csi_code: str,
    db=None,
) -> CarbonFactorData | None:
    """Look up the embodied carbon factor for a CSI code.

    Strategy:
      1. Exact match in database (if db provided)
      2. Exact match in CARBON_FACTORS dict
      3. Division-level prefix match (first 2 digits)
      4. None
    """
    if not csi_code:
        return None

    code = csi_code.strip()

    # 1. Database lookup (optional)
    if db is not None:
        try:
            from sqlalchemy import select

            from app.models.sustainability import CarbonFactor

            # Synchronous or async — caller is responsible for awaiting if needed
            stmt = select(CarbonFactor).where(CarbonFactor.csi_code == code).limit(1)
            # For sync usage in tests / calculations
            result = db.execute(stmt)
            row = result.scalars().first()
            if row:
                return CarbonFactorData(
                    csi_code=row.csi_code,
                    material_name=row.material_name,
                    embodied_carbon_kgco2e=float(row.embodied_carbon_kgco2e),
                    unit=row.unit,
                    data_source=row.data_source,
                    gwp_category=row.gwp_category,
                    notes=row.notes or "",
                )
        except Exception:
            logger.debug("DB carbon factor lookup failed for %s; using in-memory", code)

    # 2. Exact match in dict
    if code in CARBON_FACTORS:
        return CARBON_FACTORS[code]

    # 3. Division prefix match (first 2 digits)
    division = code[:2].strip() if len(code) >= 2 else ""
    if division:
        for key, factor in CARBON_FACTORS.items():
            if key.startswith(division):
                logger.debug("Carbon factor division match: %s → %s", code, key)
                return factor

    return None


def calculate_embodied_carbon(
    line_items: list[dict],
    carbon_factors: dict[str, CarbonFactorData] | None = None,
    gross_area_sf: float | None = None,
    transport_distance_km: float | None = None,
) -> EmbodiedCarbonResult:
    """Calculate total embodied carbon for a set of estimate line items.

    Parameters
    ----------
    line_items : list[dict]
        Each dict must have: csi_code, quantity, unit.
        Optional: description, unit_cost, weight_kg (for A4 transport calc).
    carbon_factors : dict | None
        Override carbon factors dict. Defaults to CARBON_FACTORS.
    gross_area_sf : float | None
        Building gross area in SF, for per-SF intensity calculation.
    transport_distance_km : float | None
        SV-27: When provided, calculates A4 transport emissions for each item.
        Uses ``weight_kg`` from line item if available, otherwise estimates
        from quantity. Formula: weight_kg * distance_km * TRANSPORT_FACTOR.

    Returns
    -------
    EmbodiedCarbonResult with total, per-division breakdown, and per-item details.
    """

    # SV-27: A4 transport emission factor (kg CO2e per kg per km, truck transport)
    TRANSPORT_FACTOR_KG_PER_KG_KM = 0.0001

    total_kgco2e = 0.0
    by_division: dict[str, float] = {}
    by_item: list[dict] = []
    unmatched: list[str] = []

    for item in line_items:
        csi_code = item.get("csi_code", "")
        quantity = float(item.get("quantity", 0))
        item_unit = _normalize_unit(item.get("unit", ""))
        description = item.get("description", "")

        if not csi_code or quantity <= 0:
            continue

        factor = get_carbon_factor(csi_code)
        if factor is None:
            unmatched.append(f"{csi_code}: {description}")
            continue

        factor_unit = _normalize_unit(factor.unit)

        # Unit conversion: if units don't match, attempt conversion
        conversion: float = 1.0
        if item_unit != factor_unit:
            converted = _get_unit_conversion(item_unit, factor_unit)
            if converted is None:
                # Cannot convert — skip with warning
                unmatched.append(
                    f"{csi_code}: {description} (unit mismatch: {item_unit} vs {factor_unit})"
                )
                continue
            conversion = converted

        item_kgco2e = factor.embodied_carbon_kgco2e * quantity * conversion

        # Division grouping (first 2 digits of CSI code)
        division = csi_code[:2].strip()
        division_name = _CSI_DIVISION_NAMES.get(division, f"Division {division}")
        by_division[division_name] = by_division.get(division_name, 0.0) + item_kgco2e

        # SV-27: Calculate A4 transport emissions when distance is provided
        a4_kgco2e = 0.0
        if transport_distance_km is not None and transport_distance_km > 0:
            weight_kg = item.get("weight_kg")
            if weight_kg is None:
                # Estimate weight from quantity (assume 1 unit ~= 1 kg as fallback)
                weight_kg = quantity * conversion
            a4_kgco2e = float(weight_kg) * transport_distance_km * TRANSPORT_FACTOR_KG_PER_KG_KM

        item_total = item_kgco2e + a4_kgco2e

        item_entry = {
            "csi_code": csi_code,
            "description": description or factor.material_name,
            "quantity": quantity,
            "unit": item_unit,
            "carbon_factor_kgco2e": factor.embodied_carbon_kgco2e,
            "factor_unit": factor_unit,
            "total_kgco2e": round(item_total, 2),
            "data_source": factor.data_source,
        }
        if a4_kgco2e > 0:
            item_entry["a4_transport_kgco2e"] = round(a4_kgco2e, 2)
        by_item.append(item_entry)

        total_kgco2e += item_total

    carbon_per_sf = None
    if gross_area_sf and gross_area_sf > 0:
        carbon_per_sf = round(total_kgco2e / gross_area_sf, 2)

    return EmbodiedCarbonResult(
        total_kgco2e=round(total_kgco2e, 2),
        total_tonco2e=round(total_kgco2e / 1000.0, 2),
        carbon_per_sf=carbon_per_sf,
        by_division={k: round(v, 2) for k, v in sorted(by_division.items())},
        by_item=by_item,
        item_count=len(by_item),
        unmatched_items=unmatched,
        gross_area_sf=gross_area_sf,
    )


def evaluate_leed_credits(
    project_data: dict,
    embodied_carbon: EmbodiedCarbonResult | None = None,
    salvaged_materials: list[dict] | None = None,
    recycled_content_pct: float = 0.0,
) -> list[LEEDCreditEvaluation]:
    """Evaluate LEED v5 credit eligibility for a project.

    Parameters
    ----------
    project_data : dict
        Project metadata: type, gross_area, energy_reduction_pct,
        renewable_energy_pct, waste_diversion_pct, etc.
    embodied_carbon : EmbodiedCarbonResult | None
        Result from calculate_embodied_carbon().
    salvaged_materials : list[dict] | None
        List of salvaged/reused materials with cost values.
    recycled_content_pct : float
        Percentage of materials (by cost) with recycled content.

    Returns
    -------
    List of LEEDCreditEvaluation for each evaluated credit.
    """
    results: list[LEEDCreditEvaluation] = []
    salvaged = salvaged_materials or []
    total_material_cost = float(project_data.get("total_material_cost", 0))

    for credit_id, credit in LEED_V5_CREDITS.items():
        evaluation = _evaluate_single_credit(
            credit_id=credit_id,
            credit=credit,
            project_data=project_data,
            embodied_carbon=embodied_carbon,
            salvaged_materials=salvaged,
            recycled_content_pct=recycled_content_pct,
            total_material_cost=total_material_cost,
        )
        results.append(evaluation)

    return results


def _evaluate_single_credit(
    credit_id: str,
    credit: dict,
    project_data: dict,
    embodied_carbon: EmbodiedCarbonResult | None,
    salvaged_materials: list[dict],
    recycled_content_pct: float,
    total_material_cost: float,
) -> LEEDCreditEvaluation:
    """Evaluate a single LEED credit."""
    max_points = credit["max_points"]
    requirements = credit["requirements"]
    evidence: list[str] = []
    earned = 0
    reasoning_parts: list[str] = []

    # --- MR_c1: Building Life-Cycle Impact Reduction ---
    if credit_id == "MR_c1":
        if embodied_carbon and embodied_carbon.carbon_per_sf is not None:
            project_type = project_data.get("type", "default")
            baseline = _CLF_BASELINES.get(project_type, _CLF_BASELINES["default"])
            reduction_pct = (
                (baseline - embodied_carbon.carbon_per_sf) / baseline * 100 if baseline > 0 else 0.0
            )
            evidence.append(
                f"Carbon intensity: {embodied_carbon.carbon_per_sf:.1f} kgCO2e/SF "
                f"vs baseline {baseline:.1f} kgCO2e/SF ({reduction_pct:.1f}% reduction)"
            )
            thresholds = credit.get("thresholds", {})
            if reduction_pct >= 40:
                earned = thresholds.get("40_pct", 5)
                reasoning_parts.append(f">=40% GWP reduction achieved ({reduction_pct:.1f}%)")
            elif reduction_pct >= 30:
                earned = thresholds.get("30_pct", 4)
                reasoning_parts.append(f">=30% GWP reduction ({reduction_pct:.1f}%)")
            elif reduction_pct >= 20:
                earned = thresholds.get("20_pct", 3)
                reasoning_parts.append(f">=20% GWP reduction ({reduction_pct:.1f}%)")
            elif reduction_pct >= 10:
                earned = thresholds.get("10_pct", 2)
                reasoning_parts.append(f">=10% GWP reduction ({reduction_pct:.1f}%)")
            elif reduction_pct >= 5:
                earned = thresholds.get("5_pct", 1)
                reasoning_parts.append(f">=5% GWP reduction ({reduction_pct:.1f}%)")
            else:
                reasoning_parts.append(f"GWP reduction {reduction_pct:.1f}% is below 5% threshold")
        else:
            reasoning_parts.append("No embodied carbon data available for LCA comparison")

    # --- MR_c2: EPDs ---
    elif credit_id == "MR_c2":
        epd_count = int(project_data.get("epd_product_count", 0))
        evidence.append(f"{epd_count} products with EPDs documented")
        if epd_count >= 40:
            earned = 2
            reasoning_parts.append(f">=40 EPD products ({epd_count})")
        elif epd_count >= 20:
            earned = 1
            reasoning_parts.append(f">=20 EPD products ({epd_count})")
        else:
            reasoning_parts.append(f"Only {epd_count} EPD products (need >=20)")

    # --- MR_c3: Sourcing of Raw Materials ---
    elif credit_id == "MR_c3":
        salvaged_cost = sum(float(m.get("cost", 0)) for m in salvaged_materials)
        salvaged_pct = (
            (salvaged_cost / total_material_cost * 100) if total_material_cost > 0 else 0.0
        )
        evidence.append(f"Recycled content: {recycled_content_pct:.1f}%")
        evidence.append(
            f"Salvaged materials: ${salvaged_cost:,.0f} ({salvaged_pct:.1f}% of material cost)"
        )
        if recycled_content_pct >= 20:
            earned += 1
            reasoning_parts.append(f"Recycled content >= 20% ({recycled_content_pct:.1f}%)")
        else:
            reasoning_parts.append(
                f"Recycled content {recycled_content_pct:.1f}% below 20% threshold"
            )
        if salvaged_pct >= 5:
            earned += 1
            reasoning_parts.append(f"Salvaged materials >= 5% ({salvaged_pct:.1f}%)")
        else:
            reasoning_parts.append(f"Salvaged materials {salvaged_pct:.1f}% below 5% threshold")
        earned = min(earned, max_points)

    # --- MR_c4: Material Ingredients ---
    elif credit_id == "MR_c4":
        inventoried = int(project_data.get("material_ingredient_count", 0))
        optimized = int(project_data.get("optimized_ingredient_count", 0))
        evidence.append(f"Inventoried: {inventoried}, Optimized: {optimized}")
        if optimized >= 10:
            earned = 2
            reasoning_parts.append(f">=10 optimized ingredient products ({optimized})")
        elif inventoried >= 20:
            earned = 1
            reasoning_parts.append(f">=20 inventoried products ({inventoried})")
        else:
            reasoning_parts.append(
                f"Insufficient: {inventoried} inventoried, {optimized} optimized"
            )

    # --- MR_c5: C&D Waste Management ---
    elif credit_id == "MR_c5":
        diversion_pct = float(project_data.get("waste_diversion_pct", 0))
        evidence.append(f"Waste diversion rate: {diversion_pct:.0f}%")
        if diversion_pct >= 75:
            earned = 2
            reasoning_parts.append(f">=75% waste diversion ({diversion_pct:.0f}%)")
        elif diversion_pct >= 50:
            earned = 1
            reasoning_parts.append(f">=50% waste diversion ({diversion_pct:.0f}%)")
        else:
            reasoning_parts.append(f"Waste diversion {diversion_pct:.0f}% below 50%")

    # --- EA_c1: Optimize Energy Performance ---
    elif credit_id == "EA_c1":
        energy_reduction = float(project_data.get("energy_reduction_pct", 0))
        evidence.append(f"Energy reduction vs ASHRAE 90.1: {energy_reduction:.0f}%")
        thresholds = credit.get("thresholds", {})
        # Find highest matching threshold
        for threshold_key, pts in sorted(thresholds.items(), key=lambda x: x[1], reverse=True):
            pct_val = int(threshold_key.split("_")[0])
            if energy_reduction >= pct_val:
                earned = pts
                reasoning_parts.append(f">={pct_val}% energy reduction ({energy_reduction:.0f}%)")
                break
        if earned == 0:
            reasoning_parts.append(f"Energy reduction {energy_reduction:.0f}% below 6% minimum")

    # --- EA_c2: Renewable Energy ---
    elif credit_id == "EA_c2":
        renewable_pct = float(project_data.get("renewable_energy_pct", 0))
        evidence.append(f"Renewable energy: {renewable_pct:.0f}% of energy cost")
        thresholds = credit.get("thresholds", {})
        for threshold_key, pts in sorted(thresholds.items(), key=lambda x: x[1], reverse=True):
            pct_val = int(threshold_key.split("_")[0])
            if renewable_pct >= pct_val:
                earned = pts
                reasoning_parts.append(f">={pct_val}% renewable energy ({renewable_pct:.0f}%)")
                break
        if earned == 0:
            reasoning_parts.append(f"Renewable energy {renewable_pct:.0f}% below 1% minimum")

    # --- SS_c1: Site Assessment ---
    elif credit_id == "SS_c1":
        has_assessment = bool(project_data.get("site_assessment_complete", False))
        evidence.append(f"Site assessment: {'Complete' if has_assessment else 'Not complete'}")
        if has_assessment:
            earned = 1
            reasoning_parts.append("Site assessment completed")
        else:
            reasoning_parts.append("Site assessment not documented")

    # --- SS_c4: Rainwater Management ---
    elif credit_id == "SS_c4":
        storm_managed = project_data.get("rainwater_percentile_managed", 0)
        evidence.append(f"Storm percentile managed: {storm_managed}")
        if storm_managed >= 98:
            earned = 3
            reasoning_parts.append("98th percentile storm managed on-site")
        elif storm_managed >= 95:
            earned = 2
            reasoning_parts.append("95th percentile storm managed on-site")
        else:
            reasoning_parts.append(f"Only {storm_managed}th percentile managed (need >=95)")

    # --- SS_c5: Heat Island Reduction ---
    elif credit_id == "SS_c5":
        site_hi = float(project_data.get("high_albedo_site_pct", 0))
        roof_hi = float(project_data.get("cool_roof_pct", 0))
        evidence.append(f"High-albedo site: {site_hi:.0f}%, Cool roof: {roof_hi:.0f}%")
        if site_hi >= 75:
            earned += 1
            reasoning_parts.append(f"High-albedo/vegetated site >= 75% ({site_hi:.0f}%)")
        if roof_hi >= 75:
            earned += 1
            reasoning_parts.append(f"Cool/green roof >= 75% ({roof_hi:.0f}%)")
        earned = min(earned, max_points)
        if earned == 0:
            reasoning_parts.append("Insufficient heat island measures")

    # --- WE_c1: Outdoor Water ---
    elif credit_id == "WE_c1":
        outdoor_reduction = float(project_data.get("outdoor_water_reduction_pct", 0))
        evidence.append(f"Outdoor water reduction: {outdoor_reduction:.0f}%")
        if outdoor_reduction >= 50:
            earned = 2
            reasoning_parts.append(f">=50% outdoor water reduction ({outdoor_reduction:.0f}%)")
        else:
            reasoning_parts.append(f"Outdoor water reduction {outdoor_reduction:.0f}% below 50%")

    # --- WE_c2: Indoor Water ---
    elif credit_id == "WE_c2":
        indoor_reduction = float(project_data.get("indoor_water_reduction_pct", 0))
        evidence.append(f"Indoor water reduction: {indoor_reduction:.0f}%")
        thresholds = credit.get("thresholds", {})
        for threshold_key, pts in sorted(thresholds.items(), key=lambda x: x[1], reverse=True):
            pct_val = int(threshold_key.split("_")[0])
            if indoor_reduction >= pct_val:
                earned = pts
                reasoning_parts.append(
                    f">={pct_val}% indoor water reduction ({indoor_reduction:.0f}%)"
                )
                break
        if earned == 0:
            reasoning_parts.append(
                f"Indoor water reduction {indoor_reduction:.0f}% below 25% minimum"
            )

    # --- IEQ_c4: Low-Emitting Materials ---
    elif credit_id == "IEQ_c4":
        low_emit_categories = int(project_data.get("low_emitting_categories", 0))
        evidence.append(f"Low-emitting material categories: {low_emit_categories}")
        if low_emit_categories >= 6:
            earned = 3
            reasoning_parts.append("All 6 categories use low-emitting materials")
        elif low_emit_categories >= 4:
            earned = 2
            reasoning_parts.append(f"{low_emit_categories} of 6 categories")
        elif low_emit_categories >= 2:
            earned = 1
            reasoning_parts.append(f"{low_emit_categories} of 6 categories")
        else:
            reasoning_parts.append(f"Only {low_emit_categories} categories (need >=2)")

    # --- IEQ_c6: Daylight ---
    elif credit_id == "IEQ_c6":
        daylight_pct = float(project_data.get("daylight_area_pct", 0))
        evidence.append(f"Daylit area: {daylight_pct:.0f}%")
        if daylight_pct >= 75:
            earned = 3
            reasoning_parts.append(">=75% of occupied area achieves 300+ lux")
        elif daylight_pct >= 55:
            earned = 2
            reasoning_parts.append(">=55% of occupied area achieves 300+ lux")
        else:
            reasoning_parts.append(f"Only {daylight_pct:.0f}% meets daylight threshold")

    # Determine status
    if earned >= max_points:
        status = "achievable"
    elif earned > 0:
        status = "partial"
    else:
        status = "not_achievable"

    reasoning = "; ".join(reasoning_parts) if reasoning_parts else "Insufficient data"

    return LEEDCreditEvaluation(
        credit_id=credit_id,
        credit_name=credit["name"],
        category=credit["category"],
        max_points=max_points,
        status=status,
        earned_points=earned,
        reasoning=reasoning,
        requirements=requirements,
        evidence=evidence,
    )


async def calculate_project_sustainability(
    db,
    project_id: str,
    gross_area_sf: float | None = None,
    project_data: dict | None = None,
    salvaged_materials: list[dict] | None = None,
    recycled_content_pct: float = 0.0,
) -> SustainabilityDashboard:
    """Full project sustainability calculation.

    Fetches estimate line items from the database, calculates embodied
    carbon, evaluates LEED credits, and compares against CLF baselines.

    Parameters
    ----------
    db : AsyncSession
        Database session.
    project_id : str
        Project UUID.
    gross_area_sf : float | None
        Building gross area for intensity calculation.
    project_data : dict | None
        Additional project metadata for LEED evaluation.
    salvaged_materials : list[dict] | None
        Salvaged/reused material entries.
    recycled_content_pct : float
        Percentage of recycled-content materials.
    """
    from sqlalchemy import select

    from app.models.estimating import CostEstimate, EstimateLineItem

    proj_data = project_data or {}

    # Fetch the latest completed estimate and its line items
    line_items_dicts: list[dict] = []
    try:
        import uuid as _uuid

        pid = _uuid.UUID(project_id) if isinstance(project_id, str) else project_id
        stmt = (
            select(CostEstimate)
            .where(
                CostEstimate.project_id == pid,
                CostEstimate.status == "completed",
            )
            .order_by(CostEstimate.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        estimate = result.scalars().first()

        if estimate:
            li_stmt = select(EstimateLineItem).where(EstimateLineItem.estimate_id == estimate.id)
            li_result = await db.execute(li_stmt)
            items = li_result.scalars().all()

            for li in items:
                line_items_dicts.append(
                    {
                        "csi_code": li.csi_code or "",
                        "description": li.description,
                        "quantity": float(li.quantity),
                        "unit": li.unit,
                        "unit_cost": float(li.unit_cost),
                    }
                )

            # Estimate gross area from assumptions if not provided
            if gross_area_sf is None and estimate.assumptions:
                gross_area_sf = estimate.assumptions.get("gross_area_sf")

            # Total material cost for LEED MR_c3
            if "total_material_cost" not in proj_data and estimate.total_cost:
                proj_data["total_material_cost"] = float(estimate.total_cost) * 0.45
    except Exception as exc:
        logger.warning("Failed to fetch estimate data for project %s: %s", project_id, exc)

    # Calculate embodied carbon
    embodied_carbon = calculate_embodied_carbon(
        line_items=line_items_dicts,
        gross_area_sf=gross_area_sf,
    )

    # Baseline comparison
    project_type = proj_data.get("type", "default")
    baseline = _CLF_BASELINES.get(project_type, _CLF_BASELINES["default"])
    baseline_comparison_pct = None
    if embodied_carbon.carbon_per_sf is not None and baseline > 0:
        baseline_comparison_pct = round(
            (baseline - embodied_carbon.carbon_per_sf) / baseline * 100, 2
        )

    # Evaluate LEED credits
    leed_credits = evaluate_leed_credits(
        project_data=proj_data,
        embodied_carbon=embodied_carbon,
        salvaged_materials=salvaged_materials,
        recycled_content_pct=recycled_content_pct,
    )

    total_points = sum(c.earned_points for c in leed_credits)
    max_points = sum(c.max_points for c in leed_credits)

    # Persist to project_sustainability table
    try:
        import uuid as _uuid

        from app.models.sustainability import ProjectSustainability

        pid = _uuid.UUID(project_id) if isinstance(project_id, str) else project_id

        ps_stmt = select(ProjectSustainability).where(ProjectSustainability.project_id == pid)
        result = await db.execute(ps_stmt)
        ps = result.scalars().first()

        if ps is None:
            ps = ProjectSustainability(project_id=pid)
            db.add(ps)

        ps.total_embodied_carbon_kgco2e = Decimal(str(embodied_carbon.total_kgco2e))
        ps.carbon_per_sf = (
            Decimal(str(embodied_carbon.carbon_per_sf))
            if embodied_carbon.carbon_per_sf is not None
            else None
        )
        ps.salvaged_materials = salvaged_materials or []
        ps.recycled_content_pct = Decimal(str(recycled_content_pct))
        ps.leed_credits = [
            {
                "credit_id": c.credit_id,
                "credit_name": c.credit_name,
                "category": c.category,
                "status": c.status,
                "earned_points": c.earned_points,
                "max_points": c.max_points,
            }
            for c in leed_credits
        ]
        ps.baseline_comparison_pct = (
            Decimal(str(baseline_comparison_pct)) if baseline_comparison_pct is not None else None
        )
        ps.last_calculated = datetime.now(UTC)

        await db.flush()
    except Exception as exc:
        logger.warning("Failed to persist sustainability data: %s", exc)

    return SustainabilityDashboard(
        project_id=project_id,
        total_embodied_carbon_kgco2e=embodied_carbon.total_kgco2e,
        carbon_per_sf=embodied_carbon.carbon_per_sf,
        baseline_comparison_pct=baseline_comparison_pct,
        embodied_carbon=embodied_carbon,
        leed_credits=leed_credits,
        salvaged_materials=salvaged_materials or [],
        recycled_content_pct=recycled_content_pct,
        total_leed_points=total_points,
        max_possible_points=max_points,
        calculated_at=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# IG-11: Carbon impact for a specific cost estimate
# ---------------------------------------------------------------------------


async def calculate_carbon_for_estimate(
    db,
    estimate_id: str,
) -> dict:
    """Calculate embodied carbon for all line items in a specific estimate.

    Parameters
    ----------
    db : AsyncSession
        Database session.
    estimate_id : str
        UUID of the CostEstimate to analyze.

    Returns
    -------
    dict with keys:
        - estimate_id: str
        - total_kgco2e: float
        - total_tonco2e: float
        - carbon_per_sf: float | None
        - items: list[dict]  (per-item carbon breakdown)
        - unmatched_items: list[str]
        - item_count: int
    """
    import uuid as _uuid

    from sqlalchemy import select as _select

    from app.models.estimating import CostEstimate, EstimateLineItem

    eid = _uuid.UUID(estimate_id) if isinstance(estimate_id, str) else estimate_id

    estimate = await db.get(CostEstimate, eid)
    if estimate is None:
        raise ValueError(f"Estimate not found: {estimate_id}")

    # Load line items
    li_stmt = _select(EstimateLineItem).where(EstimateLineItem.estimate_id == eid)
    li_result = await db.execute(li_stmt)
    line_items = list(li_result.scalars().all())

    if not line_items:
        return {
            "estimate_id": str(eid),
            "total_kgco2e": 0.0,
            "total_tonco2e": 0.0,
            "carbon_per_sf": None,
            "items": [],
            "unmatched_items": [],
            "item_count": 0,
        }

    # Build line item dicts for the carbon calculator
    li_dicts: list[dict] = []
    for li in line_items:
        li_dicts.append(
            {
                "csi_code": li.csi_code or "",
                "description": li.description,
                "quantity": float(li.quantity),
                "unit": li.unit,
                "unit_cost": float(li.unit_cost),
            }
        )

    # Determine gross area from estimate assumptions if available
    gross_area_sf = None
    assumptions = getattr(estimate, "assumptions", None) or {}
    if isinstance(assumptions, dict):
        gross_area_sf = assumptions.get("gross_area_sf")

    # Calculate
    result = calculate_embodied_carbon(
        line_items=li_dicts,
        gross_area_sf=gross_area_sf,
    )

    return {
        "estimate_id": str(eid),
        "total_kgco2e": result.total_kgco2e,
        "total_tonco2e": result.total_tonco2e,
        "carbon_per_sf": result.carbon_per_sf,
        "items": result.by_item,
        "unmatched_items": result.unmatched_items,
        "item_count": result.item_count,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_unit_conversion(from_unit: str, to_unit: str) -> float | None:
    """Get conversion factor from one unit to another.

    Returns multiplier such that: quantity_in_from_unit * multiplier = quantity_in_to_unit.
    Returns None if conversion is not possible.
    """
    if from_unit == to_unit:
        return 1.0

    conversions: dict[tuple[str, str], float] = {
        # Weight
        ("LB", "TON"): 0.0005,
        ("TON", "LB"): 2000.0,
        ("KG", "TON"): 0.001102,
        ("TON", "KG"): 907.185,
        # Volume
        ("CF", "CY"): 1.0 / 27.0,
        ("CY", "CF"): 27.0,
        ("GAL", "CY"): 0.004951,
        # Area
        ("SY", "SF"): 9.0,
        ("SF", "SY"): 1.0 / 9.0,
        # Length to area (not generally valid — skip)
    }

    return conversions.get((from_unit, to_unit))


_CSI_DIVISION_NAMES: dict[str, str] = {
    "01": "General Requirements",
    "02": "Existing Conditions",
    "03": "Concrete",
    "04": "Masonry",
    "05": "Metals",
    "06": "Wood, Plastics & Composites",
    "07": "Thermal & Moisture Protection",
    "08": "Openings",
    "09": "Finishes",
    "10": "Specialties",
    "11": "Equipment",
    "12": "Furnishings",
    "13": "Special Construction",
    "14": "Conveying Equipment",
    "21": "Fire Suppression",
    "22": "Plumbing",
    "23": "HVAC",
    "25": "Integrated Automation",
    "26": "Electrical",
    "27": "Communications",
    "28": "Electronic Safety & Security",
    "31": "Earthwork",
    "32": "Exterior Improvements",
    "33": "Utilities",
}


# ---------------------------------------------------------------------------
# SV-25: Regional grid emission factors for Scope 2 operational carbon
# ---------------------------------------------------------------------------

# EPA eGRID data — kg CO2e per kWh by US region
GRID_EMISSION_FACTORS: dict[str, float] = {
    "northeast": 0.25,
    "southeast": 0.40,
    "midwest": 0.45,
    "southwest": 0.35,
    "west": 0.20,
    "northwest": 0.15,
}


def estimate_operational_carbon(
    electricity_kwh: float,
    region: str,
) -> dict:
    """Estimate Scope 2 operational carbon from electricity consumption.

    Uses EPA eGRID regional emission factors.

    Parameters
    ----------
    electricity_kwh : float
        Annual electricity consumption in kWh.
    region : str
        US region key (northeast, southeast, midwest, southwest, west, northwest).

    Returns
    -------
    dict with total_kgco2e, total_tonco2e, emission_factor, region.

    Raises
    ------
    ValueError if region is not recognized.
    """
    region_lower = region.lower().strip()
    factor = GRID_EMISSION_FACTORS.get(region_lower)
    if factor is None:
        raise ValueError(
            f"Unknown region '{region}'. Supported: "
            f"{', '.join(sorted(GRID_EMISSION_FACTORS.keys()))}"
        )

    total_kgco2e = electricity_kwh * factor
    return {
        "total_kgco2e": round(total_kgco2e, 2),
        "total_tonco2e": round(total_kgco2e / 1000.0, 2),
        "emission_factor_kgco2e_per_kwh": factor,
        "region": region_lower,
        "electricity_kwh": electricity_kwh,
        "scope": "Scope 2 - Indirect (Electricity)",
    }


# ---------------------------------------------------------------------------
# SV-26: LEED submission documentation generation
# ---------------------------------------------------------------------------


def generate_leed_documentation(
    project_data: dict,
    credits_evaluation: list[LEEDCreditEvaluation],
) -> dict[str, dict]:
    """Generate per-credit narrative documentation for LEED submission.

    Returns a dict keyed by credit_id, each containing:
    - credit_name: str
    - narrative: str (the documentation text for submission)
    - evidence_summary: str
    - calculations: str
    - status: str

    This is the narrative text needed for actual LEED submission packages.

    Parameters
    ----------
    project_data : dict
        Project metadata (type, name, location, gross_area, etc.).
    credits_evaluation : list[LEEDCreditEvaluation]
        Results from evaluate_leed_credits().

    Returns
    -------
    dict[str, dict] keyed by credit_id.
    """
    project_name = project_data.get("name", "Project")
    project_type = project_data.get("type", "commercial")
    gross_area = project_data.get("gross_area", "N/A")
    location = project_data.get("location", "N/A")

    documentation: dict[str, dict] = {}

    for credit in credits_evaluation:
        if credit.earned_points == 0:
            narrative = (
                f"Credit {credit.credit_id} ({credit.credit_name}) was evaluated "
                f"but the project does not currently meet the requirements. "
                f"Assessment: {credit.reasoning}"
            )
        else:
            narrative = (
                f"Project '{project_name}' ({project_type}, {gross_area} SF, "
                f"located in {location}) achieves {credit.earned_points} of "
                f"{credit.max_points} points for {credit.credit_name} "
                f"(Category: {credit.category}).\n\n"
                f"Achievement basis: {credit.reasoning}\n\n"
                f"Requirements met: {'; '.join(credit.requirements) if credit.requirements else 'See evidence.'}"
            )

        evidence_summary = (
            "; ".join(credit.evidence) if credit.evidence else "No evidence documented."
        )

        calculations = (
            f"Points earned: {credit.earned_points}/{credit.max_points}. "
            f"Evaluation: {credit.reasoning}"
        )

        documentation[credit.credit_id] = {
            "credit_name": credit.credit_name,
            "category": credit.category,
            "narrative": narrative,
            "evidence_summary": evidence_summary,
            "calculations": calculations,
            "status": credit.status,
            "earned_points": credit.earned_points,
            "max_points": credit.max_points,
        }

    logger.info(
        "Generated LEED documentation for %d credits (%d achieved)",
        len(documentation),
        sum(1 for c in credits_evaluation if c.earned_points > 0),
    )
    return documentation
