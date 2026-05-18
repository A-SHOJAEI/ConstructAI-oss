"""Stage 2: Pydantic schema validation."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Field range validators per agent output type
FIELD_VALIDATORS: dict[str, dict[str, dict[str, Any]]] = {
    "cost_estimate": {
        "unit_cost": {"min": 0, "max": 1_000_000},
        "quantity": {"min": 0, "max": 10_000_000},
        "total_cost": {"min": 0, "max": 10_000_000_000},
    },
    "schedule_analysis": {
        "duration_days": {"min": 0, "max": 3650},
        "float_days": {"min": -365, "max": 3650},
    },
    "safety_alert": {
        "confidence": {"min": 0.0, "max": 1.0},
        "severity": {"min": 1, "max": 5},
    },
    "evm_snapshot": {
        "spi": {"min": 0.0, "max": 5.0},
        "cpi": {"min": 0.0, "max": 5.0},
        "percent_complete": {"min": 0.0, "max": 100.0},
    },
}


async def validate_fields(
    parsed_output: dict,
    agent_name: str,
) -> dict:
    """Validate field ranges and types for agent output."""
    errors = []
    validators = FIELD_VALIDATORS.get(agent_name, {})

    for field_name, rules in validators.items():
        if field_name not in parsed_output:
            continue
        value = parsed_output[field_name]
        if not isinstance(value, int | float):
            continue
        if "min" in rules and value < rules["min"]:
            errors.append(
                {
                    "stage": "schema_validate",
                    "field": field_name,
                    "message": (f"{field_name}={value} below min {rules['min']}"),
                    "severity": "error",
                }
            )
        if "max" in rules and value > rules["max"]:
            errors.append(
                {
                    "stage": "schema_validate",
                    "field": field_name,
                    "message": (f"{field_name}={value} above max {rules['max']}"),
                    "severity": "error",
                }
            )

    return {"errors": errors}
