"""Tests for memory store/retrieve operations."""

from __future__ import annotations

from app.services.memory.fact_extractor import (
    extract_facts,
    extract_facts_from_agent_output,
)
from app.services.memory.project_memory import (
    FACT_TYPES,
    SOURCE_TYPES,
    ProjectMemory,
)


class TestProjectMemory:
    async def test_store_and_retrieve(self):
        mem = ProjectMemory()
        fact_id = await mem.store_fact(
            project_id="p1",
            fact_type="decision",
            fact_text="Use steel framing for structure",
            source_type="conversation",
        )
        assert fact_id is not None
        facts = await mem.retrieve_facts(
            "p1",
            "steel framing",
        )
        assert len(facts) >= 1
        assert "steel" in facts[0]["fact_text"].lower()

    async def test_invalidate_fact(self):
        mem = ProjectMemory()
        fact_id = await mem.store_fact(
            project_id="p1",
            fact_type="budget",
            fact_text="Budget is $5M",
            source_type="user_input",
        )
        result = await mem.invalidate_fact(fact_id)
        assert result is True
        active = await mem.get_active_facts("p1")
        assert len(active) == 0

    async def test_get_active_facts_by_type(self):
        mem = ProjectMemory()
        await mem.store_fact(
            "p1",
            "budget",
            "Budget $5M",
            "user_input",
        )
        await mem.store_fact(
            "p1",
            "risk",
            "Soil conditions",
            "agent_output",
        )
        budgets = await mem.get_active_facts(
            "p1",
            fact_type="budget",
        )
        assert len(budgets) == 1
        assert budgets[0]["fact_type"] == "budget"

    async def test_invalid_fact_type(self):
        import pytest

        mem = ProjectMemory()
        with pytest.raises(ValueError):
            await mem.store_fact(
                "p1",
                "invalid_type",
                "text",
                "conversation",
            )

    async def test_invalid_source_type(self):
        import pytest

        mem = ProjectMemory()
        with pytest.raises(ValueError):
            await mem.store_fact(
                "p1",
                "decision",
                "text",
                "invalid_source",
            )

    def test_fact_types_defined(self):
        assert len(FACT_TYPES) >= 8
        assert "decision" in FACT_TYPES
        assert "budget" in FACT_TYPES

    def test_source_types_defined(self):
        assert len(SOURCE_TYPES) >= 4
        assert "conversation" in SOURCE_TYPES


class TestFactExtractor:
    async def test_extract_budget(self):
        text = "The project budget is $5,000,000."
        facts = await extract_facts(text)
        budget_facts = [f for f in facts if f["fact_type"] == "budget"]
        assert len(budget_facts) >= 1

    async def test_extract_decision(self):
        text = "We decided to use precast concrete panels."
        facts = await extract_facts(text)
        decisions = [f for f in facts if f["fact_type"] == "decision"]
        assert len(decisions) >= 1

    async def test_extract_from_agent_output(self):
        output = {"total_cost": 5000000.0}
        facts = await extract_facts_from_agent_output(
            "estimating_agent",
            output,
        )
        assert len(facts) >= 1
        assert facts[0]["fact_type"] == "budget"

    async def test_no_facts_in_empty_text(self):
        facts = await extract_facts("Hello world")
        assert len(facts) == 0
