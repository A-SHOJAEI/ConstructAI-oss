"""Industry benchmark data for construction project controls.

Sources: AACE International, CMAA, ENR historical analysis, Dodge Analytics.
All values represent industry-wide statistical summaries.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# CPI (Cost Performance Index) benchmarks by project type
# ---------------------------------------------------------------------------

CPI_BENCHMARKS: dict[str, dict[str, float]] = {
    "commercial": {"mean": 0.92, "std": 0.15, "p10": 0.72, "p50": 0.93, "p90": 1.05},
    "infrastructure": {"mean": 0.85, "std": 0.18, "p10": 0.62, "p50": 0.86, "p90": 1.02},
    "residential": {"mean": 0.95, "std": 0.10, "p10": 0.82, "p50": 0.96, "p90": 1.06},
    "institutional": {"mean": 0.88, "std": 0.14, "p10": 0.70, "p50": 0.89, "p90": 1.03},
    "healthcare": {"mean": 0.83, "std": 0.16, "p10": 0.62, "p50": 0.84, "p90": 0.99},
    "industrial": {"mean": 0.90, "std": 0.13, "p10": 0.73, "p50": 0.91, "p90": 1.04},
}

# ---------------------------------------------------------------------------
# Duration uncertainty by activity category
# Expressed as percentage offsets from the most-likely duration:
#   optimistic = duration * (1 + optimistic_pct)
#   pessimistic = duration * (1 + pessimistic_pct)
# ---------------------------------------------------------------------------

DURATION_UNCERTAINTY: dict[str, dict[str, float]] = {
    "site_work": {"optimistic_pct": -0.15, "pessimistic_pct": 0.50},
    "foundations": {"optimistic_pct": -0.10, "pessimistic_pct": 0.30},
    "structural_steel": {"optimistic_pct": -0.05, "pessimistic_pct": 0.35},
    "concrete_structure": {"optimistic_pct": -0.08, "pessimistic_pct": 0.30},
    "mep_rough_in": {"optimistic_pct": -0.08, "pessimistic_pct": 0.40},
    "building_enclosure": {"optimistic_pct": -0.05, "pessimistic_pct": 0.25},
    "interior_finishes": {"optimistic_pct": -0.05, "pessimistic_pct": 0.20},
    "commissioning": {"optimistic_pct": -0.05, "pessimistic_pct": 0.15},
    "default": {"optimistic_pct": -0.20, "pessimistic_pct": 0.20},
}

# ---------------------------------------------------------------------------
# Change order rates by project type (as fraction of contract value)
# ---------------------------------------------------------------------------

CHANGE_ORDER_RATES: dict[str, dict[str, float]] = {
    "commercial": {"mean_pct": 0.10, "std_pct": 0.04},
    "infrastructure": {"mean_pct": 0.15, "std_pct": 0.06},
    "residential": {"mean_pct": 0.065, "std_pct": 0.03},
    "institutional": {"mean_pct": 0.125, "std_pct": 0.05},
    "healthcare": {"mean_pct": 0.175, "std_pct": 0.07},
}

# ---------------------------------------------------------------------------
# Activity classification — keyword matching
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "site_work": [
        "site",
        "excavat",
        "grading",
        "demoli",
        "clearing",
        "earthwork",
        "erosion",
        "landscap",
        "paving",
        "asphalt",
        "curb",
        "gutter",
        "storm",
        "drain",
        "utility",
        "mobiliz",
    ],
    "foundations": [
        "foundation",
        "footing",
        "pile",
        "caisson",
        "slab on grade",
        "mat slab",
        "grade beam",
        "retaining wall",
        "shoring",
        "underpinning",
        "soil",
    ],
    "structural_steel": [
        "structural steel",
        "steel erect",
        "steel fram",
        "iron work",
        "metal deck",
        "bar joist",
        "steel column",
        "steel beam",
    ],
    "concrete_structure": [
        "concrete",
        "formwork",
        "rebar",
        "reinforc",
        "pour",
        "cast",
        "precast",
        "tilt-up",
        "post-tension",
        "masonry",
        "block",
        "brick",
        "cmu",
    ],
    "mep_rough_in": [
        "mechanical",
        "electrical",
        "plumbing",
        "hvac",
        "ductwork",
        "conduit",
        "piping",
        "sprinkler",
        "fire protect",
        "fire alarm",
        "low voltage",
        "rough-in",
        "rough in",
        "mep",
    ],
    "building_enclosure": [
        "enclosure",
        "envelope",
        "curtain wall",
        "glazing",
        "window",
        "exterior wall",
        "roofing",
        "waterproof",
        "insulation",
        "cladding",
        "siding",
        "exterior finish",
    ],
    "interior_finishes": [
        "finish",
        "drywall",
        "paint",
        "floor",
        "tile",
        "carpet",
        "ceiling",
        "millwork",
        "cabinet",
        "casework",
        "trim",
        "interior",
        "partition",
        "door",
        "hardware",
    ],
    "commissioning": [
        "commission",
        "startup",
        "start-up",
        "testing",
        "balancing",
        "tab",
        "punch",
        "closeout",
        "close-out",
        "handover",
        "substantial completion",
        "final clean",
    ],
}


def classify_activity(
    name: str,
    wbs_code: str | None = None,
) -> str:
    """Classify an activity into a DURATION_UNCERTAINTY category.

    Matches against activity name and optional WBS code using keyword
    matching.  Returns one of the DURATION_UNCERTAINTY keys.
    """
    text = (name or "").lower()
    if wbs_code:
        text += " " + wbs_code.lower()

    for category, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return category

    return "default"


def get_duration_bounds(
    duration_days: int | float,
    category: str | None = None,
    name: str | None = None,
    wbs_code: str | None = None,
) -> tuple[float, float]:
    """Return (optimistic, pessimistic) durations for an activity.

    If *category* is not provided, classifies by name/wbs_code.
    """
    if category is None:
        category = classify_activity(name or "", wbs_code)

    unc = DURATION_UNCERTAINTY.get(category, DURATION_UNCERTAINTY["default"])
    optimistic = max(1, duration_days * (1.0 + unc["optimistic_pct"]))
    pessimistic = duration_days * (1.0 + unc["pessimistic_pct"])
    return optimistic, pessimistic
