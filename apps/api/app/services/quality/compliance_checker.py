"""Regulatory compliance checking service."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Seed-data-based checklists (250+ checks loaded from curated JSON)
# ---------------------------------------------------------------------------

_CHECKLIST_SEED_FILE = (
    Path(__file__).resolve().parents[3] / "data" / "seed" / "compliance_checklists_v1.json"
)

_checklist_cache: list[dict] | None = None


def _load_checklists() -> list[dict]:
    """Load compliance checklists from seed JSON."""
    global _checklist_cache
    if _checklist_cache is not None:
        return _checklist_cache

    _checklist_cache = []

    if not _CHECKLIST_SEED_FILE.exists():
        logger.warning("Compliance checklists seed file not found: %s", _CHECKLIST_SEED_FILE)
        return _checklist_cache or []

    with open(_CHECKLIST_SEED_FILE) as f:
        raw = json.load(f)

    if isinstance(raw, dict) and "checklists" in raw:
        entries = raw["checklists"]
    elif isinstance(raw, list):
        entries = raw
    else:
        logger.warning("Unexpected JSON format in %s", _CHECKLIST_SEED_FILE)
        return _checklist_cache or []

    _checklist_cache = entries
    logger.info("Loaded %d compliance checklists from seed file", len(entries))
    return _checklist_cache or []


def get_checklists(
    *,
    category: str | None = None,
    severity: str | None = None,
    phase: str | None = None,
    project_type: str | None = None,
) -> list[dict]:
    """Return compliance checklists filtered by optional criteria.

    Parameters
    ----------
    category: Filter by category (osha_safety, ibc_inspection,
        environmental_swppp, quality_control)
    severity: Filter by severity (critical, major, minor)
    phase: Filter by applicable construction phase
    project_type: Filter by applicable project type
    """
    checks = _load_checklists()
    result = checks

    if category:
        result = [c for c in result if c.get("category") == category]

    if severity:
        result = [c for c in result if c.get("severity") == severity]

    if phase:
        result = [c for c in result if phase in c.get("applicable_phases", [])]

    if project_type:
        result = [c for c in result if project_type in c.get("applicable_project_types", [])]

    return result


def get_checklist_by_id(check_id: str) -> dict | None:
    """Look up a single checklist item by check_id."""
    checks = _load_checklists()
    for c in checks:
        if c.get("check_id") == check_id:
            return c
    return None


def get_checklist_summary() -> dict:
    """Get summary statistics of all loaded checklists."""
    checks = _load_checklists()
    cats: dict[str, int] = {}
    sevs: dict[str, int] = {}
    for c in checks:
        cat = c.get("category", "unknown")
        cats[cat] = cats.get(cat, 0) + 1
        sev = c.get("severity", "unknown")
        sevs[sev] = sevs.get(sev, 0) + 1

    return {
        "total_checks": len(checks),
        "by_category": cats,
        "by_severity": sevs,
    }


def clear_checklist_cache() -> None:
    """Clear the checklist cache (for testing)."""
    global _checklist_cache
    _checklist_cache = None


# ---------------------------------------------------------------------------
# Comprehensive OSHA 1926 Construction Standards (40+ standards)
# ---------------------------------------------------------------------------

OSHA_STANDARDS: dict[str, dict] = {
    # --- General Safety (1926.20-1926.35) ---
    "1926.20": {
        "title": "General Safety and Health Provisions",
        "category": "general_safety",
        "subpart": "C",
    },
    "1926.21": {
        "title": "Safety Training and Education",
        "category": "general_safety",
        "subpart": "C",
    },
    "1926.23": {
        "title": "First Aid and Medical Attention",
        "category": "general_safety",
        "subpart": "C",
    },
    "1926.25": {
        "title": "Housekeeping",
        "category": "general_safety",
        "subpart": "C",
    },
    "1926.28": {
        "title": "Personal Protective Equipment",
        "category": "general_safety",
        "subpart": "C",
    },
    "1926.32": {
        "title": "Definitions",
        "category": "general_safety",
        "subpart": "C",
    },
    "1926.35": {
        "title": "Employee Emergency Action Plans",
        "category": "general_safety",
        "subpart": "C",
    },
    # --- PPE (1926.95-1926.107) ---
    "1926.95": {
        "title": "Criteria for Personal Protective Equipment",
        "category": "ppe",
        "subpart": "E",
    },
    "1926.100": {
        "title": "Head Protection",
        "category": "ppe",
        "subpart": "E",
    },
    "1926.101": {
        "title": "Hearing Protection",
        "category": "ppe",
        "subpart": "E",
    },
    "1926.102": {
        "title": "Eye and Face Protection",
        "category": "ppe",
        "subpart": "E",
    },
    "1926.103": {
        "title": "Respiratory Protection",
        "category": "ppe",
        "subpart": "E",
    },
    "1926.104": {
        "title": "Safety Belts, Lifelines, and Lanyards",
        "category": "ppe",
        "subpart": "E",
    },
    "1926.106": {
        "title": "Working Over or Near Water",
        "category": "ppe",
        "subpart": "E",
    },
    "1926.107": {
        "title": "Definitions Applicable to Subpart E",
        "category": "ppe",
        "subpart": "E",
    },
    # --- Fire Protection (1926.150-1926.159) ---
    "1926.150": {
        "title": "Fire Protection - General Requirements",
        "category": "fire_protection",
        "subpart": "F",
    },
    "1926.151": {
        "title": "Fire Prevention",
        "category": "fire_protection",
        "subpart": "F",
    },
    "1926.152": {
        "title": "Flammable Liquids",
        "category": "fire_protection",
        "subpart": "F",
    },
    "1926.155": {
        "title": "Fire Extinguishers and Hose Systems",
        "category": "fire_protection",
        "subpart": "F",
    },
    # --- Signs, Signals, and Barricades (1926.200-1926.203) ---
    "1926.200": {
        "title": "Accident Prevention Signs and Tags",
        "category": "signs_signals",
        "subpart": "G",
    },
    "1926.201": {
        "title": "Signaling",
        "category": "signs_signals",
        "subpart": "G",
    },
    "1926.202": {
        "title": "Barricades",
        "category": "signs_signals",
        "subpart": "G",
    },
    "1926.203": {
        "title": "Definitions Applicable to Subpart G",
        "category": "signs_signals",
        "subpart": "G",
    },
    # --- Materials Handling (1926.250-1926.252) ---
    "1926.250": {
        "title": "General Requirements for Storage",
        "category": "materials_handling",
        "subpart": "H",
    },
    "1926.251": {
        "title": "Rigging Equipment for Material Handling",
        "category": "materials_handling",
        "subpart": "H",
    },
    "1926.252": {
        "title": "Disposal of Waste Materials",
        "category": "materials_handling",
        "subpart": "H",
    },
    # --- Tools (1926.300-1926.307) ---
    "1926.300": {
        "title": "General Requirements - Tools",
        "category": "tools",
        "subpart": "I",
    },
    "1926.301": {
        "title": "Hand Tools",
        "category": "tools",
        "subpart": "I",
    },
    "1926.302": {
        "title": "Power-Operated Hand Tools",
        "category": "tools",
        "subpart": "I",
    },
    "1926.304": {
        "title": "Woodworking Tools",
        "category": "tools",
        "subpart": "I",
    },
    "1926.307": {
        "title": "Mechanical Power-Transmission Apparatus",
        "category": "tools",
        "subpart": "I",
    },
    # --- Welding and Cutting (1926.350-1926.354) ---
    "1926.350": {
        "title": "Gas Welding and Cutting",
        "category": "welding",
        "subpart": "J",
    },
    "1926.351": {
        "title": "Arc Welding and Cutting",
        "category": "welding",
        "subpart": "J",
    },
    "1926.352": {
        "title": "Fire Prevention During Welding",
        "category": "welding",
        "subpart": "J",
    },
    "1926.354": {
        "title": "Welding, Cutting, and Heating in Preservative Coatings",
        "category": "welding",
        "subpart": "J",
    },
    # --- Electrical (1926.400-1926.449) ---
    "1926.400": {
        "title": "Introduction - Electrical",
        "category": "electrical",
        "subpart": "K",
    },
    "1926.404": {
        "title": "Wiring Design and Protection",
        "category": "electrical",
        "subpart": "K",
    },
    "1926.405": {
        "title": "Wiring Methods, Components, and Equipment",
        "category": "electrical",
        "subpart": "K",
    },
    "1926.416": {
        "title": "Safety-Related Work Practices",
        "category": "electrical",
        "subpart": "K",
    },
    "1926.417": {
        "title": "Lockout and Tagging of Circuits",
        "category": "electrical",
        "subpart": "K",
    },
    "1926.431": {
        "title": "Maintenance of Equipment",
        "category": "electrical",
        "subpart": "K",
    },
    "1926.449": {
        "title": "Definitions Applicable to Subpart K",
        "category": "electrical",
        "subpart": "K",
    },
    # --- Scaffolding (1926.450-1926.454) ---
    "1926.450": {
        "title": "Scaffolding - Scope, Application, Definitions",
        "category": "scaffolding",
        "subpart": "L",
    },
    "1926.451": {
        "title": "Scaffolding - General Requirements",
        "category": "scaffolding",
        "subpart": "L",
    },
    "1926.452": {
        "title": "Additional Requirements for Specific Scaffold Types",
        "category": "scaffolding",
        "subpart": "L",
    },
    "1926.453": {
        "title": "Aerial Lifts",
        "category": "scaffolding",
        "subpart": "L",
    },
    "1926.454": {
        "title": "Training - Scaffolding",
        "category": "scaffolding",
        "subpart": "L",
    },
    # --- Fall Protection (1926.500-1926.503) ---
    "1926.500": {
        "title": "Fall Protection - Scope, Application, Definitions",
        "category": "fall_protection",
        "subpart": "M",
    },
    "1926.501": {
        "title": "Fall Protection - Duty to Have Fall Protection",
        "category": "fall_protection",
        "subpart": "M",
    },
    "1926.502": {
        "title": "Fall Protection Systems Criteria and Practices",
        "category": "fall_protection",
        "subpart": "M",
    },
    "1926.503": {
        "title": "Fall Protection Training Requirements",
        "category": "fall_protection",
        "subpart": "M",
    },
    # --- Cranes and Derricks (1926.550-1926.556) ---
    "1926.550": {
        "title": "Cranes and Derricks - General Requirements",
        "category": "cranes",
        "subpart": "N",
    },
    "1926.552": {
        "title": "Material Hoists, Personnel Hoists, and Elevators",
        "category": "cranes",
        "subpart": "N",
    },
    "1926.553": {
        "title": "Base-Mounted Drum Hoists",
        "category": "cranes",
        "subpart": "N",
    },
    "1926.554": {
        "title": "Overhead Hoists",
        "category": "cranes",
        "subpart": "N",
    },
    "1926.555": {
        "title": "Conveyors",
        "category": "cranes",
        "subpart": "N",
    },
    "1926.556": {
        "title": "Aerial Lifts (Subpart N)",
        "category": "cranes",
        "subpart": "N",
    },
    # --- Excavation (1926.650-1926.652) ---
    "1926.650": {
        "title": "Excavations - Scope, Application, Definitions",
        "category": "excavation",
        "subpart": "P",
    },
    "1926.651": {
        "title": "Excavations - Specific Excavation Requirements",
        "category": "excavation",
        "subpart": "P",
    },
    "1926.652": {
        "title": "Excavations - Requirements for Protective Systems",
        "category": "excavation",
        "subpart": "P",
    },
    # --- Concrete and Masonry (1926.700-1926.706) ---
    "1926.700": {
        "title": "Concrete and Masonry - Scope, Application, Definitions",
        "category": "concrete_masonry",
        "subpart": "Q",
    },
    "1926.701": {
        "title": "General Requirements - Concrete and Masonry",
        "category": "concrete_masonry",
        "subpart": "Q",
    },
    "1926.702": {
        "title": "Requirements for Equipment and Tools",
        "category": "concrete_masonry",
        "subpart": "Q",
    },
    "1926.703": {
        "title": "Requirements for Cast-in-Place Concrete",
        "category": "concrete_masonry",
        "subpart": "Q",
    },
    "1926.706": {
        "title": "Requirements for Masonry Construction",
        "category": "concrete_masonry",
        "subpart": "Q",
    },
    # --- Steel Erection (1926.750-1926.761) ---
    "1926.750": {
        "title": "Steel Erection - Scope",
        "category": "steel_erection",
        "subpart": "R",
    },
    "1926.751": {
        "title": "Steel Erection - Definitions",
        "category": "steel_erection",
        "subpart": "R",
    },
    "1926.752": {
        "title": "Site Layout, Site-Specific Erection Plan, Construction Sequence",
        "category": "steel_erection",
        "subpart": "R",
    },
    "1926.753": {
        "title": "Hoisting and Rigging",
        "category": "steel_erection",
        "subpart": "R",
    },
    "1926.754": {
        "title": "Structural Steel Assembly",
        "category": "steel_erection",
        "subpart": "R",
    },
    "1926.755": {
        "title": "Column Anchorage",
        "category": "steel_erection",
        "subpart": "R",
    },
    "1926.756": {
        "title": "Beams and Columns",
        "category": "steel_erection",
        "subpart": "R",
    },
    "1926.757": {
        "title": "Open Web Steel Joists",
        "category": "steel_erection",
        "subpart": "R",
    },
    "1926.760": {
        "title": "Fall Protection - Steel Erection",
        "category": "steel_erection",
        "subpart": "R",
    },
    "1926.761": {
        "title": "Training - Steel Erection",
        "category": "steel_erection",
        "subpart": "R",
    },
    # --- Demolition (1926.850-1926.860) ---
    "1926.850": {
        "title": "Preparatory Operations - Demolition",
        "category": "demolition",
        "subpart": "T",
    },
    "1926.851": {
        "title": "Stairs, Passageways, and Ladders - Demolition",
        "category": "demolition",
        "subpart": "T",
    },
    "1926.852": {
        "title": "Chutes - Demolition",
        "category": "demolition",
        "subpart": "T",
    },
    "1926.854": {
        "title": "Removal of Walls, Masonry Sections, and Chimneys",
        "category": "demolition",
        "subpart": "T",
    },
    "1926.855": {
        "title": "Manual Removal of Floors",
        "category": "demolition",
        "subpart": "T",
    },
    "1926.856": {
        "title": "Removal of Walls, Floors, and Material with Equipment",
        "category": "demolition",
        "subpart": "T",
    },
    "1926.858": {
        "title": "Removal of Steel Construction",
        "category": "demolition",
        "subpart": "T",
    },
    "1926.859": {
        "title": "Mechanical Demolition",
        "category": "demolition",
        "subpart": "T",
    },
    "1926.860": {
        "title": "Selective Demolition by Explosives",
        "category": "demolition",
        "subpart": "T",
    },
    # --- Stairways and Ladders (1926.1050-1926.1060) ---
    "1926.1050": {
        "title": "Stairways and Ladders - Scope, Application, Definitions",
        "category": "stairways_ladders",
        "subpart": "X",
    },
    "1926.1051": {
        "title": "General Requirements - Stairways and Ladders",
        "category": "stairways_ladders",
        "subpart": "X",
    },
    "1926.1052": {
        "title": "Stairways",
        "category": "stairways_ladders",
        "subpart": "X",
    },
    "1926.1053": {
        "title": "Ladders",
        "category": "stairways_ladders",
        "subpart": "X",
    },
    "1926.1060": {
        "title": "Training - Stairways and Ladders",
        "category": "stairways_ladders",
        "subpart": "X",
    },
    # --- Confined Spaces (1926.1200-1926.1213) ---
    "1926.1200": {
        "title": "Confined Spaces - Scope",
        "category": "confined_spaces",
        "subpart": "AA",
    },
    "1926.1201": {
        "title": "Confined Spaces - Scope Application",
        "category": "confined_spaces",
        "subpart": "AA",
    },
    "1926.1202": {
        "title": "Definitions - Confined Spaces",
        "category": "confined_spaces",
        "subpart": "AA",
    },
    "1926.1203": {
        "title": "General Requirements - Confined Spaces",
        "category": "confined_spaces",
        "subpart": "AA",
    },
    "1926.1204": {
        "title": "Permit-Required Confined Spaces",
        "category": "confined_spaces",
        "subpart": "AA",
    },
    "1926.1206": {
        "title": "Entry Rescue and Emergency Services",
        "category": "confined_spaces",
        "subpart": "AA",
    },
    "1926.1207": {
        "title": "Training - Confined Spaces",
        "category": "confined_spaces",
        "subpart": "AA",
    },
    "1926.1211": {
        "title": "Rescue and Emergency Services",
        "category": "confined_spaces",
        "subpart": "AA",
    },
    "1926.1213": {
        "title": "Confined Spaces - Recordkeeping",
        "category": "confined_spaces",
        "subpart": "AA",
    },
}

# ---------------------------------------------------------------------------
# IBC (International Building Code) basic checks
# ---------------------------------------------------------------------------

IBC_STANDARDS: dict[str, dict] = {
    "IBC-302": {
        "title": "Occupancy Classification",
        "category": "ibc_occupancy",
        "description": "Correct occupancy classification (A, B, E, F, H, I, M, R, S, U)",
    },
    "IBC-1004": {
        "title": "Occupant Load",
        "category": "ibc_occupancy",
        "description": "Maximum occupant load calculation per Table 1004.5",
    },
    "IBC-1006": {
        "title": "Number of Exits and Exit Configuration",
        "category": "ibc_egress",
        "description": "Minimum number of exits based on occupant load",
    },
    "IBC-1005": {
        "title": "Means of Egress Sizing",
        "category": "ibc_egress",
        "description": "Egress width calculation (0.3 in per occupant stairs, 0.2 in other)",
    },
    "IBC-1009": {
        "title": "Accessible Means of Egress",
        "category": "ibc_egress",
        "description": "ADA-compliant egress routes and areas of refuge",
    },
    "IBC-1020": {
        "title": "Exit Access Travel Distance",
        "category": "ibc_egress",
        "description": "Maximum travel distance to exit (200-300 ft depending on occupancy)",
    },
    "IBC-602": {
        "title": "Fire-Resistance Rating Requirements",
        "category": "ibc_fire_resistance",
        "description": "Required fire-resistance ratings for building elements by type",
    },
    "IBC-603": {
        "title": "Allowable Materials by Construction Type",
        "category": "ibc_fire_resistance",
        "description": "Combustible vs non-combustible materials per construction type",
    },
    "IBC-704": {
        "title": "Exterior Wall Fire-Resistance",
        "category": "ibc_fire_resistance",
        "description": "Fire-resistance based on fire separation distance",
    },
    "IBC-903": {
        "title": "Automatic Sprinkler Systems",
        "category": "ibc_fire_resistance",
        "description": "When automatic sprinkler systems are required",
    },
}

# ---------------------------------------------------------------------------
# Project-type-aware checklist definitions
# ---------------------------------------------------------------------------

_PROJECT_TYPE_CATEGORIES: dict[str, list[str]] = {
    "residential": [
        "general_safety",
        "ppe",
        "fall_protection",
        "electrical",
        "stairways_ladders",
        "scaffolding",
        "tools",
        "fire_protection",
        "ibc_occupancy",
        "ibc_egress",
        "ibc_fire_resistance",
    ],
    "commercial": [
        "general_safety",
        "ppe",
        "fall_protection",
        "scaffolding",
        "electrical",
        "fire_protection",
        "cranes",
        "concrete_masonry",
        "steel_erection",
        "stairways_ladders",
        "signs_signals",
        "materials_handling",
        "tools",
        "welding",
        "confined_spaces",
        "ibc_occupancy",
        "ibc_egress",
        "ibc_fire_resistance",
    ],
    "infrastructure": [
        "general_safety",
        "ppe",
        "excavation",
        "cranes",
        "fall_protection",
        "scaffolding",
        "electrical",
        "concrete_masonry",
        "steel_erection",
        "signs_signals",
        "materials_handling",
        "tools",
        "welding",
        "confined_spaces",
        "demolition",
        "fire_protection",
    ],
}


def _get_applicable_standards(
    project_type: str | None = None,
) -> dict[str, dict]:
    """Return standards applicable to a given project type.

    Combines OSHA and IBC standards filtered by applicable categories.
    If no project_type specified, returns all standards.
    """
    if project_type is None:
        combined = {}
        combined.update(OSHA_STANDARDS)
        combined.update(IBC_STANDARDS)
        return combined

    applicable_categories = _PROJECT_TYPE_CATEGORIES.get(
        project_type,
        # Default: return all categories if type is unknown
        list({s.get("category", "general") for s in OSHA_STANDARDS.values()})
        + list({s.get("category", "general") for s in IBC_STANDARDS.values()}),
    )

    combined = {}
    for code, standard in OSHA_STANDARDS.items():
        if standard.get("category") in applicable_categories:
            combined[code] = standard
    for code, standard in IBC_STANDARDS.items():
        if standard.get("category") in applicable_categories:
            combined[code] = standard

    return combined


async def check_project_compliance(
    project_id: str,
    regulations: list[str] | None = None,
    project_data: dict | None = None,
    project_type: str | None = None,
) -> list[dict]:
    """Check project compliance against regulations.

    Parameters
    ----------
    project_id: Project UUID string
    regulations: List of regulation codes to check. If None, checks all
        applicable standards for the project type.
    project_data: Dict with project info for evaluation including:
        - safety_measures: list of dicts with "type" keys
        - active_zones: list of active work zones
        - occupancy_type: str (IBC occupancy classification)
        - construction_type: str (I, II, III, IV, V)
        - num_exits: int
        - has_sprinkler: bool
        - fire_resistance_rating: float (hours)
    project_type: Optional project type ("residential", "commercial",
        "infrastructure") for project-type-aware checklists.

    Returns list of compliance check results.
    """
    # Get applicable standards based on project type
    all_standards = _get_applicable_standards(project_type)

    if regulations is None:
        regulations = list(all_standards.keys())

    results = []
    for code in regulations:
        # Look up in combined OSHA + IBC standards
        standard = all_standards.get(code)
        if standard is None:
            # Check in full registries as fallback
            standard = OSHA_STANDARDS.get(code) or IBC_STANDARDS.get(code)

        if not standard:
            results.append(
                {
                    "regulation_code": code,
                    "regulation_title": "Unknown Standard",
                    "status": "skipped",
                    "check_result": "Standard not in database",
                    "findings": [],
                }
            )
            continue

        # Evaluate compliance
        check_result = _evaluate_compliance(code, standard, project_data or {})
        results.append(
            {
                "regulation_code": code,
                "regulation_title": standard["title"],
                "category": standard.get("category", "general"),
                "status": check_result["status"],
                "check_result": check_result["result"],
                "findings": check_result["findings"],
            }
        )

    passed = sum(1 for r in results if r["status"] == "pass")
    total = len(results)
    logger.info(
        "Compliance check for project %s: %d/%d passed (type=%s)",
        project_id,
        passed,
        total,
        project_type or "all",
    )
    return results


def _evaluate_compliance(
    code: str,
    standard: dict,
    project_data: dict,
) -> dict:
    """Evaluate a single compliance standard."""
    category = standard.get("category", "general")
    safety_measures = project_data.get("safety_measures", [])
    active_zones = project_data.get("active_zones", [])

    findings: list[str] = []
    status = "pass"

    # ----- OSHA Category Checks -----

    if category == "fall_protection":
        has_fall_protection = any(m.get("type") == "fall_protection" for m in safety_measures)
        if not has_fall_protection and active_zones:
            status = "warning"
            findings.append("Fall protection measures not documented")
        has_training = any(m.get("type") == "fall_protection_training" for m in safety_measures)
        if code == "1926.503" and not has_training:
            findings.append("Fall protection training records not found")
            if status == "pass":
                status = "info"

    elif category == "scaffolding":
        has_scaffold_plan = any(m.get("type") == "scaffold_safety" for m in safety_measures)
        if not has_scaffold_plan:
            status = "info"
            findings.append("Scaffold safety plan or competent person not documented")
        if code == "1926.454":
            has_scaffold_training = any(
                m.get("type") == "scaffold_training" for m in safety_measures
            )
            if not has_scaffold_training:
                findings.append("Scaffold user training records not found")
                if status == "pass":
                    status = "info"

    elif category == "excavation":
        has_excavation_plan = any(m.get("type") == "excavation_safety" for m in safety_measures)
        if not has_excavation_plan:
            status = "info"
            findings.append("Excavation safety plan not yet submitted")
        if code == "1926.652":
            has_soil_class = any(m.get("type") == "soil_classification" for m in safety_measures)
            if not has_soil_class:
                findings.append("Soil classification not documented for protective system design")

    elif category == "ppe":
        has_ppe_policy = any(m.get("type") == "ppe" for m in safety_measures)
        if not has_ppe_policy:
            status = "warning"
            findings.append("PPE policy not documented")
        if code == "1926.103":
            has_respiratory = any(m.get("type") == "respiratory_program" for m in safety_measures)
            if not has_respiratory:
                findings.append("Respiratory protection program not documented")
                if status == "pass":
                    status = "info"

    elif category == "general_safety":
        if code == "1926.20":
            has_safety_program = any(
                m.get("type") in ("safety_program", "safety_plan") for m in safety_measures
            )
            if not has_safety_program:
                status = "warning"
                findings.append("General safety and health program not documented")
        elif code == "1926.21":
            has_training = any(m.get("type") == "safety_training" for m in safety_measures)
            if not has_training:
                status = "info"
                findings.append("Safety training program records not found")
        elif code == "1926.23":
            has_first_aid = any(m.get("type") == "first_aid" for m in safety_measures)
            if not has_first_aid:
                status = "info"
                findings.append("First aid provisions not documented")
        elif code == "1926.25":
            has_housekeeping = any(m.get("type") == "housekeeping" for m in safety_measures)
            if not has_housekeeping:
                findings.append("Housekeeping plan not documented")
                if status == "pass":
                    status = "info"
        elif code == "1926.35":
            has_eap = any(m.get("type") == "emergency_action_plan" for m in safety_measures)
            if not has_eap:
                status = "warning"
                findings.append("Employee emergency action plan not documented")

    elif category == "fire_protection":
        has_fire_plan = any(
            m.get("type") in ("fire_protection", "fire_prevention") for m in safety_measures
        )
        if not has_fire_plan:
            status = "info"
            findings.append("Fire protection/prevention plan not documented")
        if code in ("1926.155", "1926.150"):
            has_extinguisher = any(m.get("type") == "fire_extinguisher" for m in safety_measures)
            if not has_extinguisher:
                findings.append("Fire extinguisher placement plan not verified")

    elif category == "signs_signals":
        has_signage = any(
            m.get("type") in ("signs", "barricades", "signage") for m in safety_measures
        )
        if not has_signage and active_zones:
            status = "info"
            findings.append("Accident prevention signs/barricades not documented")

    elif category == "materials_handling":
        has_rigging = any(
            m.get("type") in ("rigging", "material_handling") for m in safety_measures
        )
        if not has_rigging:
            status = "info"
            findings.append("Rigging/material handling plan not documented")

    elif category == "tools":
        has_tool_safety = any(
            m.get("type") in ("tool_safety", "power_tools") for m in safety_measures
        )
        if not has_tool_safety:
            status = "info"
            findings.append("Tool safety program not documented")

    elif category == "welding":
        has_welding = any(m.get("type") in ("welding_safety", "hot_work") for m in safety_measures)
        if not has_welding:
            status = "info"
            findings.append("Welding/cutting safety plan not documented")
        if code == "1926.352":
            has_fire_watch = any(m.get("type") == "fire_watch" for m in safety_measures)
            if not has_fire_watch:
                findings.append("Fire watch procedures for welding not documented")

    elif category == "electrical":
        has_electrical = any(
            m.get("type") in ("electrical_safety", "gfci", "lockout_tagout")
            for m in safety_measures
        )
        if not has_electrical:
            status = "info"
            findings.append("Electrical safety program not documented")
        if code == "1926.417":
            has_loto = any(m.get("type") == "lockout_tagout" for m in safety_measures)
            if not has_loto:
                findings.append("Lockout/tagout program not documented")
                if status == "pass":
                    status = "warning"

    elif category == "cranes":
        has_crane_plan = any(
            m.get("type") in ("crane_safety", "crane_inspection", "lift_plan")
            for m in safety_measures
        )
        if not has_crane_plan:
            status = "info"
            findings.append("Crane/hoist safety plan or inspection records not documented")

    elif category == "concrete_masonry":
        has_concrete_plan = any(
            m.get("type") in ("concrete_safety", "formwork_safety") for m in safety_measures
        )
        if not has_concrete_plan:
            status = "info"
            findings.append("Concrete/masonry safety plan not documented")
        if code == "1926.703":
            has_formwork = any(m.get("type") == "formwork_safety" for m in safety_measures)
            if not has_formwork:
                findings.append("Cast-in-place concrete formwork plan not documented")

    elif category == "steel_erection":
        has_steel_plan = any(
            m.get("type") in ("steel_erection", "erection_plan") for m in safety_measures
        )
        if not has_steel_plan:
            status = "info"
            findings.append("Steel erection plan not documented")
        if code == "1926.752":
            has_site_plan = any(m.get("type") == "site_erection_plan" for m in safety_measures)
            if not has_site_plan:
                findings.append("Site-specific erection plan not documented")

    elif category == "demolition":
        has_demo_plan = any(
            m.get("type") in ("demolition_plan", "demolition_safety") for m in safety_measures
        )
        if not has_demo_plan:
            status = "info"
            findings.append("Demolition plan not documented")
        if code == "1926.850":
            has_engineering_survey = any(
                m.get("type") == "engineering_survey" for m in safety_measures
            )
            if not has_engineering_survey:
                findings.append("Engineering survey prior to demolition not documented")

    elif category == "stairways_ladders":
        has_ladder_safety = any(
            m.get("type") in ("ladder_safety", "stairway_safety") for m in safety_measures
        )
        if not has_ladder_safety:
            status = "info"
            findings.append("Stairway/ladder safety plan not documented")

    elif category == "confined_spaces":
        has_confined = any(
            m.get("type") in ("confined_space", "permit_confined_space") for m in safety_measures
        )
        if not has_confined:
            status = "info"
            findings.append("Confined space program not documented")
        if code == "1926.1204":
            has_permit = any(m.get("type") == "permit_confined_space" for m in safety_measures)
            if not has_permit:
                findings.append("Permit-required confined space program not documented")
        if code == "1926.1211":
            has_rescue = any(m.get("type") == "confined_space_rescue" for m in safety_measures)
            if not has_rescue:
                findings.append("Confined space rescue plan not documented")

    # ----- IBC Category Checks -----

    elif category == "ibc_occupancy":
        occupancy = project_data.get("occupancy_type")
        if code == "IBC-302":
            if not occupancy:
                status = "warning"
                findings.append("Occupancy classification not specified in project data")
        elif code == "IBC-1004":
            occupant_load = project_data.get("occupant_load")
            if occupant_load is None:
                status = "info"
                findings.append("Occupant load calculation not provided")

    elif category == "ibc_egress":
        if code == "IBC-1006":
            num_exits = project_data.get("num_exits")
            occupant_load = project_data.get("occupant_load", 0)
            if num_exits is not None:
                # IBC requires 2 exits when occupant load > 49
                required = 2 if occupant_load > 49 else 1
                if occupant_load > 500:
                    required = 3
                if occupant_load > 1000:
                    required = 4
                if num_exits < required:
                    status = "fail"
                    findings.append(
                        f"Insufficient exits: {num_exits} provided, "
                        f"{required} required for occupant load {occupant_load}"
                    )
            else:
                status = "info"
                findings.append("Number of exits not specified")
        elif code == "IBC-1020":
            travel_distance = project_data.get("exit_travel_distance")
            has_sprinkler = project_data.get("has_sprinkler", False)
            if travel_distance is not None:
                max_distance = 300 if has_sprinkler else 200
                if travel_distance > max_distance:
                    status = "fail"
                    findings.append(
                        f"Exit travel distance {travel_distance} ft exceeds "
                        f"maximum {max_distance} ft "
                        f"({'sprinklered' if has_sprinkler else 'non-sprinklered'})"
                    )
            else:
                status = "info"
                findings.append("Exit travel distance not specified")
        elif code in ("IBC-1005", "IBC-1009"):
            status = "info"
            findings.append(f"Manual review needed for {standard['title']}")

    elif category == "ibc_fire_resistance":
        if code == "IBC-602":
            construction_type = project_data.get("construction_type")
            fire_rating = project_data.get("fire_resistance_rating")
            if not construction_type:
                status = "info"
                findings.append("Construction type not specified")
            elif fire_rating is not None:
                # Basic check: Type I requires 2-3 hr, Type II 1-2 hr, etc.
                min_ratings = {
                    "IA": 3.0,
                    "IB": 2.0,
                    "IIA": 1.0,
                    "IIB": 0.0,
                    "IIIA": 1.0,
                    "IIIB": 0.0,
                    "IV": 1.0,  # Heavy Timber
                    "VA": 1.0,
                    "VB": 0.0,
                }
                min_required = min_ratings.get(construction_type, 0.0)
                if fire_rating < min_required:
                    status = "fail"
                    findings.append(
                        f"Fire-resistance rating {fire_rating} hr is below "
                        f"minimum {min_required} hr for Type {construction_type}"
                    )
        elif code == "IBC-903":
            has_sprinkler = project_data.get("has_sprinkler", False)
            building_area = project_data.get("building_area", 0)
            num_stories = project_data.get("num_stories", 1)
            if not has_sprinkler:
                # Simplified: NFPA 13 required for buildings > 12,000 sq ft
                # or > 3 stories in most occupancies
                if building_area > 12000 or num_stories > 3:
                    status = "warning"
                    findings.append(
                        f"Automatic sprinkler system may be required "
                        f"(area: {building_area} sq ft, stories: {num_stories})"
                    )
        elif code in ("IBC-603", "IBC-704"):
            status = "info"
            findings.append(f"Manual review needed for {standard['title']}")

    return {
        "status": status,
        "result": (
            f"Compliant with {standard['title']}"
            if status == "pass"
            else f"Review needed for {standard['title']}"
        ),
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# OSHA enforcement data context (for compliance briefings)
# ---------------------------------------------------------------------------

# Map project types to NAICS prefixes for OSHA data filtering
_NAICS_BY_PROJECT_TYPE: dict[str, str] = {
    "residential": "2361",
    "commercial": "2362",
    "infrastructure": "237",
}


async def get_osha_violation_context(
    db: AsyncSession,
    state: str,
    project_type: str | None = None,
    top_n: int = 5,
) -> dict:
    """Return top cited OSHA violations in a state for a project type.

    Intended as contextual enrichment for compliance briefings, not
    as a compliance check itself.

    Parameters
    ----------
    db: Async database session
    state: Two-letter state abbreviation (e.g. "VA")
    project_type: Optional "residential", "commercial", "infrastructure"
    top_n: Number of top violations to return

    Returns
    -------
    Dict with state, project_type, context_message, and top_violations list.
    """
    from app.services.safety.osha_lookup import get_violation_stats

    naics_prefix = _NAICS_BY_PROJECT_TYPE.get(project_type or "")

    stats = await get_violation_stats(
        db,
        state=state,
        naics_prefix=naics_prefix,
        since_years=5,
    )

    top = stats["top_standards"][:top_n]

    # Enrich with titles from the existing OSHA_STANDARDS dict
    for item in top:
        std = OSHA_STANDARDS.get(item["standard"], {})
        item["title"] = std.get("title")
        item["category"] = std.get("category")

    standards_str = ", ".join(t["standard"] for t in top) if top else "none"
    context_message = (
        f"Top {len(top)} cited OSHA violations in {state}"
        + (f" for {project_type} projects" if project_type else "")
        + f": {standards_str}."
    )

    return {
        "state": state,
        "project_type": project_type,
        "context_message": context_message,
        "top_violations": top,
    }
