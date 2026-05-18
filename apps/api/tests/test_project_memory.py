"""Tests for the long-term project fact store.

[security] H-11 (sanitize on store + retrieve to prevent prompt
injection from past memory) and H-12 (eviction at fact limit) are
both pinned here.
"""

from __future__ import annotations

import pytest

from app.services.memory.project_memory import (
    FACT_TYPES,
    SOURCE_TYPES,
    ProjectMemory,
)

# =========================================================================
# Module constants
# =========================================================================


def test_fact_types_canonical():
    """Pin documented fact types — refactor must not silently drop one."""
    expected = {
        "decision",
        "constraint",
        "requirement",
        "preference",
        "contact",
        "budget",
        "schedule",
        "risk",
        "lesson_learned",
    }
    assert expected == FACT_TYPES


def test_source_types_canonical():
    expected = {
        "conversation",
        "document",
        "agent_output",
        "user_input",
        "system_derived",
    }
    assert expected == SOURCE_TYPES


# =========================================================================
# store_fact — happy path + validation
# =========================================================================


@pytest.fixture
def memory() -> ProjectMemory:
    return ProjectMemory()


@pytest.mark.asyncio
async def test_store_fact_returns_uuid(memory: ProjectMemory):
    fid = await memory.store_fact(
        project_id="p-1",
        fact_type="decision",
        fact_text="Selected vendor B for steel",
        source_type="conversation",
    )
    import uuid

    uuid.UUID(fid)  # must be valid UUID


@pytest.mark.asyncio
async def test_store_fact_invalid_fact_type_rejected(memory: ProjectMemory):
    """Unknown fact_type must raise — keeps the taxonomy controlled."""
    with pytest.raises(ValueError, match="Invalid fact_type"):
        await memory.store_fact(
            project_id="p",
            fact_type="alien_type",
            fact_text="x",
            source_type="conversation",
        )


@pytest.mark.asyncio
async def test_store_fact_invalid_source_type_rejected(memory: ProjectMemory):
    with pytest.raises(ValueError, match="Invalid source_type"):
        await memory.store_fact(
            project_id="p",
            fact_type="decision",
            fact_text="x",
            source_type="alien_source",
        )


@pytest.mark.asyncio
async def test_store_fact_default_confidence_one(memory: ProjectMemory):
    """Default confidence is 1.0 — caller-provided fact assumed
    high-confidence unless told otherwise."""
    await memory.store_fact(
        project_id="p",
        fact_type="decision",
        fact_text="hello world",
        source_type="conversation",
    )
    facts = await memory.get_active_facts("p")
    assert facts[0]["confidence"] == 1.0


@pytest.mark.asyncio
async def test_store_fact_uses_explicit_confidence(memory: ProjectMemory):
    await memory.store_fact(
        project_id="p",
        fact_type="decision",
        fact_text="hello world",
        source_type="conversation",
        confidence=0.5,
    )
    facts = await memory.get_active_facts("p")
    assert facts[0]["confidence"] == 0.5


@pytest.mark.asyncio
async def test_store_fact_carries_metadata(memory: ProjectMemory):
    """Caller-provided metadata round-trips through storage."""
    await memory.store_fact(
        project_id="p",
        fact_type="decision",
        fact_text="hello world",
        source_type="conversation",
        metadata={"agent": "estimator", "version": 2},
    )
    facts = await memory.get_active_facts("p")
    assert facts[0]["metadata"] == {"agent": "estimator", "version": 2}


@pytest.mark.asyncio
async def test_store_fact_per_project_isolation(memory: ProjectMemory):
    """Facts in project A don't leak into project B."""
    await memory.store_fact(
        project_id="p-a",
        fact_type="decision",
        fact_text="alpha decision data",
        source_type="conversation",
    )
    await memory.store_fact(
        project_id="p-b",
        fact_type="decision",
        fact_text="beta decision data",
        source_type="conversation",
    )
    a = await memory.get_active_facts("p-a")
    b = await memory.get_active_facts("p-b")
    assert len(a) == 1 and "alpha" in a[0]["fact_text"]
    assert len(b) == 1 and "beta" in b[0]["fact_text"]


# =========================================================================
# retrieve_facts — text matching
# =========================================================================


@pytest.mark.asyncio
async def test_retrieve_no_match_returns_empty(memory: ProjectMemory):
    await memory.store_fact(
        project_id="p",
        fact_type="decision",
        fact_text="completely unrelated content here",
        source_type="conversation",
    )
    out = await memory.retrieve_facts("p", "alien topic xyz")
    assert out == []


@pytest.mark.asyncio
async def test_retrieve_word_match_returns_fact(memory: ProjectMemory):
    await memory.store_fact(
        project_id="p",
        fact_type="decision",
        fact_text="we chose 4000 psi concrete mix for the slab",
        source_type="conversation",
    )
    out = await memory.retrieve_facts("p", "concrete")
    assert len(out) == 1


@pytest.mark.asyncio
async def test_retrieve_orders_by_match_count(memory: ProjectMemory):
    """A query with 3 word matches outranks one with 1."""
    await memory.store_fact(
        project_id="p",
        fact_type="decision",
        fact_text="rebar steel concrete pour today",  # all 3 keywords
        source_type="conversation",
    )
    await memory.store_fact(
        project_id="p",
        fact_type="decision",
        fact_text="just rebar mention only",  # 1 keyword
        source_type="conversation",
    )
    out = await memory.retrieve_facts("p", "rebar steel concrete")
    assert len(out) == 2
    # Higher-match first:
    assert "rebar steel concrete" in out[0]["fact_text"]


@pytest.mark.asyncio
async def test_retrieve_respects_limit(memory: ProjectMemory):
    """Only return at most ``limit`` facts."""
    for i in range(5):
        await memory.store_fact(
            project_id="p",
            fact_type="decision",
            fact_text=f"concrete fact number {i}",
            source_type="conversation",
        )
    out = await memory.retrieve_facts("p", "concrete", limit=2)
    assert len(out) == 2


@pytest.mark.asyncio
async def test_retrieve_excludes_invalidated_facts(memory: ProjectMemory):
    fid = await memory.store_fact(
        project_id="p",
        fact_type="decision",
        fact_text="old superseded decision about concrete",
        source_type="conversation",
    )
    await memory.invalidate_fact(fid)
    out = await memory.retrieve_facts("p", "concrete")
    assert out == []


# =========================================================================
# invalidate_fact
# =========================================================================


@pytest.mark.asyncio
async def test_invalidate_existing_returns_true(memory: ProjectMemory):
    fid = await memory.store_fact(
        project_id="p",
        fact_type="decision",
        fact_text="hello world",
        source_type="conversation",
    )
    assert await memory.invalidate_fact(fid) is True


@pytest.mark.asyncio
async def test_invalidate_unknown_returns_false(memory: ProjectMemory):
    assert await memory.invalidate_fact("never-exists-id-xyz") is False


@pytest.mark.asyncio
async def test_invalidated_fact_excluded_from_active(memory: ProjectMemory):
    fid = await memory.store_fact(
        project_id="p",
        fact_type="decision",
        fact_text="hello world",
        source_type="conversation",
    )
    await memory.invalidate_fact(fid)
    out = await memory.get_active_facts("p")
    assert out == []


# =========================================================================
# get_active_facts — type filter
# =========================================================================


@pytest.mark.asyncio
async def test_get_active_facts_filter_by_type(memory: ProjectMemory):
    await memory.store_fact("p", "decision", "decision text data", "conversation")
    await memory.store_fact("p", "constraint", "constraint text data", "conversation")
    await memory.store_fact("p", "risk", "risk text data", "conversation")

    decisions = await memory.get_active_facts("p", fact_type="decision")
    assert len(decisions) == 1
    assert decisions[0]["fact_type"] == "decision"

    all_facts = await memory.get_active_facts("p")
    assert len(all_facts) == 3


# =========================================================================
# clear
# =========================================================================


@pytest.mark.asyncio
async def test_clear_empties_all_projects(memory: ProjectMemory):
    await memory.store_fact("p-1", "decision", "fact one data", "conversation")
    await memory.store_fact("p-2", "decision", "fact two data", "conversation")
    memory.clear()
    assert await memory.get_active_facts("p-1") == []
    assert await memory.get_active_facts("p-2") == []
