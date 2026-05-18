"""Regional cost factor lookup with nearest-metro fallback.

Provides metro-level material, labor, and equipment cost multipliers
relative to the national average. Supports lookup by city/state, zip
prefix, or lat/lon with haversine nearest-metro fallback.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

_SEED_FILE = Path(__file__).resolve().parents[3] / "data" / "seed" / "regional_factors_v1.json"

# Weights for composite factor calculation
_WEIGHT_LABOR = 0.40
_WEIGHT_MATERIAL = 0.45
_WEIGHT_EQUIPMENT = 0.15

# Maximum distance (km) for nearest-metro fallback before warning
_MAX_FALLBACK_DISTANCE_KM = 200


@dataclass(frozen=True, slots=True)
class RegionalFactor:
    """Immutable regional cost factor for a metro area."""

    city: str
    state: str
    state_abbr: str
    zip_prefix: str
    latitude: float
    longitude: float
    material_factor: float
    labor_factor: float
    equipment_factor: float
    composite_factor: float


@dataclass(frozen=True, slots=True)
class AppliedRegionalFactor:
    """Result of applying a regional factor to a cost, with transparency."""

    metro: str
    state_abbr: str
    material_factor: float
    labor_factor: float
    equipment_factor: float
    composite_factor: float
    distance_km: float | None  # None if exact match, else km to nearest metro
    is_fallback: bool
    warning: str | None  # Warning message if fallback used


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM = 6371.0


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points in km."""
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return _EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# In-memory metro cache (loaded once from seed JSON or DB)
# ---------------------------------------------------------------------------

_metro_cache: list[RegionalFactor] | None = None
_city_state_index: dict[tuple[str, str], RegionalFactor] | None = None
_zip_index: dict[str, RegionalFactor] | None = None
_state_index: dict[str, list[RegionalFactor]] | None = None
_cache_init_lock = threading.Lock()


def _load_seed_data() -> list[RegionalFactor]:
    """Load regional factors from the seed JSON file."""
    if not _SEED_FILE.exists():
        logger.warning("Regional factors seed file not found: %s", _SEED_FILE)
        return []

    with open(_SEED_FILE) as f:
        raw = json.load(f)

    # Support both flat list and {_metadata, metros} wrapper formats
    if isinstance(raw, dict) and "metros" in raw:
        entries = raw["metros"]
    elif isinstance(raw, list):
        entries = raw
    else:
        logger.warning("Unexpected seed JSON format")
        return []

    factors = []
    for entry in entries:
        factors.append(
            RegionalFactor(
                city=entry["city"],
                state=entry["state"],
                state_abbr=entry["state_abbr"],
                zip_prefix=entry.get("zip_prefix", ""),
                latitude=float(entry.get("latitude", 0)),
                longitude=float(entry.get("longitude", 0)),
                material_factor=float(entry["material_factor"]),
                labor_factor=float(entry["labor_factor"]),
                equipment_factor=float(entry["equipment_factor"]),
                composite_factor=float(entry["composite_factor"]),
            )
        )

    logger.info("Loaded %d regional factors from seed file", len(factors))
    return factors


def _ensure_loaded() -> None:
    """Ensure the metro cache and indexes are populated."""
    global _metro_cache, _city_state_index, _zip_index, _state_index

    if _metro_cache is not None:
        return

    with _cache_init_lock:
        # Double-checked locking: re-check after acquiring lock
        if _metro_cache is not None:
            return

        loaded = _load_seed_data()

        city_state_idx: dict[tuple[str, str], RegionalFactor] = {}
        zip_idx: dict[str, RegionalFactor] = {}
        state_idx: dict[str, list[RegionalFactor]] = {}

        for f in loaded:
            key = (f.city.lower(), f.state_abbr.lower())
            city_state_idx[key] = f

            if f.zip_prefix:
                zip_idx[f.zip_prefix] = f

            st = f.state_abbr.lower()
            if st not in state_idx:
                state_idx[st] = []
            state_idx[st].append(f)

        # Assign indexes before cache to avoid partial visibility
        _city_state_index = city_state_idx
        _zip_index = zip_idx
        _state_index = state_idx
        _metro_cache = loaded


async def load_from_db(db: AsyncSession) -> list[RegionalFactor]:
    """Load regional factors from database, replacing the seed cache."""
    global _metro_cache, _city_state_index, _zip_index, _state_index

    from sqlalchemy import select

    from app.models.regional_cost_factor import RegionalCostFactor

    result = await db.execute(select(RegionalCostFactor))
    rows = result.scalars().all()

    if not rows:
        logger.info("No regional factors in DB, using seed data")
        _ensure_loaded()
        return _metro_cache or []

    factors = []
    for row in rows:
        factors.append(
            RegionalFactor(
                city=row.city,
                state=row.state,
                state_abbr=row.state_abbr,
                zip_prefix=row.zip_prefix or "",
                latitude=float(row.latitude) if row.latitude else 0.0,
                longitude=float(row.longitude) if row.longitude else 0.0,
                material_factor=float(row.material_factor),
                labor_factor=float(row.labor_factor),
                equipment_factor=float(row.equipment_factor),
                composite_factor=float(row.composite_factor),
            )
        )

    _metro_cache = factors
    _city_state_index = {}
    _zip_index = {}
    _state_index = {}

    for f in factors:
        _city_state_index[(f.city.lower(), f.state_abbr.lower())] = f
        if f.zip_prefix:
            _zip_index[f.zip_prefix] = f
        st = f.state_abbr.lower()
        if st not in _state_index:
            _state_index[st] = []
        _state_index[st].append(f)

    logger.info("Loaded %d regional factors from database", len(factors))
    return factors


def clear_cache() -> None:
    """Clear the in-memory regional factor cache (for testing)."""
    global _metro_cache, _city_state_index, _zip_index, _state_index
    _metro_cache = None
    _city_state_index = None
    _zip_index = None
    _state_index = None


# ---------------------------------------------------------------------------
# Lookup functions
# ---------------------------------------------------------------------------


def lookup_by_city_state(city: str, state_abbr: str) -> RegionalFactor | None:
    """Exact lookup by city name and state abbreviation."""
    _ensure_loaded()
    assert _city_state_index is not None
    return _city_state_index.get((city.lower(), state_abbr.lower()))


def lookup_by_zip(zip_code: str) -> RegionalFactor | None:
    """Lookup by zip code (matches on 3-digit prefix)."""
    _ensure_loaded()
    assert _zip_index is not None
    prefix = zip_code.strip()[:3]
    return _zip_index.get(prefix)


def lookup_by_state(state_abbr: str) -> list[RegionalFactor]:
    """Get all metros in a state."""
    _ensure_loaded()
    assert _state_index is not None
    return _state_index.get(state_abbr.lower(), [])


def find_nearest_metro(latitude: float, longitude: float) -> tuple[RegionalFactor | None, float]:
    """Find nearest metro by lat/lon using haversine distance.

    Returns (factor, distance_km). Returns (None, inf) if no metros loaded.
    """
    _ensure_loaded()
    assert _metro_cache is not None

    if not _metro_cache:
        return None, float("inf")

    best: RegionalFactor | None = None
    best_dist = float("inf")

    for f in _metro_cache:
        if f.latitude == 0.0 and f.longitude == 0.0:
            continue
        dist = _haversine(latitude, longitude, f.latitude, f.longitude)
        if dist < best_dist:
            best_dist = dist
            best = f

    return best, best_dist


# ---------------------------------------------------------------------------
# Unified lookup with fallback chain
# ---------------------------------------------------------------------------


def get_regional_factor(
    *,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> AppliedRegionalFactor:
    """Look up regional cost factor with cascading fallback.

    Lookup order:
    1. Exact city + state match
    2. Zip prefix match
    3. State average (if multiple metros in state, average their factors)
    4. Nearest metro by lat/lon (haversine)
    5. National average (all factors = 1.0)

    Returns AppliedRegionalFactor with transparency about which method was used.
    """
    _ensure_loaded()

    # 1. City + state exact match
    if city and state:
        factor = lookup_by_city_state(city, state)
        if factor:
            return AppliedRegionalFactor(
                metro=factor.city,
                state_abbr=factor.state_abbr,
                material_factor=factor.material_factor,
                labor_factor=factor.labor_factor,
                equipment_factor=factor.equipment_factor,
                composite_factor=factor.composite_factor,
                distance_km=None,
                is_fallback=False,
                warning=None,
            )

    # 2. Zip prefix match
    if zip_code:
        factor = lookup_by_zip(zip_code)
        if factor:
            return AppliedRegionalFactor(
                metro=factor.city,
                state_abbr=factor.state_abbr,
                material_factor=factor.material_factor,
                labor_factor=factor.labor_factor,
                equipment_factor=factor.equipment_factor,
                composite_factor=factor.composite_factor,
                distance_km=None,
                is_fallback=False,
                warning=None,
            )

    # 3. State average
    if state:
        state_metros = lookup_by_state(state)
        if state_metros:
            avg_mat = sum(f.material_factor for f in state_metros) / len(state_metros)
            avg_lab = sum(f.labor_factor for f in state_metros) / len(state_metros)
            avg_eq = sum(f.equipment_factor for f in state_metros) / len(state_metros)
            avg_comp = (
                _WEIGHT_LABOR * avg_lab + _WEIGHT_MATERIAL * avg_mat + _WEIGHT_EQUIPMENT * avg_eq
            )
            return AppliedRegionalFactor(
                metro=f"{state} state average",
                state_abbr=state.upper(),
                material_factor=round(avg_mat, 4),
                labor_factor=round(avg_lab, 4),
                equipment_factor=round(avg_eq, 4),
                composite_factor=round(avg_comp, 4),
                distance_km=None,
                is_fallback=True,
                warning=f"No exact metro match; using average of {len(state_metros)} metro(s) in {state.upper()}",
            )

    # 4. Nearest metro by lat/lon
    if latitude is not None and longitude is not None:
        factor, dist = find_nearest_metro(latitude, longitude)
        if factor:
            warning = None
            if dist > _MAX_FALLBACK_DISTANCE_KM:
                warning = (
                    f"Nearest metro is {factor.city}, {factor.state_abbr} "
                    f"({dist:.0f} km away). Factors may not be accurate."
                )
            return AppliedRegionalFactor(
                metro=factor.city,
                state_abbr=factor.state_abbr,
                material_factor=factor.material_factor,
                labor_factor=factor.labor_factor,
                equipment_factor=factor.equipment_factor,
                composite_factor=factor.composite_factor,
                distance_km=round(dist, 1),
                is_fallback=True,
                warning=warning,
            )

    # 5. National average fallback
    return AppliedRegionalFactor(
        metro="National Average",
        state_abbr="US",
        material_factor=1.0,
        labor_factor=1.0,
        equipment_factor=1.0,
        composite_factor=1.0,
        distance_km=None,
        is_fallback=True,
        warning="No location provided; using national average factors (1.0)",
    )


# ---------------------------------------------------------------------------
# Cost adjustment functions
# ---------------------------------------------------------------------------


def apply_factor_to_cost(
    base_cost: float,
    factor: AppliedRegionalFactor,
) -> dict:
    """Apply composite regional factor to a single cost value.

    Returns dict with adjusted_cost and factor details.
    """
    adjusted = round(base_cost * factor.composite_factor, 2)
    return {
        "base_cost": base_cost,
        "adjusted_cost": adjusted,
        "regional_factor": factor.composite_factor,
        "metro": factor.metro,
        "is_fallback": factor.is_fallback,
        "warning": factor.warning,
    }


def apply_factor_to_breakdown(
    material_cost: float,
    labor_cost: float,
    equipment_cost: float,
    factor: AppliedRegionalFactor,
) -> dict:
    """Apply per-component regional factors to a cost breakdown.

    Applies material_factor to material, labor_factor to labor,
    equipment_factor to equipment for accurate adjustment.
    """
    adj_material = round(material_cost * factor.material_factor, 2)
    adj_labor = round(labor_cost * factor.labor_factor, 2)
    adj_equipment = round(equipment_cost * factor.equipment_factor, 2)
    adj_total = round(adj_material + adj_labor + adj_equipment, 2)

    return {
        "material_cost": adj_material,
        "labor_cost": adj_labor,
        "equipment_cost": adj_equipment,
        "total_adjusted_cost": adj_total,
        "factors_applied": {
            "material": factor.material_factor,
            "labor": factor.labor_factor,
            "equipment": factor.equipment_factor,
        },
        "metro": factor.metro,
        "state_abbr": factor.state_abbr,
        "is_fallback": factor.is_fallback,
        "distance_km": factor.distance_km,
        "warning": factor.warning,
    }
