#!/usr/bin/env python3
"""Ingest OSHA public enforcement CSV data into the database.

Downloads are available at https://enforcedata.dol.gov/views/data_summary.php

Usage:
    python scripts/ingest_osha_data.py \
        --inspections osha_inspection.csv \
        --violations osha_violation.csv

    python scripts/ingest_osha_data.py \
        --inspections osha_inspection.csv \
        --violations osha_violation.csv \
        --since 5 --batch-size 1000 --dry-run

    python scripts/ingest_osha_data.py \
        --inspections osha_inspection.csv \
        --violations osha_violation.csv \
        --db-url postgresql+asyncpg://user:pass@localhost/constructai
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import re
import sys
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def parse_standard(raw: str) -> str | None:
    """Convert OSHA raw standard code to canonical CFR dotted form.

    OSHA stores the section number zero-padded to 4 digits, but the
    canonical citation form (used in legal references and the published
    CFR) drops the leading zeros. ``19260501`` is therefore CFR
    ``1926.501`` (fall protection), not ``1926.0501``.

    Must match osha_lookup.parse_standard exactly — the ingest script
    writes data the lookup queries, so any divergence breaks lookups.

    "19260501" -> "1926.501"
    "19100134" -> "1910.134"
    """
    raw = raw.strip()
    if not raw or len(raw) < 5:
        return None
    part = raw[:4]
    # Strip leading zeros from the section component, but keep at least
    # one digit so a section of "0000" doesn't render as "1926.".
    rest = raw[4:].lstrip("0") or "0"
    return f"{part}.{rest}"


def is_construction(naics: str | None, sic: str | None) -> bool:
    """Return True if record is in the construction sector.

    Construction = NAICS 23xx or SIC 1500-1799.
    """
    if naics and naics.startswith("23"):
        return True
    if sic:
        try:
            sic_int = int(sic)
            if 1500 <= sic_int <= 1799:
                return True
        except ValueError:
            pass
    return False


def _safe_date(val: str) -> date | None:
    """Parse MM/DD/YYYY or YYYY-MM-DD to date, or None."""
    val = val.strip()
    if not val:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            from datetime import datetime

            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def _safe_decimal(val: str) -> Decimal | None:
    """Parse decimal string, returning None on failure."""
    val = val.strip()
    if not val:
        return None
    try:
        return Decimal(val)
    except InvalidOperation:
        return None


def _safe_int(val: str) -> int | None:
    """Parse integer string, returning None on failure."""
    val = val.strip()
    if not val:
        return None
    try:
        return int(val)
    except ValueError:
        return None


def _safe_bool(val: str) -> bool:
    """Parse delete_flag-style value (empty or 0 = False, else True)."""
    val = val.strip().upper()
    return val in ("1", "Y", "TRUE", "X")


# ---------------------------------------------------------------------------
# CSV parsers
# ---------------------------------------------------------------------------

# Expected column names for inspections CSV (OSHA enforcedata format)
_INSPECTION_COLUMNS = {
    "activity_nr",
    "reporting_id",
    "state_flag",
    "estab_name",
    "site_address",
    "site_city",
    "site_state",
    "site_zip",
    "owner_type",
    "naics_code",
    "sic_code",
    "insp_type",
    "safety_hlth",
    "open_date",
    "close_case_date",
    "nr_in_estab",
    "union_status",
}

# Expected column names for violations CSV
_VIOLATION_COLUMNS = {
    "activity_nr",
    "citation_id",
    "delete_flag",
    "standard",
    "viol_type",
    "gravity",
    "nr_exposed",
    "nr_instances",
    "current_penalty",
    "initial_penalty",
    "penalty",
    "contest_date",
    "final_order_date",
    "emphasis",
    "abate_date",
    "issuance_date",
}


def parse_inspections_csv(
    path: Path,
    since: date,
    limit: int | None = None,
) -> Iterator[dict]:
    """Stream construction inspections from OSHA CSV.

    Filters to:
    - Construction sector (NAICS 23xx or SIC 1500-1799)
    - open_date >= since
    """
    count = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)

        # Validate columns
        if reader.fieldnames:
            missing = _INSPECTION_COLUMNS - set(reader.fieldnames)
            if missing:
                logger.warning("Inspections CSV missing columns: %s", missing)

        for row in reader:
            naics = row.get("naics_code", "").strip()
            sic = row.get("sic_code", "").strip()

            if not is_construction(naics or None, sic or None):
                continue

            open_dt = _safe_date(row.get("open_date", ""))
            if open_dt and open_dt < since:
                continue

            activity_nr = row.get("activity_nr", "").strip()
            if not activity_nr:
                continue

            estab_name = row.get("estab_name", "").strip()
            if not estab_name:
                continue

            yield {
                "activity_nr": activity_nr,
                "reporting_id": row.get("reporting_id", "").strip() or None,
                "state_flag": row.get("state_flag", "").strip() or None,
                "establishment_name": estab_name,
                "name_normalized": normalize_name(estab_name),
                "site_address": row.get("site_address", "").strip() or None,
                "site_city": row.get("site_city", "").strip() or None,
                "site_state": row.get("site_state", "").strip() or None,
                "site_zip": row.get("site_zip", "").strip() or None,
                "owner_type": row.get("owner_type", "").strip() or None,
                "naics_code": naics or None,
                "sic_code": sic or None,
                "insp_type": row.get("insp_type", "").strip() or None,
                "safety_hlth": row.get("safety_hlth", "").strip() or None,
                "open_date": open_dt,
                "close_date": _safe_date(
                    row.get("close_case_date", "") or row.get("close_date", "")
                ),
                "total_penalty": _safe_decimal(row.get("total_penalty", "")) or Decimal("0"),
                "nr_in_estab": _safe_int(row.get("nr_in_estab", "")),
                "union_status": row.get("union_status", "").strip() or None,
                "insp_scope": row.get("insp_scope", "").strip() or None,
            }

            count += 1
            if limit and count >= limit:
                return


def parse_violations_csv(
    path: Path,
    valid_activity_nrs: set[str],
    limit: int | None = None,
) -> Iterator[dict]:
    """Stream violations linked to loaded inspections.

    Only yields rows whose activity_nr is in valid_activity_nrs.
    """
    count = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames:
            missing = _VIOLATION_COLUMNS - set(reader.fieldnames)
            if missing:
                logger.warning("Violations CSV missing columns: %s", missing)

        for row in reader:
            activity_nr = row.get("activity_nr", "").strip()
            if not activity_nr or activity_nr not in valid_activity_nrs:
                continue

            standard_raw = row.get("standard", "").strip()

            yield {
                "activity_nr": activity_nr,
                "citation_id": row.get("citation_id", "").strip() or None,
                "delete_flag": _safe_bool(row.get("delete_flag", "")),
                "standard_cited": standard_raw or None,
                "standard_parsed": parse_standard(standard_raw),
                "violation_type": row.get("viol_type", "").strip() or None,
                "gravity": _safe_int(row.get("gravity", "")),
                "nr_exposed": _safe_int(row.get("nr_exposed", "")),
                "nr_instances": _safe_int(row.get("nr_instances", "")),
                "penalty": _safe_decimal(row.get("penalty", "")),
                "initial_penalty": _safe_decimal(row.get("initial_penalty", "")),
                "current_penalty": _safe_decimal(row.get("current_penalty", "")),
                "contest_date": _safe_date(row.get("contest_date", "")),
                "final_order_date": _safe_date(row.get("final_order_date", "")),
                "emphasis": row.get("emphasis", "").strip() or None,
                "abatement_date": _safe_date(row.get("abate_date", "")),
                "issuance_date": _safe_date(row.get("issuance_date", "")),
            }

            count += 1
            if limit and count >= limit:
                return


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------


def _chunk(iterable: list, size: int) -> Iterator[list]:
    """Split a list into chunks of given size."""
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]


async def upsert_inspections(
    session: AsyncSession,
    rows: list[dict],
) -> int:
    """Upsert inspection rows using ON CONFLICT on activity_nr."""
    from sqlalchemy import text

    if not rows:
        return 0

    sql = text("""
        INSERT INTO osha_inspections (
            activity_nr, reporting_id, state_flag,
            establishment_name, name_normalized,
            site_address, site_city, site_state, site_zip,
            owner_type, naics_code, sic_code,
            insp_type, safety_hlth, open_date, close_date,
            total_penalty, nr_in_estab, union_status, insp_scope
        ) VALUES (
            :activity_nr, :reporting_id, :state_flag,
            :establishment_name, :name_normalized,
            :site_address, :site_city, :site_state, :site_zip,
            :owner_type, :naics_code, :sic_code,
            :insp_type, :safety_hlth, :open_date, :close_date,
            :total_penalty, :nr_in_estab, :union_status, :insp_scope
        )
        ON CONFLICT (activity_nr) DO UPDATE SET
            reporting_id = EXCLUDED.reporting_id,
            state_flag = EXCLUDED.state_flag,
            establishment_name = EXCLUDED.establishment_name,
            name_normalized = EXCLUDED.name_normalized,
            site_address = EXCLUDED.site_address,
            site_city = EXCLUDED.site_city,
            site_state = EXCLUDED.site_state,
            site_zip = EXCLUDED.site_zip,
            owner_type = EXCLUDED.owner_type,
            naics_code = EXCLUDED.naics_code,
            sic_code = EXCLUDED.sic_code,
            insp_type = EXCLUDED.insp_type,
            safety_hlth = EXCLUDED.safety_hlth,
            open_date = EXCLUDED.open_date,
            close_date = EXCLUDED.close_date,
            total_penalty = EXCLUDED.total_penalty,
            nr_in_estab = EXCLUDED.nr_in_estab,
            union_status = EXCLUDED.union_status,
            insp_scope = EXCLUDED.insp_scope
    """)

    for row in rows:
        await session.execute(sql, row)

    return len(rows)


async def insert_violations(
    session: AsyncSession,
    rows: list[dict],
    batch_activity_nrs: set[str],
) -> int:
    """Insert violations, deleting existing ones for the same activity_nrs first.

    Violations have no stable unique key in the OSHA CSV, so we use
    DELETE + INSERT for each batch of activity_nrs.
    """
    from sqlalchemy import text

    if not rows:
        return 0

    # Delete existing violations for these inspections
    delete_sql = text("""
        DELETE FROM osha_violations
        WHERE activity_nr = ANY(:activity_nrs)
    """)
    await session.execute(delete_sql, {"activity_nrs": list(batch_activity_nrs)})

    insert_sql = text("""
        INSERT INTO osha_violations (
            activity_nr, citation_id, delete_flag, standard_cited, standard_parsed,
            violation_type, gravity, nr_exposed, nr_instances,
            penalty, initial_penalty, current_penalty,
            contest_date, final_order_date, emphasis,
            abatement_date, issuance_date
        ) VALUES (
            :activity_nr, :citation_id, :delete_flag, :standard_cited, :standard_parsed,
            :violation_type, :gravity, :nr_exposed, :nr_instances,
            :penalty, :initial_penalty, :current_penalty,
            :contest_date, :final_order_date, :emphasis,
            :abatement_date, :issuance_date
        )
    """)

    for row in rows:
        await session.execute(insert_sql, row)

    return len(rows)


async def ingest_database(
    inspections_path: Path,
    violations_path: Path | None,
    db_url: str,
    since: date,
    batch_size: int = 1000,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Main ingestion loop.

    Returns summary dict with counts.
    """
    # Parse inspections
    logger.info("Parsing inspections from %s (since %s)...", inspections_path, since)
    inspections = list(parse_inspections_csv(inspections_path, since, limit=limit))
    logger.info("Parsed %d construction inspections", len(inspections))

    valid_activity_nrs = {r["activity_nr"] for r in inspections}

    # Parse violations
    violations: list[dict] = []
    if violations_path:
        logger.info("Parsing violations from %s...", violations_path)
        violations = list(parse_violations_csv(violations_path, valid_activity_nrs, limit=limit))
        logger.info("Parsed %d violations linked to loaded inspections", len(violations))

    if dry_run:
        logger.info(
            "[DRY RUN] Would insert %d inspections and %d violations",
            len(inspections),
            len(violations),
        )
        return {
            "inspections_parsed": len(inspections),
            "violations_parsed": len(violations),
            "inspections_loaded": 0,
            "violations_loaded": 0,
            "dry_run": True,
        }

    # Connect and load
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    insp_loaded = 0
    viol_loaded = 0

    async with async_session() as session:
        # Upsert inspections in batches
        for i, batch in enumerate(_chunk(inspections, batch_size)):
            count = await upsert_inspections(session, batch)
            insp_loaded += count
            if (i + 1) % 10 == 0 or i == 0:
                logger.info("  Inspections: %d / %d", insp_loaded, len(inspections))

        await session.commit()
        logger.info("Committed %d inspections", insp_loaded)

        # Insert violations in batches
        if violations:
            for i, batch in enumerate(_chunk(violations, batch_size)):
                batch_anrs = {r["activity_nr"] for r in batch}
                count = await insert_violations(session, batch, batch_anrs)
                viol_loaded += count
                if (i + 1) % 10 == 0 or i == 0:
                    logger.info("  Violations: %d / %d", viol_loaded, len(violations))

            await session.commit()
            logger.info("Committed %d violations", viol_loaded)

    await engine.dispose()

    return {
        "inspections_parsed": len(inspections),
        "violations_parsed": len(violations),
        "inspections_loaded": insp_loaded,
        "violations_loaded": viol_loaded,
        "dry_run": False,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest OSHA enforcement CSV data into the database."
    )
    parser.add_argument(
        "--inspections",
        type=Path,
        required=True,
        help="Path to osha_inspection.csv",
    )
    parser.add_argument(
        "--violations",
        type=Path,
        default=None,
        help="Path to osha_violation.csv (optional — violations loaded only if provided)",
    )
    parser.add_argument(
        "--since",
        type=int,
        default=5,
        help="Only load inspections from the last N years (default: 5)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Rows per database batch (default: 1000)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit total rows parsed (for testing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse CSVs but do not write to the database",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Database URL (default: DATABASE_URL env var)",
    )

    args = parser.parse_args()

    # Validate paths
    if not args.inspections.exists():
        logger.error("Inspections file not found: %s", args.inspections)
        sys.exit(1)
    if args.violations and not args.violations.exists():
        logger.error("Violations file not found: %s", args.violations)
        sys.exit(1)

    db_url = args.db_url or os.environ.get("DATABASE_URL")
    if not db_url and not args.dry_run:
        logger.error("No database URL. Set DATABASE_URL or use --db-url / --dry-run")
        sys.exit(1)

    since_date = date.today() - timedelta(days=args.since * 365)

    result = asyncio.run(
        ingest_database(
            inspections_path=args.inspections,
            violations_path=args.violations,
            db_url=db_url or "",
            since=since_date,
            batch_size=args.batch_size,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    )

    logger.info("Done. %s", result)


if __name__ == "__main__":
    main()
