"""Tests for the guardrail stage helpers (parser, schema validator,
routing).

These three modules form the spine of the guardrail pipeline. Tests
pin the documented contracts so a refactor can't quietly weaken any
of them — particularly the safety/RFI "always human review" invariant.
"""

from __future__ import annotations

import pytest

from app.services.guardrails.routing_decision import (
    DEFAULT_THRESHOLDS,
    ROUTING_THRESHOLDS,
    decide_route,
)
from app.services.guardrails.schema_validator import validate_fields
from app.services.guardrails.structured_parser import parse_output

# =========================================================================
# structured_parser.parse_output
# =========================================================================


async def test_parse_output_empty_returns_error():
    out = await parse_output("", "any")
    assert out["data"] is None
    assert out["error"] == "Empty output"


async def test_parse_output_whitespace_only_returns_error():
    out = await parse_output("   \n\t  ", "any")
    assert out["error"] == "Empty output"


async def test_parse_output_clean_json_object():
    out = await parse_output('{"x": 1}', "any")
    assert out["error"] is None
    assert out["data"] == {"x": 1}


async def test_parse_output_json_scalar_wrapped_in_value_key():
    """Top-level JSON that isn't an object gets wrapped under
    ``value`` so downstream code always sees a dict."""
    out = await parse_output("42", "any")
    assert out["data"] == {"value": 42}


async def test_parse_output_extracts_json_from_markdown_fence():
    raw = '```json\n{"a": "b"}\n```'
    out = await parse_output(raw, "any")
    assert out["data"] == {"a": "b"}


async def test_parse_output_falls_back_to_unstructured_text():
    """Non-JSON, non-fenced output is preserved as raw text under a
    documented format flag."""
    out = await parse_output("This is just narrative text.", "any")
    assert out["data"]["format"] == "unstructured"
    assert out["data"]["raw_text"] == "This is just narrative text."


async def test_parse_output_malformed_json_in_fence_falls_through():
    raw = "```json\n{not even close\n```"
    out = await parse_output(raw, "any")
    # Fence parse failed → falls through to raw-text path.
    assert out["data"]["format"] == "unstructured"


# =========================================================================
# schema_validator.validate_fields
# =========================================================================


async def test_validate_fields_unknown_agent_no_errors():
    """If the agent isn't in the validator map, every field passes."""
    out = await validate_fields({"unit_cost": -100}, "ghost_agent")
    assert out["errors"] == []


async def test_validate_fields_negative_unit_cost_rejected():
    out = await validate_fields({"unit_cost": -50}, "cost_estimate")
    errs = out["errors"]
    assert len(errs) == 1
    assert "unit_cost" in errs[0]["message"]
    assert errs[0]["severity"] == "error"


async def test_validate_fields_above_max_rejected():
    out = await validate_fields({"unit_cost": 2_000_000}, "cost_estimate")
    errs = out["errors"]
    assert any("above max" in e["message"] for e in errs)


async def test_validate_fields_within_range_accepted():
    out = await validate_fields(
        {"unit_cost": 200, "quantity": 100, "total_cost": 20_000},
        "cost_estimate",
    )
    assert out["errors"] == []


async def test_validate_fields_skips_non_numeric_values():
    """A string in a numeric field doesn't crash — it's just skipped
    (the type-check is the upstream schema's job)."""
    out = await validate_fields({"unit_cost": "expensive"}, "cost_estimate")
    assert out["errors"] == []


async def test_validate_fields_evm_spi_cpi_pinned():
    """SPI/CPI in [0, 5] is the canonical EVM range; out-of-range
    values are operator-side errors that must be flagged."""
    out_lo = await validate_fields({"spi": -0.5}, "evm_snapshot")
    out_hi = await validate_fields({"spi": 6.0}, "evm_snapshot")
    out_ok = await validate_fields({"spi": 1.05}, "evm_snapshot")
    assert out_lo["errors"]
    assert out_hi["errors"]
    assert out_ok["errors"] == []


async def test_validate_fields_safety_alert_severity_pinned_one_to_five():
    out_low = await validate_fields({"severity": 0}, "safety_alert")
    out_hi = await validate_fields({"severity": 6}, "safety_alert")
    assert out_low["errors"]
    assert out_hi["errors"]


# =========================================================================
# routing_decision.decide_route
# =========================================================================


def test_decide_route_validation_error_forces_expert_escalation():
    """Any error-level validation issue routes to the human expert,
    regardless of confidence."""
    decision = decide_route(
        confidence=0.99,
        agent_name="cost_estimate",
        validation_errors=[{"severity": "error", "message": "x"}],
    )
    assert decision == "expert_escalation"


def test_decide_route_warnings_do_not_escalate():
    """``warning`` severity is advisory — does not force escalation."""
    decision = decide_route(
        confidence=0.99,
        agent_name="cost_estimate",
        validation_errors=[{"severity": "warning", "message": "soft"}],
    )
    # Above auto threshold (0.85) and no errors → auto.
    assert decision == "auto_approve"


def test_decide_route_safety_alert_always_human_review():
    """Security invariant (C-11): safety alerts are never auto-approved
    regardless of confidence."""
    decision = decide_route(0.999, "safety_alert", [])
    assert decision == "human_review"


def test_decide_route_change_order_always_human_review():
    decision = decide_route(0.999, "change_order_impact", [])
    assert decision == "human_review"


def test_decide_route_rfi_draft_always_human_review():
    decision = decide_route(0.999, "rfi_draft", [])
    assert decision == "human_review"


def test_decide_route_unknown_agent_defaults_to_safe_path():
    """L-12: unknown agent types default to human review (never
    auto-approved). The default thresholds carry auto=None."""
    decision = decide_route(0.999, "made_up_agent", [])
    assert decision == "human_review"


def test_decide_route_above_auto_threshold():
    decision = decide_route(0.95, "cost_estimate", [])
    assert decision == "auto_approve"


def test_decide_route_between_thresholds_human_review():
    decision = decide_route(0.70, "cost_estimate", [])
    assert decision == "human_review"


def test_decide_route_below_human_threshold_expert():
    decision = decide_route(0.30, "cost_estimate", [])
    assert decision == "expert_escalation"


def test_decide_route_at_auto_threshold_inclusive():
    """The threshold is inclusive — exactly at the boundary auto-approves."""
    decision = decide_route(0.85, "cost_estimate", [])
    assert decision == "auto_approve"


@pytest.mark.parametrize(
    "agent",
    [
        "document_classification",
        "cost_estimate",
        "schedule_analysis",
        "daily_report",
        "evm_snapshot",
        "quality_inspection",
    ],
)
def test_routing_thresholds_documented_agents_pinned(agent):
    """Pin the documented agent set — protects against a refactor
    silently dropping or renaming an agent."""
    assert agent in ROUTING_THRESHOLDS
    assert "auto" in ROUTING_THRESHOLDS[agent]
    assert "human" in ROUTING_THRESHOLDS[agent]


def test_default_thresholds_auto_is_none():
    """Pin the L-12 invariant directly: the unknown-agent default must
    NEVER auto-approve."""
    assert DEFAULT_THRESHOLDS["auto"] is None
