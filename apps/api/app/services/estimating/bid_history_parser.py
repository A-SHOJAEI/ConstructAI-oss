"""CSV parser for historical bid data import.

Handles encoding detection, delimiter sniffing, flexible column mapping,
and per-row error reporting. Each parsed row becomes a BidOpportunity
dict + BidDecision dict.
"""

from __future__ import annotations

import contextlib
import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column mapping — flexible name matching
# ---------------------------------------------------------------------------

# Maps canonical field name to list of acceptable CSV header variants
_COLUMN_ALIASES: dict[str, list[str]] = {
    "name": ["name", "project_name", "project name", "opportunity", "opportunity_name"],
    "project_type": ["project_type", "project type", "type", "sector"],
    "delivery_method": ["delivery_method", "delivery method", "method", "procurement_method"],
    "estimated_value": ["estimated_value", "estimated value", "value", "contract_value", "amount"],
    "location": ["location", "city", "address", "city_state", "city/state"],
    "outcome": ["outcome", "result", "won_lost", "won/lost", "status"],
    "bid_date": ["bid_date", "bid date", "date", "bid_due_date", "due_date"],
    "owner_name": ["owner_name", "owner name", "owner", "client", "client_name"],
    "actual_margin": ["actual_margin", "actual margin", "margin", "profit_margin"],
    "competitors": ["competitors", "num_competitors", "number_of_competitors", "competition"],
}

_VALID_OUTCOMES = {"won", "lost"}
_VALID_METHODS = {"hard_bid", "negotiated", "design_build", "cmar", "ipd"}


@dataclass
class CSVRowError:
    row: int
    field: str | None
    message: str


@dataclass
class ParseResult:
    opportunities: list[dict] = field(default_factory=list)
    errors: list[CSVRowError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    row_count: int = 0


def _decode_content(file_content: bytes) -> str:
    """Decode file bytes, trying UTF-8 first then latin-1."""
    try:
        return file_content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return file_content.decode("latin-1")


def _map_headers(headers: list[str]) -> dict[str, int]:
    """Map canonical field names to column indices."""
    mapping: dict[str, int] = {}
    normalized = [h.strip().lower().replace("-", "_") for h in headers]

    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            alias_norm = alias.lower().replace("-", "_")
            if alias_norm in normalized:
                mapping[canonical] = normalized.index(alias_norm)
                break
    return mapping


def _parse_value(raw: str) -> float | None:
    """Parse a monetary value, stripping $ and commas."""
    if not raw:
        return None
    cleaned = raw.strip().replace("$", "").replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(raw: str) -> str | None:
    """Parse a date string to ISO format."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y"):
        try:
            return date(
                *__import__("datetime").datetime.strptime(raw, fmt).timetuple()[:3]
            ).isoformat()
        except ValueError:
            continue
    return None


async def parse_bid_history_csv(file_content: bytes, org_id: str) -> ParseResult:
    """Parse a CSV file of historical bid data.

    Args:
        file_content: Raw file bytes.
        org_id: Organization ID to associate records with.

    Returns:
        ParseResult with opportunities, errors, warnings, and row count.
    """
    result = ParseResult()
    text = _decode_content(file_content)

    # Sniff delimiter
    try:
        sample = text[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel  # Default to comma

    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)

    if len(rows) < 2:
        result.warnings.append("File has no data rows")
        return result

    # Map headers
    header_row = rows[0]
    col_map = _map_headers(header_row)

    if "name" not in col_map:
        result.errors.append(
            CSVRowError(row=0, field="name", message="Required column 'name' not found in headers")
        )
        return result

    if "outcome" not in col_map:
        result.warnings.append(
            "No 'outcome' column found — records will be imported without win/loss data"
        )

    for row_idx, row in enumerate(rows[1:], start=2):
        # Skip blank rows
        if not any(cell.strip() for cell in row):
            continue

        result.row_count += 1
        row_errors = []

        # Required: name
        name_idx = col_map["name"]
        if name_idx >= len(row) or not row[name_idx].strip():
            row_errors.append(
                CSVRowError(row=row_idx, field="name", message="Missing project name")
            )
            result.errors.extend(row_errors)
            continue

        name = row[name_idx].strip()

        # Optional fields (bind `row` via default arg to silence B023 loop-capture warning)
        def _get(field_name: str, row: list[str] = row) -> str:
            idx = col_map.get(field_name)
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        project_type = _get("project_type") or None
        delivery_method = _get("delivery_method")
        if delivery_method:
            dm_normalized = delivery_method.lower().replace(" ", "_").replace("-", "_")
            if dm_normalized in _VALID_METHODS:
                delivery_method = dm_normalized
            else:
                row_errors.append(
                    CSVRowError(
                        row=row_idx,
                        field="delivery_method",
                        message=f"Unknown delivery method: {delivery_method}",
                    )
                )
                delivery_method = ""

        estimated_value = _parse_value(_get("estimated_value"))
        location = _get("location") or None
        owner_name = _get("owner_name") or None
        actual_margin = _parse_value(_get("actual_margin"))
        bid_date = _parse_date(_get("bid_date"))

        competitors_raw = _get("competitors")
        competitors = None
        if competitors_raw:
            with contextlib.suppress(ValueError):
                competitors = int(competitors_raw)

        # Outcome
        outcome_raw = _get("outcome")
        outcome = None
        human_decision = None
        status = "evaluating"
        if outcome_raw:
            outcome_norm = outcome_raw.lower().strip()
            if outcome_norm in _VALID_OUTCOMES:
                outcome = outcome_norm
                status = outcome_norm
                human_decision = "pursue"  # They bid on it, so they pursued
            else:
                row_errors.append(
                    CSVRowError(
                        row=row_idx,
                        field="outcome",
                        message=f"Invalid outcome: {outcome_raw} (expected 'won' or 'lost')",
                    )
                )

        opportunity: dict[str, Any] = {
            "org_id": org_id,
            "name": name,
            "owner_name": owner_name,
            "project_type": project_type,
            "delivery_method": delivery_method,
            "estimated_value": estimated_value,
            "location": location,
            "status": status,
            "outcome": outcome,
            "actual_margin": actual_margin,
            "metadata_json": {},
        }

        if bid_date:
            opportunity["bid_due_date"] = bid_date
        if competitors is not None:
            opportunity["metadata_json"]["competitors"] = competitors

        decision: dict[str, Any] = {
            "ai_score": 0,
            "ai_recommendation": None,
            "ai_reasoning": None,
            "human_decision": human_decision,
            "human_notes": f"Imported from CSV (row {row_idx})",
            "factor_scores": {},
            "win_probability": None,
        }

        result.opportunities.append(
            {
                "opportunity": opportunity,
                "decision": decision,
            }
        )
        result.errors.extend(row_errors)

    if not result.opportunities:
        result.warnings.append("No valid records found in CSV")

    return result
