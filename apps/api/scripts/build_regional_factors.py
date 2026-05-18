#!/usr/bin/env python3
"""Build regional cost factors from BLS OEWS data and curated premiums.

Combines:
  1. BLS Occupational Employment & Wage Statistics (OEWS) for construction
     trades (SOC 47-XXXX) to compute relative labor cost ratios per metro.
  2. Curated material premiums for metros with known cost differentials
     (e.g., Hawaii +30%, NYC +25%).
  3. Equipment factors default to 1.0 (minimal regional variation).

Output: data/seed/regional_factors_v1.json

Usage:
    python scripts/build_regional_factors.py
    python scripts/build_regional_factors.py --bls-key YOUR_KEY
    python scripts/build_regional_factors.py --dry-run
    python scripts/build_regional_factors.py --load-db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BLS_OEWS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# SOC 47-0000: Construction and Extraction Occupations (broad group)
# We fetch mean hourly wage for the top 50 MSAs.
_SOC_CONSTRUCTION = "47-0000"

# National average hourly wage for SOC 47-0000 (2024 OEWS estimate)
_NATIONAL_AVG_WAGE = 28.50

OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "seed" / "regional_factors_v1.json"

# ---------------------------------------------------------------------------
# Top 50 US metro areas with curated data
# ---------------------------------------------------------------------------

# Each entry: city, state, state_abbr, zip_prefix, lat, lon,
# curated material premium, curated labor premium (BLS override),
# equipment factor
METRO_CATALOG: list[dict] = [
    {
        "city": "New York",
        "state": "New York",
        "state_abbr": "NY",
        "zip_prefix": "100",
        "lat": 40.7128,
        "lon": -74.0060,
        "mat": 1.25,
        "lab": 1.52,
        "eq": 1.05,
    },
    {
        "city": "Los Angeles",
        "state": "California",
        "state_abbr": "CA",
        "zip_prefix": "900",
        "lat": 34.0522,
        "lon": -118.2437,
        "mat": 1.15,
        "lab": 1.35,
        "eq": 1.02,
    },
    {
        "city": "Chicago",
        "state": "Illinois",
        "state_abbr": "IL",
        "zip_prefix": "606",
        "lat": 41.8781,
        "lon": -87.6298,
        "mat": 1.08,
        "lab": 1.32,
        "eq": 1.00,
    },
    {
        "city": "Houston",
        "state": "Texas",
        "state_abbr": "TX",
        "zip_prefix": "770",
        "lat": 29.7604,
        "lon": -95.3698,
        "mat": 0.95,
        "lab": 0.92,
        "eq": 0.98,
    },
    {
        "city": "Phoenix",
        "state": "Arizona",
        "state_abbr": "AZ",
        "zip_prefix": "850",
        "lat": 33.4484,
        "lon": -112.0740,
        "mat": 0.98,
        "lab": 0.90,
        "eq": 1.00,
    },
    {
        "city": "Philadelphia",
        "state": "Pennsylvania",
        "state_abbr": "PA",
        "zip_prefix": "191",
        "lat": 39.9526,
        "lon": -75.1652,
        "mat": 1.12,
        "lab": 1.38,
        "eq": 1.02,
    },
    {
        "city": "San Antonio",
        "state": "Texas",
        "state_abbr": "TX",
        "zip_prefix": "782",
        "lat": 29.4241,
        "lon": -98.4936,
        "mat": 0.93,
        "lab": 0.85,
        "eq": 0.98,
    },
    {
        "city": "San Diego",
        "state": "California",
        "state_abbr": "CA",
        "zip_prefix": "921",
        "lat": 32.7157,
        "lon": -117.1611,
        "mat": 1.12,
        "lab": 1.30,
        "eq": 1.02,
    },
    {
        "city": "Dallas",
        "state": "Texas",
        "state_abbr": "TX",
        "zip_prefix": "752",
        "lat": 32.7767,
        "lon": -96.7970,
        "mat": 0.96,
        "lab": 0.90,
        "eq": 0.98,
    },
    {
        "city": "San Jose",
        "state": "California",
        "state_abbr": "CA",
        "zip_prefix": "951",
        "lat": 37.3382,
        "lon": -121.8863,
        "mat": 1.18,
        "lab": 1.48,
        "eq": 1.03,
    },
    {
        "city": "Austin",
        "state": "Texas",
        "state_abbr": "TX",
        "zip_prefix": "787",
        "lat": 30.2672,
        "lon": -97.7431,
        "mat": 0.97,
        "lab": 0.88,
        "eq": 0.99,
    },
    {
        "city": "Jacksonville",
        "state": "Florida",
        "state_abbr": "FL",
        "zip_prefix": "322",
        "lat": 30.3322,
        "lon": -81.6557,
        "mat": 0.95,
        "lab": 0.82,
        "eq": 0.99,
    },
    {
        "city": "San Francisco",
        "state": "California",
        "state_abbr": "CA",
        "zip_prefix": "941",
        "lat": 37.7749,
        "lon": -122.4194,
        "mat": 1.20,
        "lab": 1.55,
        "eq": 1.05,
    },
    {
        "city": "Columbus",
        "state": "Ohio",
        "state_abbr": "OH",
        "zip_prefix": "432",
        "lat": 39.9612,
        "lon": -82.9988,
        "mat": 0.96,
        "lab": 0.92,
        "eq": 0.99,
    },
    {
        "city": "Indianapolis",
        "state": "Indiana",
        "state_abbr": "IN",
        "zip_prefix": "462",
        "lat": 39.7684,
        "lon": -86.1581,
        "mat": 0.95,
        "lab": 0.90,
        "eq": 0.99,
    },
    {
        "city": "Charlotte",
        "state": "North Carolina",
        "state_abbr": "NC",
        "zip_prefix": "282",
        "lat": 35.2271,
        "lon": -80.8431,
        "mat": 0.94,
        "lab": 0.82,
        "eq": 0.99,
    },
    {
        "city": "Fort Worth",
        "state": "Texas",
        "state_abbr": "TX",
        "zip_prefix": "761",
        "lat": 32.7555,
        "lon": -97.3308,
        "mat": 0.95,
        "lab": 0.88,
        "eq": 0.98,
    },
    {
        "city": "Seattle",
        "state": "Washington",
        "state_abbr": "WA",
        "zip_prefix": "981",
        "lat": 47.6062,
        "lon": -122.3321,
        "mat": 1.12,
        "lab": 1.35,
        "eq": 1.02,
    },
    {
        "city": "Denver",
        "state": "Colorado",
        "state_abbr": "CO",
        "zip_prefix": "802",
        "lat": 39.7392,
        "lon": -104.9903,
        "mat": 1.05,
        "lab": 1.08,
        "eq": 1.00,
    },
    {
        "city": "Washington",
        "state": "District of Columbia",
        "state_abbr": "DC",
        "zip_prefix": "200",
        "lat": 38.9072,
        "lon": -77.0369,
        "mat": 1.10,
        "lab": 1.25,
        "eq": 1.02,
    },
    {
        "city": "Nashville",
        "state": "Tennessee",
        "state_abbr": "TN",
        "zip_prefix": "372",
        "lat": 36.1627,
        "lon": -86.7816,
        "mat": 0.95,
        "lab": 0.85,
        "eq": 0.99,
    },
    {
        "city": "Oklahoma City",
        "state": "Oklahoma",
        "state_abbr": "OK",
        "zip_prefix": "731",
        "lat": 35.4676,
        "lon": -97.5164,
        "mat": 0.92,
        "lab": 0.80,
        "eq": 0.98,
    },
    {
        "city": "El Paso",
        "state": "Texas",
        "state_abbr": "TX",
        "zip_prefix": "799",
        "lat": 31.7619,
        "lon": -106.4850,
        "mat": 0.90,
        "lab": 0.75,
        "eq": 0.97,
    },
    {
        "city": "Boston",
        "state": "Massachusetts",
        "state_abbr": "MA",
        "zip_prefix": "021",
        "lat": 42.3601,
        "lon": -71.0589,
        "mat": 1.18,
        "lab": 1.42,
        "eq": 1.03,
    },
    {
        "city": "Portland",
        "state": "Oregon",
        "state_abbr": "OR",
        "zip_prefix": "972",
        "lat": 45.5152,
        "lon": -122.6784,
        "mat": 1.08,
        "lab": 1.18,
        "eq": 1.01,
    },
    {
        "city": "Las Vegas",
        "state": "Nevada",
        "state_abbr": "NV",
        "zip_prefix": "891",
        "lat": 36.1699,
        "lon": -115.1398,
        "mat": 1.05,
        "lab": 1.15,
        "eq": 1.00,
    },
    {
        "city": "Memphis",
        "state": "Tennessee",
        "state_abbr": "TN",
        "zip_prefix": "381",
        "lat": 35.1495,
        "lon": -90.0490,
        "mat": 0.92,
        "lab": 0.78,
        "eq": 0.98,
    },
    {
        "city": "Louisville",
        "state": "Kentucky",
        "state_abbr": "KY",
        "zip_prefix": "402",
        "lat": 38.2527,
        "lon": -85.7585,
        "mat": 0.94,
        "lab": 0.85,
        "eq": 0.99,
    },
    {
        "city": "Baltimore",
        "state": "Maryland",
        "state_abbr": "MD",
        "zip_prefix": "212",
        "lat": 39.2904,
        "lon": -76.6122,
        "mat": 1.05,
        "lab": 1.10,
        "eq": 1.00,
    },
    {
        "city": "Milwaukee",
        "state": "Wisconsin",
        "state_abbr": "WI",
        "zip_prefix": "532",
        "lat": 43.0389,
        "lon": -87.9065,
        "mat": 1.02,
        "lab": 1.12,
        "eq": 1.00,
    },
    {
        "city": "Albuquerque",
        "state": "New Mexico",
        "state_abbr": "NM",
        "zip_prefix": "871",
        "lat": 35.0844,
        "lon": -106.6504,
        "mat": 0.95,
        "lab": 0.82,
        "eq": 0.99,
    },
    {
        "city": "Tucson",
        "state": "Arizona",
        "state_abbr": "AZ",
        "zip_prefix": "857",
        "lat": 32.2226,
        "lon": -110.9747,
        "mat": 0.95,
        "lab": 0.82,
        "eq": 0.99,
    },
    {
        "city": "Fresno",
        "state": "California",
        "state_abbr": "CA",
        "zip_prefix": "937",
        "lat": 36.7378,
        "lon": -119.7871,
        "mat": 1.05,
        "lab": 1.10,
        "eq": 1.00,
    },
    {
        "city": "Sacramento",
        "state": "California",
        "state_abbr": "CA",
        "zip_prefix": "958",
        "lat": 38.5816,
        "lon": -121.4944,
        "mat": 1.10,
        "lab": 1.28,
        "eq": 1.02,
    },
    {
        "city": "Mesa",
        "state": "Arizona",
        "state_abbr": "AZ",
        "zip_prefix": "852",
        "lat": 33.4152,
        "lon": -111.8315,
        "mat": 0.97,
        "lab": 0.88,
        "eq": 1.00,
    },
    {
        "city": "Kansas City",
        "state": "Missouri",
        "state_abbr": "MO",
        "zip_prefix": "641",
        "lat": 39.0997,
        "lon": -94.5786,
        "mat": 0.97,
        "lab": 1.02,
        "eq": 0.99,
    },
    {
        "city": "Atlanta",
        "state": "Georgia",
        "state_abbr": "GA",
        "zip_prefix": "303",
        "lat": 33.7490,
        "lon": -84.3880,
        "mat": 0.97,
        "lab": 0.88,
        "eq": 0.99,
    },
    {
        "city": "Omaha",
        "state": "Nebraska",
        "state_abbr": "NE",
        "zip_prefix": "681",
        "lat": 41.2565,
        "lon": -95.9345,
        "mat": 0.95,
        "lab": 0.88,
        "eq": 0.99,
    },
    {
        "city": "Colorado Springs",
        "state": "Colorado",
        "state_abbr": "CO",
        "zip_prefix": "809",
        "lat": 38.8339,
        "lon": -104.8214,
        "mat": 1.02,
        "lab": 0.98,
        "eq": 1.00,
    },
    {
        "city": "Raleigh",
        "state": "North Carolina",
        "state_abbr": "NC",
        "zip_prefix": "276",
        "lat": 35.7796,
        "lon": -78.6382,
        "mat": 0.95,
        "lab": 0.82,
        "eq": 0.99,
    },
    {
        "city": "Long Beach",
        "state": "California",
        "state_abbr": "CA",
        "zip_prefix": "908",
        "lat": 33.7701,
        "lon": -118.1937,
        "mat": 1.15,
        "lab": 1.35,
        "eq": 1.02,
    },
    {
        "city": "Virginia Beach",
        "state": "Virginia",
        "state_abbr": "VA",
        "zip_prefix": "234",
        "lat": 36.8529,
        "lon": -75.9780,
        "mat": 0.98,
        "lab": 0.88,
        "eq": 0.99,
    },
    {
        "city": "Miami",
        "state": "Florida",
        "state_abbr": "FL",
        "zip_prefix": "331",
        "lat": 25.7617,
        "lon": -80.1918,
        "mat": 1.08,
        "lab": 0.95,
        "eq": 1.00,
    },
    {
        "city": "Oakland",
        "state": "California",
        "state_abbr": "CA",
        "zip_prefix": "946",
        "lat": 37.8044,
        "lon": -122.2712,
        "mat": 1.18,
        "lab": 1.50,
        "eq": 1.04,
    },
    {
        "city": "Minneapolis",
        "state": "Minnesota",
        "state_abbr": "MN",
        "zip_prefix": "554",
        "lat": 44.9778,
        "lon": -93.2650,
        "mat": 1.05,
        "lab": 1.18,
        "eq": 1.00,
    },
    {
        "city": "Tampa",
        "state": "Florida",
        "state_abbr": "FL",
        "zip_prefix": "336",
        "lat": 27.9506,
        "lon": -82.4572,
        "mat": 0.98,
        "lab": 0.82,
        "eq": 0.99,
    },
    {
        "city": "New Orleans",
        "state": "Louisiana",
        "state_abbr": "LA",
        "zip_prefix": "701",
        "lat": 29.9511,
        "lon": -90.0715,
        "mat": 0.96,
        "lab": 0.85,
        "eq": 0.99,
    },
    {
        "city": "Honolulu",
        "state": "Hawaii",
        "state_abbr": "HI",
        "zip_prefix": "968",
        "lat": 21.3069,
        "lon": -157.8583,
        "mat": 1.30,
        "lab": 1.28,
        "eq": 1.15,
    },
    {
        "city": "Anchorage",
        "state": "Alaska",
        "state_abbr": "AK",
        "zip_prefix": "995",
        "lat": 61.2181,
        "lon": -149.9003,
        "mat": 1.35,
        "lab": 1.32,
        "eq": 1.18,
    },
    {
        "city": "Detroit",
        "state": "Michigan",
        "state_abbr": "MI",
        "zip_prefix": "481",
        "lat": 42.3314,
        "lon": -83.0458,
        "mat": 1.02,
        "lab": 1.18,
        "eq": 1.00,
    },
]


# ---------------------------------------------------------------------------
# BLS OEWS fetch (optional — overrides curated labor if available)
# ---------------------------------------------------------------------------


async def fetch_oews_wages(api_key: str | None, metros: list[dict]) -> dict[str, float]:
    """Fetch mean hourly wages for construction from BLS OEWS.

    Returns dict mapping state_abbr -> labor_ratio (relative to national avg).
    This is best-effort; missing data just uses curated values.
    """
    if not api_key:
        logger.info("No BLS API key; using curated labor factors only")
        return {}

    # OEWS data is published by state; series format:
    # OEUM{area_code}{soc_code}000000000{data_type}
    # We'll fetch state-level data and compute ratios.
    # For simplicity, use the CES construction wage series by state.
    logger.info("BLS OEWS fetch not yet implemented; using curated labor factors")
    return {}


# ---------------------------------------------------------------------------
# Build factors
# ---------------------------------------------------------------------------


def compute_composite(mat: float, lab: float, eq: float) -> float:
    """Compute weighted composite factor: 40% labor + 45% material + 15% equipment."""
    return round(0.40 * lab + 0.45 * mat + 0.15 * eq, 4)


def build_factors(
    oews_overrides: dict[str, float] | None = None,
) -> list[dict]:
    """Build the full regional factors list from curated data + OEWS overrides."""
    if oews_overrides is None:
        oews_overrides = {}

    results = []
    for metro in METRO_CATALOG:
        lab = oews_overrides.get(metro["state_abbr"], metro["lab"])
        mat = metro["mat"]
        eq = metro["eq"]
        comp = compute_composite(mat, lab, eq)

        results.append(
            {
                "city": metro["city"],
                "state": metro["state"],
                "state_abbr": metro["state_abbr"],
                "zip_prefix": metro["zip_prefix"],
                "latitude": metro["lat"],
                "longitude": metro["lon"],
                "material_factor": mat,
                "labor_factor": lab,
                "equipment_factor": eq,
                "composite_factor": comp,
            }
        )

    return results


# ---------------------------------------------------------------------------
# Database loading
# ---------------------------------------------------------------------------


async def load_into_db(factors: list[dict], db_url: str) -> int:
    """Insert regional factors into the database.

    Uses PostgreSQL INSERT ... ON CONFLICT for idempotent upserts.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    upsert_sql = text("""
        INSERT INTO regional_cost_factors
            (city, state, state_abbr, zip_prefix, latitude, longitude,
             material_factor, labor_factor, equipment_factor, composite_factor,
             effective_date, data_source, metadata)
        VALUES
            (:city, :state, :state_abbr, :zip_prefix, :latitude, :longitude,
             :material_factor, :labor_factor, :equipment_factor, :composite_factor,
             :effective_date, :data_source, :metadata)
        ON CONFLICT (city, state_abbr)
        DO UPDATE SET
            material_factor = EXCLUDED.material_factor,
            labor_factor = EXCLUDED.labor_factor,
            equipment_factor = EXCLUDED.equipment_factor,
            composite_factor = EXCLUDED.composite_factor,
            effective_date = EXCLUDED.effective_date,
            updated_at = NOW()
    """)

    count = 0
    async with async_session() as session:
        for f in factors:
            params = {
                **f,
                "effective_date": date.today().isoformat(),
                "data_source": "curated",
                "metadata": "{}",
            }
            await session.execute(upsert_sql, params)
            count += 1
        await session.commit()

    await engine.dispose()
    logger.info("Loaded %d regional factors into database", count)
    return count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Build regional cost factors")
    parser.add_argument("--bls-key", default=None, help="BLS API key for OEWS data")
    parser.add_argument("--dry-run", action="store_true", help="Print factors without writing")
    parser.add_argument("--load-db", action="store_true", help="Load factors into database")
    parser.add_argument(
        "--db-url", default=None, help="Database URL (defaults to DATABASE_URL env)"
    )
    args = parser.parse_args()

    api_key = args.bls_key or os.environ.get("BLS_API_KEY")

    # Fetch OEWS data if available
    oews = asyncio.run(fetch_oews_wages(api_key, METRO_CATALOG))

    # Build factors
    factors = build_factors(oews or None)

    logger.info("Built %d regional factors", len(factors))

    # Log summary
    for f in factors:
        logger.info(
            "  %-20s %s  mat=%.2f lab=%.2f eq=%.2f comp=%.4f",
            f["city"],
            f["state_abbr"],
            f["material_factor"],
            f["labor_factor"],
            f["equipment_factor"],
            f["composite_factor"],
        )

    if args.dry_run:
        print(json.dumps(factors, indent=2))
        return

    # Write to JSON
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as fp:
        json.dump(factors, fp, indent=2)
    logger.info("Wrote %d factors to %s", len(factors), OUTPUT_PATH)

    # Load into DB if requested
    if args.load_db:
        db_url = args.db_url or os.environ.get("DATABASE_URL")
        if not db_url:
            logger.error("--load-db requires --db-url or DATABASE_URL env var")
            sys.exit(1)
        asyncio.run(load_into_db(factors, db_url))


if __name__ == "__main__":
    main()
