"""Extract facts from conversations and agent outputs."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Patterns for extracting different fact types
FACT_PATTERNS = {
    "budget": [
        re.compile(
            r"budget\s+(?:is|of|set\s+to)\s+\$?([\d,]+(?:\.\d{2})?)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\$?([\d,]+(?:\.\d{2})?)\s+budget",
            re.IGNORECASE,
        ),
    ],
    "schedule": [
        re.compile(
            r"(?:deadline|due\s+date|completion)\s+(?:is|by)\s+"
            r"(\d{4}-\d{2}-\d{2}|\w+\s+\d{1,2},?\s+\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"(\d+)\s+(?:day|week|month)s?\s+(?:duration|timeline)",
            re.IGNORECASE,
        ),
    ],
    "decision": [
        re.compile(
            r"(?:decided|decision|agreed)\s+(?:to|that)\s+(.{10,100})",
            re.IGNORECASE,
        ),
    ],
    "constraint": [
        re.compile(
            r"(?:constraint|limitation|restriction):\s*(.{10,100})",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:must|shall|cannot|must\s+not)\s+(.{10,80})",
            re.IGNORECASE,
        ),
    ],
    "risk": [
        re.compile(
            r"(?:risk|concern|worry)(?:\s+(?:is|about|of))?\s*:?\s*" r"(.{10,100})",
            re.IGNORECASE,
        ),
    ],
}


async def extract_facts(
    text: str,
    source_type: str = "conversation",
) -> list[dict]:
    """Extract facts from text using pattern matching.

    Returns list of dicts with fact_type, fact_text,
    and confidence.
    """
    facts = []

    for fact_type, patterns in FACT_PATTERNS.items():
        for pattern in patterns:
            matches = pattern.finditer(text)
            for match in matches:
                fact_text = match.group(1).strip()
                if len(fact_text) < 5:
                    continue
                facts.append(
                    {
                        "fact_type": fact_type,
                        "fact_text": fact_text,
                        "confidence": 0.75,
                        "source_type": source_type,
                        "match_span": match.span(),
                    }
                )

    logger.info("Extracted %d facts from text", len(facts))
    return facts


async def extract_facts_from_agent_output(
    agent_name: str,
    output: dict,
) -> list[dict]:
    """Extract facts from structured agent output."""
    facts = []

    # Cost-related facts from estimating agent
    if agent_name == "estimating_agent" and "total_cost" in output:
        facts.append(
            {
                "fact_type": "budget",
                "fact_text": (f"Estimated total cost: ${output['total_cost']:,.2f}"),
                "confidence": 0.85,
                "source_type": "agent_output",
            }
        )

    # Schedule facts from scheduling agent
    if agent_name == "scheduling_agent" and "total_duration" in output:
        facts.append(
            {
                "fact_type": "schedule",
                "fact_text": (f"Estimated duration: {output['total_duration']} days"),
                "confidence": 0.85,
                "source_type": "agent_output",
            }
        )

    # Risk facts from controls agent
    if agent_name == "controls_agent" and "risk_drivers" in output:
        for driver in output.get("risk_drivers", [])[:3]:
            name = driver.get("activity", "unknown")
            facts.append(
                {
                    "fact_type": "risk",
                    "fact_text": (f"Schedule risk driver: {name}"),
                    "confidence": 0.80,
                    "source_type": "agent_output",
                }
            )

    return facts
