"""Simplified IFC file parser for basic entity extraction.

IFC (Industry Foundation Classes) files are STEP-encoded text files.  This
module performs lightweight regex-based extraction of key entities without
pulling in a full IFC geometry library.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class IfcParseResult:
    """Structured result of a basic IFC parse."""

    entities: list[dict] = field(default_factory=list)
    # Each entity dict: {"id": str, "type": str, "name": str | None, "raw": str}
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Regex patterns for commonly needed IFC entities
# ---------------------------------------------------------------------------

# Matches lines like: #42=IFCPROJECT('0YvctVUKr0kugbFTf53O9L',$,'My Project',...);
_ENTITY_PATTERNS: dict[str, re.Pattern[str]] = {
    "IFCPROJECT": re.compile(r"(#\d+)\s*=\s*IFCPROJECT\(([^;]*)\);", re.IGNORECASE),
    "IFCSITE": re.compile(r"(#\d+)\s*=\s*IFCSITE\(([^;]*)\);", re.IGNORECASE),
    "IFCBUILDING": re.compile(r"(#\d+)\s*=\s*IFCBUILDING\(([^;]*)\);", re.IGNORECASE),
    "IFCBUILDINGSTOREY": re.compile(r"(#\d+)\s*=\s*IFCBUILDINGSTOREY\(([^;]*)\);", re.IGNORECASE),
    "IFCSPACE": re.compile(r"(#\d+)\s*=\s*IFCSPACE\(([^;]*)\);", re.IGNORECASE),
}

# IFC name is typically the third positional argument (after GlobalId and
# OwnerHistory).  Quoted strings use single quotes in STEP encoding.
_NAME_RE = re.compile(r"'([^']*)'")


def _extract_name(args_str: str) -> str | None:
    """Try to pull a human-readable name from the entity arguments string.

    Standard IFC IfcRoot-derived entities follow this positional layout:

    - position 1 (string): GlobalId
    - position 2 (reference, e.g. ``#5``): OwnerHistory
    - position 3 (string): Name
    - position 4 (string): Description

    Since OwnerHistory is a reference (not a quoted string), the first quoted
    string is GlobalId and the *second* quoted string is the Name. If Name is
    unset (``$``) we fall back to Description (the third quoted string).
    """
    matches = _NAME_RE.findall(args_str)
    # Prefer the Name field — second quoted string after GlobalId:
    if len(matches) >= 2:
        name = matches[1]
        if name and name != "$":
            return name
    # Fall back to Description (third quoted string) when Name is unset:
    if len(matches) >= 3:
        desc = matches[2]
        if desc and desc != "$":
            return desc
    return None


# ---------------------------------------------------------------------------
# Header / metadata extraction
# ---------------------------------------------------------------------------

_FILE_DESCRIPTION_RE = re.compile(r"FILE_DESCRIPTION\s*\(([^;]*)\);", re.IGNORECASE | re.DOTALL)
_FILE_NAME_RE = re.compile(r"FILE_NAME\s*\(([^;]*)\);", re.IGNORECASE | re.DOTALL)
_FILE_SCHEMA_RE = re.compile(r"FILE_SCHEMA\s*\(\(([^)]*)\)\)", re.IGNORECASE)


def _extract_metadata(text: str) -> dict:
    """Extract header-section metadata from the IFC file text."""
    meta: dict = {}

    m = _FILE_DESCRIPTION_RE.search(text)
    if m:
        meta["file_description"] = m.group(1).strip()

    m = _FILE_NAME_RE.search(text)
    if m:
        names = _NAME_RE.findall(m.group(1))
        if names:
            meta["file_name"] = names[0]

    m = _FILE_SCHEMA_RE.search(text)
    if m:
        schemas = _NAME_RE.findall(m.group(1))
        meta["schema"] = schemas if schemas else []

    return meta


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_ifc(file_bytes: bytes) -> IfcParseResult:
    """Parse an IFC file from raw bytes and extract key entities.

    This is a *simplified* parser suitable for indexing and search.  It does
    not resolve references or build a spatial hierarchy.
    """
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # Some IFC files use latin-1.
        text = file_bytes.decode("latin-1")

    metadata = _extract_metadata(text)
    entities: list[dict] = []

    for entity_type, pattern in _ENTITY_PATTERNS.items():
        for match in pattern.finditer(text):
            entity_id = match.group(1)
            args_str = match.group(2)
            name = _extract_name(args_str)
            entities.append(
                {
                    "id": entity_id,
                    "type": entity_type,
                    "name": name,
                    "raw": match.group(0),
                }
            )

    result = IfcParseResult(entities=entities, metadata=metadata)
    logger.info(
        "ifc_parsed",
        entity_count=len(entities),
        entity_types=[e["type"] for e in entities],
        schema=metadata.get("schema"),
    )
    return result
