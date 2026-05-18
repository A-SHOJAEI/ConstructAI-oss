#!/usr/bin/env python3
"""Seed compliance checklists from curated JSON data.

Usage:
    python scripts/seed_compliance_checklists.py
    python scripts/seed_compliance_checklists.py --dry-run
    python scripts/seed_compliance_checklists.py --db-url postgresql+asyncpg://...
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SEED_FILE = Path(__file__).resolve().parents[1] / "data" / "seed" / "compliance_checklists_v1.json"


def load_seed_data() -> list[dict]:
    """Load compliance checklists from seed JSON."""
    if not SEED_FILE.exists():
        logger.error("Seed file not found: %s", SEED_FILE)
        sys.exit(1)

    with open(SEED_FILE) as f:
        data = json.load(f)

    if isinstance(data, dict) and "checklists" in data:
        return data["checklists"]
    if isinstance(data, list):
        return data

    logger.error("Unexpected JSON format in %s", SEED_FILE)
    sys.exit(1)


def validate_checklists(checks: list[dict]) -> list[str]:
    """Validate seed data and return list of warnings."""
    warnings = []
    required_keys = {
        "category",
        "check_id",
        "description",
        "standard_reference",
        "severity",
        "applicable_project_types",
        "applicable_phases",
        "frequency",
    }
    valid_categories = {"osha_safety", "ibc_inspection", "environmental_swppp", "quality_control"}
    valid_severities = {"critical", "major", "minor"}
    ids_seen: set[str] = set()

    for i, check in enumerate(checks):
        missing = required_keys - set(check.keys())
        if missing:
            warnings.append(f"Check {i}: missing keys {missing}")

        cid = check.get("check_id", "")
        if cid in ids_seen:
            warnings.append(f"Check {i}: duplicate check_id '{cid}'")
        ids_seen.add(cid)

        cat = check.get("category", "")
        if cat not in valid_categories:
            warnings.append(f"Check {i} ({cid}): invalid category '{cat}'")

        sev = check.get("severity", "")
        if sev not in valid_severities:
            warnings.append(f"Check {i} ({cid}): invalid severity '{sev}'")

    return warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed compliance checklists")
    parser.add_argument("--dry-run", action="store_true", help="Validate only")
    parser.add_argument("--db-url", default=None)
    args = parser.parse_args()

    checks = load_seed_data()
    logger.info("Loaded %d compliance checks from seed file", len(checks))

    warnings = validate_checklists(checks)
    if warnings:
        for w in warnings:
            logger.warning(w)

    # Summary by category
    cats: dict[str, int] = {}
    sevs: dict[str, int] = {}
    for c in checks:
        cat = c.get("category", "unknown")
        cats[cat] = cats.get(cat, 0) + 1
        sev = c.get("severity", "unknown")
        sevs[sev] = sevs.get(sev, 0) + 1

    for cat, count in sorted(cats.items()):
        logger.info("  %-25s %3d checks", cat, count)
    logger.info("  Severity: %s", {k: v for k, v in sorted(sevs.items())})

    if args.dry_run:
        logger.info("Dry run complete, %d checks validated", len(checks))
        return

    logger.info("Compliance checklists validated. Use the compliance checker service to load.")


if __name__ == "__main__":
    main()
