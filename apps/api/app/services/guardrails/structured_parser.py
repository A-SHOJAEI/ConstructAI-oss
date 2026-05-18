"""Stage 1: Structured output parsing."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def parse_output(raw_output: str, agent_name: str) -> dict:
    """Parse raw agent output into structured dict.

    Attempts JSON parsing first, then falls back to
    key-value extraction for simple outputs.
    """
    if not raw_output or not raw_output.strip():
        return {"error": "Empty output", "data": None}

    # Try JSON parsing
    try:
        data = json.loads(raw_output)
        if isinstance(data, dict):
            return {"data": data, "error": None}
        return {"data": {"value": data}, "error": None}
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from markdown code blocks
    stripped = raw_output.strip()
    if "```json" in stripped:
        start = stripped.index("```json") + 7
        end = stripped.index("```", start)
        try:
            data = json.loads(stripped[start:end].strip())
            return {"data": data, "error": None}
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: wrap as text
    return {
        "data": {"raw_text": raw_output, "format": "unstructured"},
        "error": None,
    }
