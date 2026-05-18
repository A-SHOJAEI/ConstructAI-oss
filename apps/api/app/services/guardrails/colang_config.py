"""NeMo Guardrails Colang topic enforcement configuration."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Colang rules for topic enforcement
COLANG_CONFIG = {
    "models": [],
    "rails": {
        "input": {
            "flows": [
                {
                    "name": "construction_topic_check",
                    "description": (
                        "Ensure queries are related to construction project management"
                    ),
                    "allowed_topics": [
                        "construction",
                        "project management",
                        "cost estimation",
                        "scheduling",
                        "safety",
                        "quality control",
                        "procurement",
                        "logistics",
                        "document management",
                        "compliance",
                        "reporting",
                    ],
                },
            ],
        },
        "output": {
            "flows": [
                {
                    "name": "no_pii_in_reports",
                    "description": ("Prevent PII from appearing in generated reports"),
                },
                {
                    "name": "no_legal_advice",
                    "description": ("Prevent system from giving legal advice"),
                },
            ],
        },
    },
}


def get_colang_config() -> dict:
    """Get the Colang configuration for NeMo Guardrails."""
    return COLANG_CONFIG


async def check_topic_allowed(query: str) -> dict:
    """Check if a query is within allowed topics.

    In production, this uses NeMo Guardrails.
    For now, uses keyword matching.
    """
    allowed_keywords = {
        "construction",
        "project",
        "estimate",
        "cost",
        "schedule",
        "safety",
        "quality",
        "procurement",
        "logistics",
        "document",
        "compliance",
        "report",
        "bid",
        "submittal",
        "rfi",
        "inspection",
        "defect",
        "equipment",
        "crew",
        "material",
        "concrete",
        "steel",
        "masonry",
        "electrical",
        "plumbing",
        "hvac",
        "excavation",
        "foundation",
        "framing",
    }

    query_lower = query.lower()
    matched = [kw for kw in allowed_keywords if kw in query_lower]

    if matched:
        return {
            "allowed": True,
            "matched_topics": matched,
        }
    return {
        "allowed": False,
        "message": "Query does not appear to be construction-related",
    }
