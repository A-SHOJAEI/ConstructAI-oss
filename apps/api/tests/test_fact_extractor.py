"""Tests for the conversation/agent-output fact extractor.

The extractor pulls structured facts (budget / schedule / decision /
constraint / risk) from free-form text and structured agent output.
"""

from __future__ import annotations

import pytest

from app.services.memory.fact_extractor import (
    FACT_PATTERNS,
    extract_facts,
    extract_facts_from_agent_output,
)

# =========================================================================
# FACT_PATTERNS — pattern catalog
# =========================================================================


def test_fact_patterns_canonical_types():
    """Pin the documented fact types — refactor must not silently
    drop one."""
    expected = {"budget", "schedule", "decision", "constraint", "risk"}
    assert set(FACT_PATTERNS.keys()) == expected


def test_each_fact_type_has_at_least_one_pattern():
    for fact_type, patterns in FACT_PATTERNS.items():
        assert patterns, f"{fact_type} has no patterns"


# =========================================================================
# extract_facts — text patterns
# =========================================================================


@pytest.mark.asyncio
async def test_extract_facts_empty_text():
    out = await extract_facts("")
    assert out == []


@pytest.mark.asyncio
async def test_extract_facts_budget_with_dollar():
    out = await extract_facts("The project budget is $1,500,000")
    budget_facts = [f for f in out if f["fact_type"] == "budget"]
    assert len(budget_facts) >= 1
    assert "1,500,000" in budget_facts[0]["fact_text"]


@pytest.mark.asyncio
async def test_extract_facts_budget_word_first():
    """Pattern matches "budget set to N" with $ optional."""
    out = await extract_facts("budget set to 250000")
    budget_facts = [f for f in out if f["fact_type"] == "budget"]
    assert len(budget_facts) >= 1


@pytest.mark.asyncio
async def test_extract_facts_budget_post_position():
    """Pattern 2 matches "$N budget"."""
    out = await extract_facts("Allocate $750,000 budget for foundations")
    budget_facts = [f for f in out if f["fact_type"] == "budget"]
    assert len(budget_facts) >= 1


@pytest.mark.asyncio
async def test_extract_facts_schedule_iso_date():
    out = await extract_facts("Project deadline is 2026-12-31")
    schedule_facts = [f for f in out if f["fact_type"] == "schedule"]
    assert len(schedule_facts) >= 1
    assert "2026-12-31" in schedule_facts[0]["fact_text"]


@pytest.mark.asyncio
async def test_extract_facts_decision():
    out = await extract_facts("We decided to use 4000 psi concrete mix for slab")
    decision_facts = [f for f in out if f["fact_type"] == "decision"]
    assert len(decision_facts) >= 1
    assert "4000 psi" in decision_facts[0]["fact_text"]


@pytest.mark.asyncio
async def test_extract_facts_constraint():
    out = await extract_facts("Constraint: All work must be completed before Friday morning")
    constraint_facts = [f for f in out if f["fact_type"] == "constraint"]
    assert len(constraint_facts) >= 1


@pytest.mark.asyncio
async def test_extract_facts_must_pattern():
    out = await extract_facts("Workers must wear hard hats at all times when on site")
    constraint_facts = [f for f in out if f["fact_type"] == "constraint"]
    assert len(constraint_facts) >= 1


@pytest.mark.asyncio
async def test_extract_facts_risk():
    out = await extract_facts("Risk: weather delays affecting concrete pour schedule")
    risk_facts = [f for f in out if f["fact_type"] == "risk"]
    assert len(risk_facts) >= 1


@pytest.mark.asyncio
async def test_extract_facts_too_short_text_skipped():
    """Extracted text < 5 chars must be filtered (decision/constraint/risk
    need at least 10 chars per regex; pattern guard handles boundary)."""
    out = await extract_facts("decided to x")  # captured "x" too short
    assert all(len(f["fact_text"]) >= 5 for f in out)


@pytest.mark.asyncio
async def test_extract_facts_carries_source_type():
    out = await extract_facts(
        "Risk: weather delays may impact concrete pour", source_type="meeting_notes"
    )
    for fact in out:
        assert fact["source_type"] == "meeting_notes"


@pytest.mark.asyncio
async def test_extract_facts_default_source_type_conversation():
    out = await extract_facts("Risk: weather delays may impact concrete pour")
    if out:
        assert out[0]["source_type"] == "conversation"


@pytest.mark.asyncio
async def test_extract_facts_records_match_span():
    """Each fact carries a (start, end) span — useful for
    UI highlighting."""
    out = await extract_facts("Risk: weather delays may impact concrete pour")
    if out:
        span = out[0]["match_span"]
        assert isinstance(span, tuple)
        assert len(span) == 2
        assert span[0] < span[1]


@pytest.mark.asyncio
async def test_extract_facts_confidence_pinned():
    """All pattern-extracted facts have 0.75 confidence (less than
    0.85 for structured agent output)."""
    out = await extract_facts("Risk: weather delays may impact concrete pour")
    if out:
        assert out[0]["confidence"] == 0.75


@pytest.mark.asyncio
async def test_extract_facts_no_match_returns_empty():
    out = await extract_facts("Just a regular sentence with no facts.")
    assert out == []


@pytest.mark.asyncio
async def test_extract_facts_multiple_in_one_text():
    """Long text with multiple distinct fact patterns — extracts all."""
    text = (
        "The project budget is $500,000. "
        "Project deadline is 2026-08-31. "
        "We decided to use post-tensioned slab construction. "
        "Risk: supply chain delays for steel reinforcement may impact schedule."
    )
    out = await extract_facts(text)
    types = {f["fact_type"] for f in out}
    assert "budget" in types
    assert "schedule" in types
    assert "decision" in types
    assert "risk" in types


# =========================================================================
# extract_facts_from_agent_output
# =========================================================================


@pytest.mark.asyncio
async def test_agent_output_estimating_extracts_budget():
    """estimating_agent with total_cost → budget fact."""
    out = await extract_facts_from_agent_output("estimating_agent", {"total_cost": 1_234_567.89})
    assert len(out) == 1
    fact = out[0]
    assert fact["fact_type"] == "budget"
    assert "1,234,567.89" in fact["fact_text"]
    assert fact["confidence"] == 0.85


@pytest.mark.asyncio
async def test_agent_output_estimating_no_total_cost_no_fact():
    """Estimating output without total_cost field → no fact extracted."""
    out = await extract_facts_from_agent_output(
        "estimating_agent", {"summary": "no costs computed"}
    )
    assert out == []


@pytest.mark.asyncio
async def test_agent_output_scheduling_extracts_duration():
    out = await extract_facts_from_agent_output("scheduling_agent", {"total_duration": 365})
    assert len(out) == 1
    fact = out[0]
    assert fact["fact_type"] == "schedule"
    assert "365" in fact["fact_text"]


@pytest.mark.asyncio
async def test_agent_output_controls_extracts_risk_drivers():
    """Controls output's risk_drivers list → up to 3 risk facts."""
    out = await extract_facts_from_agent_output(
        "controls_agent",
        {
            "risk_drivers": [
                {"activity": "concrete_pour"},
                {"activity": "steel_erection"},
                {"activity": "mep_rough"},
                {"activity": "finishes"},  # 4th — must be capped
            ]
        },
    )
    # Only top 3 risk drivers extracted:
    assert len(out) == 3
    names = " ".join(f["fact_text"] for f in out)
    assert "concrete_pour" in names
    assert "steel_erection" in names
    assert "mep_rough" in names
    assert "finishes" not in names  # capped at 3


@pytest.mark.asyncio
async def test_agent_output_unknown_agent_no_facts():
    out = await extract_facts_from_agent_output("unknown_agent", {"data": "x"})
    assert out == []


@pytest.mark.asyncio
async def test_agent_output_source_type_is_agent_output():
    """All structured-extraction facts carry source_type=agent_output."""
    out = await extract_facts_from_agent_output("estimating_agent", {"total_cost": 100.0})
    assert out[0]["source_type"] == "agent_output"


@pytest.mark.asyncio
async def test_agent_output_higher_confidence_than_text_extraction():
    """Structured extraction confidence (0.85) is higher than
    text-pattern extraction (0.75)."""
    out_struct = await extract_facts_from_agent_output("estimating_agent", {"total_cost": 100.0})
    out_text = await extract_facts("Risk: weather delays may impact concrete pour")
    if out_text:
        assert out_struct[0]["confidence"] > out_text[0]["confidence"]
