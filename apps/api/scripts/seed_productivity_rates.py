#!/usr/bin/env python3
"""Seed the productivity_rates table from curated JSON data.

Usage:
    python scripts/seed_productivity_rates.py
    python scripts/seed_productivity_rates.py --dry-run
    python scripts/seed_productivity_rates.py --db-url postgresql+asyncpg://...
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SEED_FILE = Path(__file__).resolve().parents[1] / "data" / "seed" / "productivity_rates_v1.json"


def load_seed_data() -> list[dict]:
    """Load productivity rates from seed JSON."""
    if not SEED_FILE.exists():
        logger.error("Seed file not found: %s", SEED_FILE)
        sys.exit(1)

    with open(SEED_FILE) as f:
        data = json.load(f)

    if isinstance(data, dict) and "rates" in data:
        return data["rates"]
    if isinstance(data, list):
        return data

    logger.error("Unexpected JSON format in %s", SEED_FILE)
    sys.exit(1)


def validate_rates(rates: list[dict]) -> list[str]:
    """Validate seed data and return list of warnings."""
    warnings = []
    required_keys = {
        "activity_code",
        "activity_name",
        "trade",
        "crew_size",
        "daily_output",
        "unit",
        "manhours_per_unit",
    }
    codes_seen: set[str] = set()

    for i, rate in enumerate(rates):
        missing = required_keys - set(rate.keys())
        if missing:
            warnings.append(f"Rate {i}: missing keys {missing}")

        code = rate.get("activity_code", "")
        if code in codes_seen:
            warnings.append(f"Rate {i}: duplicate activity_code '{code}'")
        codes_seen.add(code)

        mh = rate.get("manhours_per_unit", 0)
        if mh <= 0:
            warnings.append(f"Rate {i} ({code}): manhours_per_unit <= 0")

        output = rate.get("daily_output", 0)
        if output <= 0:
            warnings.append(f"Rate {i} ({code}): daily_output <= 0")

    return warnings


async def seed_database(rates: list[dict], db_url: str) -> int:
    """Insert productivity rates into database with upsert on activity_code."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    upsert_sql = text("""
        INSERT INTO productivity_rates
            (activity_code, activity_name, csi_division, trade,
             crew_composition, crew_size, daily_output, unit,
             manhours_per_unit, conditions, data_source, effective_date, metadata)
        VALUES
            (:activity_code, :activity_name, :csi_division, :trade,
             :crew_composition, :crew_size, :daily_output, :unit,
             :manhours_per_unit, :conditions, :data_source, :effective_date, :metadata)
        ON CONFLICT (activity_code)
        DO UPDATE SET
            activity_name = EXCLUDED.activity_name,
            crew_composition = EXCLUDED.crew_composition,
            crew_size = EXCLUDED.crew_size,
            daily_output = EXCLUDED.daily_output,
            manhours_per_unit = EXCLUDED.manhours_per_unit,
            updated_at = NOW()
    """)

    count = 0
    async with async_session() as session:
        for rate in rates:
            params = {
                "activity_code": rate["activity_code"],
                "activity_name": rate["activity_name"],
                "csi_division": rate.get("csi_division"),
                "trade": rate["trade"],
                "crew_composition": json.dumps(rate.get("crew_composition", {})),
                "crew_size": rate["crew_size"],
                "daily_output": rate["daily_output"],
                "unit": rate["unit"],
                "manhours_per_unit": rate["manhours_per_unit"],
                "conditions": rate.get("conditions", "normal"),
                "data_source": rate.get("data_source", "curated"),
                "effective_date": (
                    date.fromisoformat(rate["effective_date"])
                    if isinstance(rate.get("effective_date"), str)
                    else rate.get("effective_date") or date(2025, 1, 1)
                ),
                "metadata": json.dumps(rate.get("metadata", {})),
            }
            await session.execute(upsert_sql, params)
            count += 1
        await session.commit()

    await engine.dispose()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed productivity rates")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, don't load")
    parser.add_argument("--db-url", default=None, help="Database URL")
    args = parser.parse_args()

    rates = load_seed_data()
    logger.info("Loaded %d productivity rates from seed file", len(rates))

    # Validate
    warnings = validate_rates(rates)
    if warnings:
        for w in warnings:
            logger.warning(w)
        if not args.dry_run:
            logger.error("Fix validation warnings before seeding")
            sys.exit(1)

    # Summary by trade
    trades: dict[str, int] = {}
    for r in rates:
        trades[r["trade"]] = trades.get(r["trade"], 0) + 1
    for trade, count in sorted(trades.items()):
        logger.info("  %-15s %3d activities", trade, count)

    if args.dry_run:
        logger.info("Dry run complete, %d rates validated", len(rates))
        return

    db_url = args.db_url or os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("--db-url or DATABASE_URL required")
        sys.exit(1)

    count = asyncio.run(seed_database(rates, db_url))
    logger.info("Seeded %d productivity rates", count)


if __name__ == "__main__":
    main()
