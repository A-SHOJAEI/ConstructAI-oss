"""OSHA enforcement data lookup service.

Provides fuzzy contractor lookup, violation statistics by geography/standard,
and contractor OSHA history for vendor scoring integration.
"""

from __future__ import annotations

import difflib
import logging
import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Violation type labels
VIOLATION_TYPE_LABELS: dict[str, str] = {
    "W": "willful",
    "R": "repeat",
    "S": "serious",
    "O": "other",
}

_NAME_STOP_WORDS = {"the", "a", "an"}


# ---------------------------------------------------------------------------
# Pure helpers (no DB)
# ---------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    "ABC CONST., INC." -> "abc const inc"
    """
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def parse_standard(raw: str) -> str | None:
    """Convert OSHA raw code to dotted form.

    OSHA stores the section number zero-padded to 4 digits, but the
    canonical citation form (used in legal references and the published
    CFR) drops the leading zeros. ``19260501`` is therefore CFR
    ``1926.501`` (fall protection), not ``1926.0501``.

    "19260501"  -> "1926.501"
    "19100134"  -> "1910.134"
    "1926"      -> None  (too short)
    ""          -> None
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
    """Return True if record is in the construction sector."""
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


def _first_token(normalized: str) -> str:
    """Extract first meaningful word for DB prefix pre-filter.

    Skips common stop words: "the", "a", "an".
    """
    for token in normalized.split():
        if token not in _NAME_STOP_WORDS and len(token) >= 2:
            return token
    # Fallback: first 3 chars
    return normalized[:3] if len(normalized) >= 3 else normalized


# ---------------------------------------------------------------------------
# DB lookups
# ---------------------------------------------------------------------------


async def lookup_contractor(
    db: AsyncSession,
    company_name: str,
    state: str | None = None,
    threshold: float = 0.6,
    limit: int = 10,
) -> list[dict]:
    """Fuzzy match company_name against OSHA inspections.

    Two-phase approach for performance on 800K+ rows:
    1. DB pre-filter using first token prefix + state
    2. Python-side difflib.SequenceMatcher scoring
    """
    query_norm = normalize_name(company_name)
    if not query_norm:
        return []

    prefix = _first_token(query_norm)

    # Phase 1: DB pre-filter — pull at most 500 candidates
    sql = text("""
        SELECT activity_nr, establishment_name, name_normalized,
               site_city, site_state, open_date, close_date,
               total_penalty, insp_type
        FROM osha_inspections
        WHERE name_normalized LIKE :prefix || '%'
          AND (:state::text IS NULL OR site_state = :state)
        ORDER BY open_date DESC NULLS LAST
        LIMIT 500
    """)
    result = await db.execute(sql, {"prefix": prefix, "state": state})
    candidates = result.mappings().all()

    if not candidates:
        return []

    # Phase 2: Python fuzzy scoring
    scored = []
    for row in candidates:
        ratio = difflib.SequenceMatcher(None, query_norm, row["name_normalized"]).ratio()
        if ratio >= threshold:
            scored.append(
                {
                    "activity_nr": row["activity_nr"],
                    "establishment_name": row["establishment_name"],
                    "site_city": row["site_city"],
                    "site_state": row["site_state"],
                    "match_score": round(ratio, 4),
                    "open_date": row["open_date"].isoformat() if row["open_date"] else None,
                    "close_date": row["close_date"].isoformat() if row["close_date"] else None,
                    "total_penalty": float(row["total_penalty"]) if row["total_penalty"] else 0.0,
                    "insp_type": row["insp_type"],
                }
            )

    scored.sort(key=lambda x: x["match_score"], reverse=True)
    return scored[:limit]


async def get_violation_stats(
    db: AsyncSession,
    state: str | None = None,
    naics_prefix: str | None = None,
    since_years: int = 5,
) -> dict:
    """Aggregate violation counts by standard for a state and/or NAICS filter."""
    since_date = date.today() - timedelta(days=since_years * 365)

    # Build the query with joins and filters
    sql = text("""
        SELECT
            v.standard_parsed AS standard,
            COUNT(*) AS count,
            COUNT(*) FILTER (WHERE v.violation_type = 'W') AS willful_count,
            COUNT(*) FILTER (WHERE v.violation_type = 'R') AS repeat_count,
            COALESCE(SUM(v.penalty), 0) AS total_penalty
        FROM osha_violations v
        JOIN osha_inspections i ON v.activity_nr = i.activity_nr
        WHERE v.standard_parsed IS NOT NULL
          AND i.open_date >= :since_date
          AND (:state::text IS NULL OR i.site_state = :state)
          AND (:naics_prefix::text IS NULL OR i.naics_code LIKE :naics_prefix || '%')
        GROUP BY v.standard_parsed
        ORDER BY count DESC
        LIMIT 25
    """)

    result = await db.execute(
        sql,
        {
            "since_date": since_date,
            "state": state,
            "naics_prefix": naics_prefix,
        },
    )
    rows = result.mappings().all()

    # Get totals
    totals_sql = text("""
        SELECT
            COUNT(DISTINCT i.activity_nr) AS total_inspections,
            COUNT(v.id) AS total_violations
        FROM osha_inspections i
        LEFT JOIN osha_violations v ON v.activity_nr = i.activity_nr
        WHERE i.open_date >= :since_date
          AND (:state::text IS NULL OR i.site_state = :state)
          AND (:naics_prefix::text IS NULL OR i.naics_code LIKE :naics_prefix || '%')
    """)
    totals_result = await db.execute(
        totals_sql,
        {
            "since_date": since_date,
            "state": state,
            "naics_prefix": naics_prefix,
        },
    )
    totals = totals_result.mappings().first()

    top_standards = []
    for row in rows:
        top_standards.append(
            {
                "standard": row["standard"],
                "title": None,  # Enriched by caller from OSHA_STANDARDS
                "category": None,
                "count": row["count"],
                "willful_count": row["willful_count"],
                "repeat_count": row["repeat_count"],
                "total_penalty": float(row["total_penalty"]),
            }
        )

    return {
        "state": state,
        "naics_prefix": naics_prefix,
        "since_date": since_date.isoformat(),
        "total_inspections": totals["total_inspections"] if totals else 0,
        "total_violations": totals["total_violations"] if totals else 0,
        "top_standards": top_standards,
    }


async def get_contractor_osha_history(
    db: AsyncSession,
    company_name: str,
    state: str | None = None,
    since_years: int = 3,
) -> dict:
    """Return structured OSHA history for a named contractor.

    Used by vendor_manager integration. Calls lookup_contractor for
    the best fuzzy match, then aggregates that contractor's violations.
    """
    empty: dict[str, Any] = {
        "matched_name": None,
        "match_score": 0.0,
        "inspection_count": 0,
        "violation_count": 0,
        "willful_count": 0,
        "repeat_count": 0,
        "total_penalty": 0.0,
        "top_cited_standards": [],
        "has_recent_willful_repeat": False,
    }

    matches = await lookup_contractor(db, company_name, state=state, threshold=0.6, limit=1)
    if not matches:
        return empty

    best = matches[0]
    matched_name = best["establishment_name"]

    # Find all inspections for this contractor (by normalized name match)
    name_norm = normalize_name(matched_name)
    since_date = date.today() - timedelta(days=since_years * 365)

    # Get all activity_nrs for this contractor
    insp_sql = text("""
        SELECT activity_nr, total_penalty
        FROM osha_inspections
        WHERE name_normalized = :name_norm
          AND (:state::text IS NULL OR site_state = :state)
          AND open_date >= :since_date
    """)
    insp_result = await db.execute(
        insp_sql,
        {
            "name_norm": name_norm,
            "state": state,
            "since_date": since_date,
        },
    )
    inspections = insp_result.mappings().all()

    if not inspections:
        return {**empty, "matched_name": matched_name, "match_score": best["match_score"]}

    activity_nrs = [r["activity_nr"] for r in inspections]
    total_penalty = sum(float(r["total_penalty"] or 0) for r in inspections)

    # Get violation aggregates
    viol_sql = text("""
        SELECT
            COUNT(*) AS violation_count,
            COUNT(*) FILTER (WHERE violation_type = 'W') AS willful_count,
            COUNT(*) FILTER (WHERE violation_type = 'R') AS repeat_count
        FROM osha_violations
        WHERE activity_nr = ANY(:activity_nrs)
    """)
    viol_result = await db.execute(viol_sql, {"activity_nrs": activity_nrs})
    viol = viol_result.mappings().first()

    # Get top cited standards
    std_sql = text("""
        SELECT standard_parsed, COUNT(*) AS cnt
        FROM osha_violations
        WHERE activity_nr = ANY(:activity_nrs)
          AND standard_parsed IS NOT NULL
        GROUP BY standard_parsed
        ORDER BY cnt DESC
        LIMIT 5
    """)
    std_result = await db.execute(std_sql, {"activity_nrs": activity_nrs})
    top_standards = [r["standard_parsed"] for r in std_result.mappings().all()]

    willful = viol["willful_count"] if viol else 0
    repeat = viol["repeat_count"] if viol else 0

    return {
        "matched_name": matched_name,
        "match_score": best["match_score"],
        "inspection_count": len(inspections),
        "violation_count": viol["violation_count"] if viol else 0,
        "willful_count": willful,
        "repeat_count": repeat,
        "total_penalty": total_penalty,
        "top_cited_standards": top_standards,
        "has_recent_willful_repeat": (willful + repeat) > 0,
    }
