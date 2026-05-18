#!/usr/bin/env python3
"""Ingest DDC CWICR data into the ConstructAI cost_items table.

Reads the DDC CWICR parquet file, maps collections to CSI MasterFormat
divisions, deduplicates by rate_code, and inserts 2,500+ cost items with
separate material/labor/equipment cost breakdowns.

Usage:
    python -m scripts.ingest_ddc_cwicr [--parquet PATH] [--dry-run] [--limit N]

Requires: pandas, pyarrow, sqlalchemy[asyncio], asyncpg
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDC Collection -> CSI MasterFormat mapping
# ---------------------------------------------------------------------------
# Maps DDC CWICR collection_name to (csi_division, csi_code, category)
# csi_code uses the "XX XX 00" 6-digit level-2 format

COLLECTION_TO_CSI: dict[str, tuple[str, str, str]] = {
    # Division 02 - Existing Conditions
    "Demolition works": ("02", "02 41 00", "demolition"),
    "Wrecking (demolishing) buildings and structures": ("02", "02 41 00", "demolition"),
    # Division 03 - Concrete
    "Monolithic concrete and reinforced concrete structures": ("03", "03 30 00", "concrete"),
    "Precast concrete structures": ("03", "03 40 00", "precast_concrete"),
    "Concrete and reinforced concrete precast structures": ("03", "03 40 00", "precast_concrete"),
    "Concrete preparations": ("03", "03 05 00", "concrete_prep"),
    # Division 03 - Concrete (additional)
    "Foundations": ("03", "03 11 00", "concrete_foundation"),
    "Concrete and reinforced concrete structures of hydraulic engineering structures": (
        "03",
        "03 30 00",
        "concrete_hydro",
    ),
    "Precast concrete and reinforced concrete structures": ("03", "03 40 00", "precast_concrete"),
    # Division 04 - Masonry
    "Brickwork": ("04", "04 21 00", "masonry_brick"),
    "Masonry works": ("04", "04 20 00", "masonry"),
    "Brick and block structures": ("04", "04 21 00", "masonry_block"),
    "Walls": ("04", "04 21 00", "masonry_walls"),
    "Stone structures of hydraulic engineering structures": ("04", "04 43 00", "stone_masonry"),
    "Stove work": ("04", "04 57 00", "stove_masonry"),
    # Division 05 - Metals
    "Steel structures": ("05", "05 12 00", "structural_steel"),
    "Metal structures": ("05", "05 50 00", "misc_metals"),
    "Welding works": ("05", "05 05 23", "welding"),
    "Metal building structures": ("05", "05 12 00", "metal_building_structures"),
    "Metal structures of hydraulic engineering structures": (
        "05",
        "05 50 00",
        "metal_hydro_structures",
    ),
    "Inspection of welded joints": ("05", "05 05 23", "weld_inspection"),
    # Division 06 - Wood/Plastics/Composites
    "Wooden structures": ("06", "06 10 00", "rough_carpentry"),
    "Carpentry works": ("06", "06 20 00", "finish_carpentry"),
    "Wooden structures of hydraulic engineering structures": ("06", "06 10 00", "wood_hydro"),
    "Partitions": ("06", "06 16 00", "partitions"),
    "Stairs, porches": ("06", "06 43 00", "stairs"),
    # Division 07 - Thermal/Moisture Protection
    "Roofs": ("07", "07 50 00", "roofing"),
    "Insulation work": ("07", "07 21 00", "insulation"),
    "Thermal insulation works": ("07", "07 21 00", "thermal_insulation"),
    "Waterproofing and vapor barrier insulation": ("07", "07 10 00", "waterproofing"),
    "Waterproofing works in hydraulic structures": ("07", "07 10 00", "waterproofing_hydro"),
    "Anti-corrosion protection of steel structures": ("07", "07 10 00", "corrosion_protection"),
    "Anti-corrosion coatings": ("07", "07 10 00", "corrosion_protection"),
    "Protection of building structures and equipment from corrosion": (
        "07",
        "07 10 00",
        "corrosion_protection_bldg",
    ),
    # Division 08 - Openings
    "Glazing works": ("08", "08 80 00", "glazing"),
    "Window and door installations": ("08", "08 50 00", "windows_doors"),
    "Openings": ("08", "08 00 00", "openings"),
    "Glazing, wallpapering, and tiling work": ("08", "08 80 00", "glazing_misc"),
    # Division 09 - Finishes
    "Finishing works": ("09", "09 00 00", "finishes"),
    "Plastering work": ("09", "09 24 00", "plastering"),
    "Stucco work": ("09", "09 24 00", "stucco"),
    "Painting works": ("09", "09 91 00", "painting"),
    "Painting work": ("09", "09 91 00", "painting_work"),
    "Wallpapering works": ("09", "09 72 00", "wall_coverings"),
    "Floor works": ("09", "09 60 00", "flooring"),
    "Floors": ("09", "09 60 00", "floor_systems"),
    # Division 21 - Fire Suppression
    "Fire protection installations": ("21", "21 13 00", "fire_sprinkler"),
    # Division 22 - Plumbing
    "Internal plumbing and sewage": ("22", "22 05 00", "plumbing"),
    "Water supply - internal networks": ("22", "22 11 00", "water_supply"),
    "Sewage - internal networks": ("22", "22 13 00", "sewage"),
    "Internal sanitary and technical work": ("22", "22 05 00", "sanitary"),
    "Internal piping": ("22", "22 11 00", "internal_piping"),
    "Water supply and sewerage - internal installations": ("22", "22 05 00", "plumbing_internal"),
    "Gas supply - internal devices": ("22", "22 11 00", "gas_internal"),
    "Inspection of pipeline fittings": ("22", "22 05 00", "pipe_inspection"),
    # Division 23 - HVAC
    "Ventilation and air conditioning": ("23", "23 00 00", "hvac"),
    "Ventilation and air conditioning systems": ("23", "23 00 00", "hvac_systems"),
    "Heating - internal networks": ("23", "23 21 00", "heating"),
    "Heating - internal devices": ("23", "23 21 00", "heating_devices"),
    "Heat supply and gas pipelines - outdoor networks": ("23", "23 21 00", "heat_supply"),
    # Division 26 - Electrical
    "Electrical installations": ("26", "26 05 00", "electrical"),
    "Electrical equipment": ("26", "26 00 00", "electrical_equipment"),
    "Electrical installation work": ("26", "26 05 00", "electrical_install"),
    "Electrical lighting - internal networks": ("26", "26 51 00", "lighting"),
    "Power devices installation": ("26", "26 24 00", "power_devices"),
    "Power lines": ("26", "26 56 00", "power_lines"),
    "Electrical technology industry equipment": ("26", "26 00 00", "electrical_tech"),
    # Division 27 - Communications
    "Communication equipment": ("27", "27 00 00", "communications"),
    "Automation systems and instrumentation": ("27", "27 51 00", "automation"),
    "Communication lines": ("27", "27 13 00", "comm_lines"),
    "Signaling, centralization and blocking on railways": ("27", "27 51 00", "rail_signaling"),
    "Communication, radio broadcasting, and television facilities": ("27", "27 00 00", "broadcast"),
    "Automated control systems": ("27", "27 51 00", "control_systems"),
    "Devices, automation and computing equipment": ("27", "27 51 00", "automation_devices"),
    "Signaling, centralization, interlocking, and contact network equipment for railway transport": (
        "27",
        "27 51 00",
        "rail_signaling_2",
    ),
    "Automation and telemechanics devices for railway transport": (
        "27",
        "27 51 00",
        "rail_telemechanics",
    ),
    # Division 28 - Electronic Safety/Security
    "Fire and security alarm systems": ("28", "28 31 00", "fire_alarm"),
    # Division 31 - Earthwork
    "Earthworks": ("31", "31 23 00", "earthwork"),
    "Soil development by excavators": ("31", "31 23 16", "excavation"),
    "Pile driving, sinking wells, soil stabilization": ("31", "31 62 00", "piling"),
    "Dredging and hydraulic reclamation works": ("31", "31 25 00", "dredging"),
    "Reclamation works": ("31", "31 25 00", "reclamation"),
    "Shore strengthening and bank protection works": ("31", "31 37 00", "shore_protection"),
    "Earthworks for hydraulic structures": ("31", "31 23 00", "earthwork_hydro"),
    "Drilling and blasting": ("31", "31 23 00", "drilling_blasting"),
    "Shore reinforcement works": ("31", "31 37 00", "shore_reinforcement"),
    "Wells": ("31", "31 62 00", "wells"),
    # Division 32 - Exterior Improvements
    "Landscaping": ("32", "32 90 00", "landscaping"),
    "Landscaping, protective forest plantations": ("32", "32 90 00", "landscaping_forest"),
    "Roads and driveways": ("32", "32 12 00", "paving"),
    "Automobile roads": ("32", "32 12 00", "road_paving"),
    "Motorways": ("32", "32 12 00", "motorways"),
    "Sidewalks and paving": ("32", "32 12 00", "sidewalks"),
    "Bridges and overpasses": ("32", "32 34 00", "bridges"),
    "Bridges and Pipes": ("32", "32 34 00", "bridges_pipes"),
    "Airports": ("32", "32 17 00", "airports"),
    "Railways": ("32", "32 12 00", "railways"),
    "Tramway tracks": ("32", "32 12 00", "tramway"),
    "Tram tracks": ("32", "32 12 00", "tram_tracks"),
    "Track work and overhead catenary system": ("32", "32 12 00", "rail_track"),
    "Fencing": ("32", "32 31 00", "fencing"),
    # Division 33 - Utilities
    "Outdoor water supply networks": ("33", "33 11 00", "water_mains"),
    "Water supply - outdoor networks": ("33", "33 11 00", "water_supply_outdoor"),
    "Outdoor sewage networks": ("33", "33 31 00", "sewer_mains"),
    "Sewerage - outdoor networks": ("33", "33 31 00", "sewer_outdoor"),
    "Main and industrial pipelines": ("33", "33 50 00", "industrial_piping"),
    "Process pipelines": ("33", "33 50 00", "process_piping"),
    "Gas supply - outdoor networks": ("33", "33 52 00", "gas_supply"),
    "Outdoor networks and structures of gas supply": ("33", "33 52 00", "gas_networks"),
    "External engineering networks": ("33", "33 00 00", "ext_networks"),
    "Water supply and sewerage facilities": ("33", "33 11 00", "water_sewer_facilities"),
    # Specialty / heavy civil (map to closest CSI)
    "Tunnels and subways": ("31", "31 70 00", "tunneling"),
    "Tunneling works": ("31", "31 70 00", "tunneling"),
    "Underwater construction (diving) works": ("31", "31 75 00", "underwater_construction"),
    "Mining": ("31", "31 23 00", "mining"),
    "Mine workings": ("31", "31 23 00", "mine_workings"),
    "Open-pit mining of rocks": ("31", "31 23 00", "open_pit_mining"),
    # Equipment installation (Division 14 / MEP)
    "Lifting and transport equipment": ("14", "14 20 00", "elevators_conveyors"),
    "Overhead crane installation": ("14", "14 42 00", "cranes"),
    "Conveyor installation": ("14", "14 31 00", "conveyors"),
    # Industrial equipment (map to nearest reasonable CSI)
    "Nuclear power plant equipment": ("13", "13 18 00", "nuclear_equipment"),
    "Crushing, grinding and sorting equipment": ("41", "41 00 00", "crushing_equipment"),
    "Equipment for chemical and oil refining plants": ("41", "41 00 00", "chemical_equipment"),
    "Rolling mill equipment": ("41", "41 00 00", "rolling_mill"),
    "Technological metal structures": ("05", "05 50 00", "tech_metal_structures"),
}

# ---------------------------------------------------------------------------
# DDC unit -> US construction unit normalization
# ---------------------------------------------------------------------------

UNIT_MAP: dict[str, tuple[str, float]] = {
    # (normalized_unit, multiplier_to_get_per_unit_cost)
    # e.g. "100 m3" means the DDC cost is per 100 m3 → divide by 100 for per-m3
    "pcs": ("EA", 1.0),
    "m": ("LF", 1.0),  # 1 m ≈ 3.28 LF, but keep metric for fidelity
    "m2": ("SF", 1.0),  # 1 m2 ≈ 10.76 SF
    "m3": ("CY", 1.0),  # 1 m3 ≈ 1.31 CY
    "t": ("TON", 1.0),
    "kg": ("KG", 1.0),
    "km": ("KM", 1.0),
    "100 m": ("LF", 0.01),  # cost is per 100m → per-m cost = divide by 100
    "100 m2": ("SF", 0.01),
    "100 m3": ("CY", 0.01),
    "100 t": ("TON", 0.01),
    "100 pcs": ("EA", 0.01),
    "1000 m3": ("CY", 0.001),
    "1000 m": ("LF", 0.001),
    "1000 m2": ("SF", 0.001),
    "1000 pcs": ("EA", 0.001),
    "10 pcs": ("EA", 0.1),
    "10 m": ("LF", 0.1),
    "set": ("SET", 1.0),
    "joint": ("EA", 1.0),
    "span": ("EA", 1.0),
    "elevator": ("EA", 1.0),
    "1 well": ("EA", 1.0),
    "machine": ("EA", 1.0),
}

# Conversion factors from metric to US units
METRIC_TO_US: dict[str, tuple[str, float]] = {
    "LF": ("LF", 3.28084),  # m → LF
    "SF": ("SF", 10.7639),  # m2 → SF
    "CY": ("CY", 1.30795),  # m3 → CY
    "TON": ("TON", 1.10231),  # metric ton → US short ton
    "KM": ("LF", 3280.84),  # km → LF
}

# EUR to USD approximate conversion (use a recent rate)
EUR_TO_USD = 1.08


def _normalize_unit(ddc_unit: str) -> tuple[str, float]:
    """Return (normalized_unit, cost_multiplier) for a DDC unit string."""
    unit_lower = ddc_unit.strip().lower()
    # Try exact match first
    for ddc_key, (norm, mult) in UNIT_MAP.items():
        if unit_lower == ddc_key.lower():
            return norm, mult
    # Fallback: use as-is with multiplier 1
    return ddc_unit.strip().upper(), 1.0


def _safe_decimal(val: Any) -> Decimal | None:
    """Convert a float/int to Decimal, returning None for NaN/None."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return Decimal(str(round(f, 2)))
    except (ValueError, TypeError):
        return None


def _safe_decimal_4(val: Any) -> Decimal | None:
    """Convert to Decimal with 4 decimal places."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return Decimal(str(round(f, 4)))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Core ingestion logic
# ---------------------------------------------------------------------------


def load_and_transform(parquet_path: str, limit: int | None = None) -> list[dict]:
    """Load DDC CWICR parquet, deduplicate by rate_code, map to cost_items format.

    Returns list of dicts ready for DB insertion.
    """
    logger.info("Loading parquet: %s", parquet_path)
    df = pd.read_parquet(parquet_path)
    logger.info("Loaded %d rows x %d columns", len(df), len(df.columns))

    # Filter to mapped collections only
    mapped_collections = set(COLLECTION_TO_CSI.keys())
    df_mapped = df[df["collection_name"].isin(mapped_collections)].copy()
    logger.info(
        "After collection filter: %d rows (%d collections matched)",
        len(df_mapped),
        df_mapped["collection_name"].nunique(),
    )

    # Deduplicate: group by rate_code to get one row per work item
    # Use the rate-level cost columns (same across all resource rows of a rate)
    agg_cols = {
        "rate_final_name": "first",
        "rate_unit": "first",
        "collection_name": "first",
        "department_name": "first",
        "section_name": "first",
        "subsection_name": "first",
        "category_type": "first",
        "total_cost_per_position": "first",
        "total_material_cost_per_position": "first",
        "cost_of_working_hours": "first",
        "total_value_machinery_equipment": "first",
        "count_workers_per_unit": "first",
        "total_labor_hours_all_personnel": "first",
    }
    work_items = df_mapped.groupby("rate_code", as_index=False).agg(agg_cols)
    logger.info("Unique work items after dedup: %d", len(work_items))

    # Filter out items with zero or NaN total cost
    work_items = work_items[
        work_items["total_cost_per_position"].notna() & (work_items["total_cost_per_position"] > 0)
    ]
    logger.info("Work items with positive cost: %d", len(work_items))

    if limit:
        work_items = work_items.head(limit)
        logger.info("Limited to %d items", limit)

    # Transform to cost_item dicts
    items: list[dict] = []
    now = datetime.now(UTC)
    today = date.today()

    for _, row in work_items.iterrows():
        collection = row["collection_name"]
        csi_info = COLLECTION_TO_CSI.get(collection)
        if not csi_info:
            continue

        _csi_div, csi_code, category = csi_info

        # Normalize unit and compute per-unit costs
        raw_unit = str(row["rate_unit"]) if pd.notna(row["rate_unit"]) else "EA"
        norm_unit, multiplier = _normalize_unit(raw_unit)

        # Convert metric to US if applicable
        us_info = METRIC_TO_US.get(norm_unit)
        unit_conv = 1.0
        if us_info:
            norm_unit, unit_conv = us_info

        # Per-unit cost = total / (multiplier adjustment) * EUR_TO_USD / unit_conv
        # The DDC costs are per rate_unit (e.g., per 100 m3)
        # multiplier converts to per-base-unit (e.g., per m3)
        # unit_conv converts metric to US (e.g., m3 → CY)
        cost_factor = multiplier * EUR_TO_USD / unit_conv if unit_conv else 1.0

        total = (
            float(row["total_cost_per_position"])
            if pd.notna(row["total_cost_per_position"])
            else 0.0
        )
        material = (
            float(row["total_material_cost_per_position"])
            if pd.notna(row["total_material_cost_per_position"])
            else 0.0
        )
        labor = (
            float(row["cost_of_working_hours"]) if pd.notna(row["cost_of_working_hours"]) else 0.0
        )
        equipment = (
            float(row["total_value_machinery_equipment"])
            if pd.notna(row["total_value_machinery_equipment"])
            else 0.0
        )

        base_unit_cost = round(total * cost_factor, 2)
        material_cost = round(material * cost_factor, 2)
        labor_cost = round(labor * cost_factor, 2)
        equipment_cost = round(equipment * cost_factor, 2)

        # Skip items with unreasonably low cost (likely data quality issue)
        if base_unit_cost < 0.01:
            continue

        # Build description from hierarchy
        name = str(row["rate_final_name"]) if pd.notna(row["rate_final_name"]) else ""
        section = str(row["section_name"]) if pd.notna(row["section_name"]) else ""
        desc = f"{section} - {name}" if section and name else name or section or collection

        # Crew size and manhours
        crew_size = (
            float(row["count_workers_per_unit"])
            if pd.notna(row["count_workers_per_unit"])
            else None
        )
        manhours = (
            float(row["total_labor_hours_all_personnel"])
            if pd.notna(row["total_labor_hours_all_personnel"])
            else None
        )
        manhours_per_unit = round(manhours * multiplier, 4) if manhours and manhours > 0 else None

        # Uncertainty based on category from our existing ranges
        from app.services.estimating.cost_database import get_uncertainty_range

        unc_min, unc_max = get_uncertainty_range(category)

        item = {
            "id": uuid.uuid4(),
            "category": category,
            "description": desc[:500],  # cap at reasonable length
            "unit": norm_unit,
            "base_unit_cost": Decimal(str(base_unit_cost)),
            "region": "national",
            "bls_series_id": None,  # populated later by BLS mapping
            "data_source": "ddc_cwicr",
            "effective_date": today,
            "csi_code": csi_code,
            "material_cost": _safe_decimal(material_cost),
            "labor_cost": _safe_decimal(labor_cost),
            "equipment_cost": _safe_decimal(equipment_cost),
            "unit_of_measure": norm_unit,
            "crew_size": _safe_decimal(crew_size),
            "daily_output": None,  # DDC doesn't provide this directly
            "manhours_per_unit": _safe_decimal_4(manhours_per_unit),
            "uncertainty_min": Decimal(str(unc_min)),
            "uncertainty_max": Decimal(str(unc_max)),
            "last_updated": now,
            "metadata": {
                "ddc_rate_code": str(row["rate_code"]),
                "ddc_collection": collection,
                "ddc_department": str(row["department_name"])
                if pd.notna(row["department_name"])
                else None,
                "ddc_section": section,
                "ddc_raw_unit": raw_unit,
                "ddc_category_type": str(row["category_type"])
                if pd.notna(row["category_type"])
                else None,
            },
        }
        items.append(item)

    logger.info("Transformed %d cost items", len(items))
    return items


def log_statistics(items: list[dict]) -> None:
    """Log ingestion statistics by CSI division."""
    from collections import Counter

    div_counts: Counter[str] = Counter()
    cat_counts: Counter[str] = Counter()
    total_cost = Decimal("0")

    for item in items:
        csi = item.get("csi_code", "")
        div = csi[:2] if csi else "??"
        div_counts[div] += 1
        cat_counts[item["category"]] += 1
        total_cost += item["base_unit_cost"]

    logger.info("=" * 60)
    logger.info("INGESTION STATISTICS")
    logger.info("=" * 60)
    logger.info("Total items: %d", len(items))
    logger.info("")
    logger.info("By CSI Division:")
    for div in sorted(div_counts):
        logger.info("  Division %s: %d items", div, div_counts[div])
    logger.info("")
    logger.info("Top 20 categories:")
    for cat, count in cat_counts.most_common(20):
        logger.info("  %-30s %d items", cat, count)
    logger.info("=" * 60)


async def insert_items(items: list[dict], db_url: str) -> int:
    """Insert cost items into the database."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    inserted = 0
    batch_size = 500

    async with async_session() as session:
        for i in range(0, len(items), batch_size):
            batch = items[i : i + batch_size]

            # Use raw insert for performance
            from app.models.estimating import CostItem

            stmt = pg_insert(CostItem.__table__).values(batch)
            # On conflict with same (category, unit, data_source, csi_code) → update
            stmt = stmt.on_conflict_do_nothing()

            await session.execute(stmt)
            await session.commit()
            inserted += len(batch)
            logger.info("Inserted batch %d-%d (%d total)", i, i + len(batch), inserted)

    await engine.dispose()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest DDC CWICR data into cost_items")
    parser.add_argument(
        "--parquet",
        default=str(
            Path(__file__).resolve().parents[3]
            / "constructai-data"
            / "cost-data"
            / "ddc-cwicr"
            / "OpenConstructionEstimate-DDC-CWICR"
            / "EN___DDC_CWICR"
            / "ENG_TORONTO_workitems_costs_resources_DDC_CWICR.parquet"
        ),
        help="Path to DDC CWICR parquet file",
    )
    parser.add_argument("--dry-run", action="store_true", help="Transform only, don't insert")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of items")
    parser.add_argument("--db-url", default=None, help="Database URL (default: from settings)")
    args = parser.parse_args()

    # Load and transform
    items = load_and_transform(args.parquet, limit=args.limit)
    log_statistics(items)

    if args.dry_run:
        logger.info("DRY RUN — no database changes made")
        return

    # Get DB URL
    db_url = args.db_url
    if not db_url:
        try:
            from app.config import settings

            db_url = str(settings.DATABASE_URL)
        except ImportError:
            logger.error("Cannot import settings. Pass --db-url explicitly.")
            sys.exit(1)

    # Insert
    count = asyncio.run(insert_items(items, db_url))
    logger.info("Successfully inserted %d cost items", count)


if __name__ == "__main__":
    main()
