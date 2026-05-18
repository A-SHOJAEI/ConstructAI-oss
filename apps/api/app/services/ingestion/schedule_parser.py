"""Schedule / CSV parser for construction project schedules."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ScheduleParseResult:
    """Structured result of parsing a schedule file."""

    tasks: list[dict] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    row_count: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _detect_delimiter(sample: str) -> str:
    """Use ``csv.Sniffer`` to guess the delimiter; fall back to comma."""
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        return ","


def _parse_csv(text: str) -> ScheduleParseResult:
    """Parse CSV/TSV content into a ``ScheduleParseResult``."""
    # Take a sample for delimiter detection (first 8 KB is plenty).
    sample = text[:8192]
    delimiter = _detect_delimiter(sample)

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    rows = list(reader)
    if not rows:
        return ScheduleParseResult()

    # First non-empty row is treated as the header.
    columns = [col.strip() for col in rows[0]]
    tasks: list[dict] = []

    for row in rows[1:]:
        # Skip completely blank rows.
        if not any(cell.strip() for cell in row):
            continue
        task: dict = {}
        for idx, col_name in enumerate(columns):
            task[col_name] = row[idx].strip() if idx < len(row) else ""
        tasks.append(task)

    return ScheduleParseResult(
        tasks=tasks,
        columns=columns,
        row_count=len(tasks),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_schedule(file_bytes: bytes, filename: str) -> ScheduleParseResult:
    """Parse a schedule file and return structured task data.

    Currently supports CSV (and TSV) files.  The *filename* parameter is used
    to choose the parsing strategy in case additional formats are supported in
    the future.

    Parameters
    ----------
    file_bytes:
        Raw file content.
    filename:
        Original filename (used for extension-based format detection).
    """
    # Decode bytes to text.
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1")

    result = _parse_csv(text)

    logger.info(
        "schedule_parsed",
        filename=filename,
        columns=result.columns,
        row_count=result.row_count,
    )
    return result
