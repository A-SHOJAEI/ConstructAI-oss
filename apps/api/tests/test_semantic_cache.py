"""Tests for the semantic cache (LLM response caching).

[security C-01] Pin tenant isolation in cache keys (org_id +
project_id incorporated in hash to prevent cross-tenant data leakage).
[business invariant] Pin NO_CACHE_AGENTS list — safety/RFI agents
must NEVER be cached.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.services.reliability.semantic_cache import (
    NO_CACHE_AGENTS,
    SemanticCache,
)

# =========================================================================
# NO_CACHE_AGENTS — pin documented safety/security list
# =========================================================================


def test_no_cache_agents_canonical_set():
    """[business invariant] safety_alert / safety_agent / rfi_resolution
    must NEVER be cached. Pin so a refactor can't silently allow
    safety output to be reused across tenants/sessions."""
    expected = {"safety_alert", "safety_agent", "rfi_resolution"}
    assert expected == NO_CACHE_AGENTS


# =========================================================================
# SemanticCache — initial state and constructor
# =========================================================================


@pytest.fixture
def cache() -> SemanticCache:
    """Cache without Redis — uses in-memory fallback."""
    return SemanticCache()


def test_default_similarity_threshold(cache: SemanticCache):
    assert cache._threshold == 0.90


def test_default_ttl_5_minutes(cache: SemanticCache):
    """Pin documented default TTL: 5 minutes (300s)."""
    assert cache._default_ttl == 300


def test_explicit_threshold_and_ttl():
    cache = SemanticCache(similarity_threshold=0.85, default_ttl=60)
    assert cache._threshold == 0.85
    assert cache._default_ttl == 60


# =========================================================================
# get/set lifecycle
# =========================================================================


@pytest.mark.asyncio
async def test_set_then_get_round_trip(cache: SemanticCache):
    await cache.set(
        prompt="What is the rebar spec?",
        response={"answer": "#5 bars at 12in OC"},
        agent_name="rag_search",
        org_id="org-1",
        project_id="proj-1",
    )
    out = await cache.get(
        prompt="What is the rebar spec?",
        agent_name="rag_search",
        org_id="org-1",
        project_id="proj-1",
    )
    assert out == {"answer": "#5 bars at 12in OC"}


@pytest.mark.asyncio
async def test_get_miss_returns_none(cache: SemanticCache):
    out = await cache.get(prompt="never cached", agent_name="rag_search", org_id="o")
    assert out is None


@pytest.mark.asyncio
async def test_set_no_cache_agent_skipped(cache: SemanticCache):
    """[security C-11] safety_alert agent name → set is a no-op."""
    await cache.set(
        prompt="dangerous fall risk",
        response={"alert": "P1"},
        agent_name="safety_alert",
        org_id="o",
    )
    out = await cache.get(prompt="dangerous fall risk", agent_name="safety_alert", org_id="o")
    assert out is None


@pytest.mark.asyncio
async def test_get_no_cache_agent_returns_none(cache: SemanticCache):
    """Even if somehow stored, no_cache agents always get None on get."""
    # Manually inject a fake entry, then verify get filters it:
    cache_key = cache._hash_prompt("x", org_id="o")
    cache._memory_cache[cache_key] = {
        "response": {"data": "leaked"},
        "agent_name": "rfi_resolution",
        "ttl": 300,
        "stored_at": time.monotonic(),
    }
    out = await cache.get(prompt="x", agent_name="rfi_resolution", org_id="o")
    assert out is None


# =========================================================================
# Tenant isolation — [security C-01]
# =========================================================================


@pytest.mark.asyncio
async def test_different_org_does_not_hit_cache(cache: SemanticCache):
    """[security C-01] Same prompt cached for org A must NOT be
    returned to org B. Cross-tenant cache leak would be a critical
    breach."""
    await cache.set(
        prompt="What's the budget?",
        response={"budget": 1_000_000, "tenant": "A"},
        agent_name="rag_search",
        org_id="org-A",
        project_id="proj-1",
    )
    out = await cache.get(
        prompt="What's the budget?",
        agent_name="rag_search",
        org_id="org-B",  # different org
        project_id="proj-1",
    )
    assert out is None


@pytest.mark.asyncio
async def test_different_project_does_not_hit_cache(cache: SemanticCache):
    """Same prompt + same org but different project → also miss.
    Project-level isolation prevents one project's analysis being
    served to a sibling project under the same org."""
    await cache.set(
        prompt="rebar size?",
        response={"size": "#5"},
        agent_name="rag_search",
        org_id="org-A",
        project_id="proj-1",
    )
    out = await cache.get(
        prompt="rebar size?",
        agent_name="rag_search",
        org_id="org-A",
        project_id="proj-2",  # different project
    )
    assert out is None


@pytest.mark.asyncio
async def test_no_org_id_logs_warning(cache: SemanticCache, caplog):
    """[security C-01] Calling without org_id is suspicious — must
    log a warning so the deployment surface is auditable."""
    import logging

    with caplog.at_level(logging.WARNING):
        cache._hash_prompt("x", org_id=None)
    assert any("isolation" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_hash_prompt_deterministic_per_tenant(cache: SemanticCache):
    """Same prompt + same tenant → same hash; different tenant →
    different hash."""
    a = cache._hash_prompt("hello", org_id="org-A", project_id="proj-1")
    b = cache._hash_prompt("hello", org_id="org-A", project_id="proj-1")
    c = cache._hash_prompt("hello", org_id="org-B", project_id="proj-1")
    assert a == b
    assert a != c


# =========================================================================
# TTL enforcement (memory cache)
# =========================================================================


@pytest.mark.asyncio
async def test_expired_memory_entry_not_returned(cache: SemanticCache):
    """An entry past its TTL must NOT be returned on read; it should
    also be evicted from memory cache."""
    await cache.set(
        prompt="x",
        response={"data": "old"},
        agent_name="rag_search",
        ttl=10,
        org_id="o",
    )
    # Patch monotonic to advance past TTL:
    with patch.object(time, "monotonic", return_value=time.monotonic() + 100):
        out = await cache.get(prompt="x", agent_name="rag_search", org_id="o")
    assert out is None


@pytest.mark.asyncio
async def test_within_ttl_entry_returned(cache: SemanticCache):
    await cache.set(
        prompt="x",
        response={"data": "fresh"},
        agent_name="rag_search",
        ttl=300,
        org_id="o",
    )
    out = await cache.get(prompt="x", agent_name="rag_search", org_id="o")
    assert out == {"data": "fresh"}


# =========================================================================
# invalidate / clear
# =========================================================================


@pytest.mark.asyncio
async def test_invalidate_removes_entry(cache: SemanticCache):
    await cache.set(prompt="x", response={"data": 1}, agent_name="rag_search", org_id="o")
    await cache.invalidate(prompt="x", org_id="o")
    out = await cache.get(prompt="x", agent_name="rag_search", org_id="o")
    assert out is None


@pytest.mark.asyncio
async def test_clear_empties_cache(cache: SemanticCache):
    await cache.set(prompt="a", response={"x": 1}, agent_name="rag_search", org_id="o")
    await cache.set(prompt="b", response={"x": 2}, agent_name="rag_search", org_id="o")
    cache.clear()
    assert cache._memory_cache == {}


# =========================================================================
# Memory cache eviction
# =========================================================================


@pytest.mark.asyncio
async def test_memory_cache_size_capped(cache: SemanticCache):
    """[memory bound] When in-memory cache hits its cap, oldest 25%
    are evicted."""
    with patch(
        "app.services.reliability.semantic_cache._MEMORY_CACHE_MAX_SIZE",
        20,
    ):
        # Fill 30 entries:
        for i in range(30):
            await cache.set(
                prompt=f"prompt-{i}",
                response={"i": i},
                agent_name="rag_search",
                org_id="o",
            )
        # Cache should not exceed cap:
        assert len(cache._memory_cache) <= 20


@pytest.mark.asyncio
async def test_evict_oldest_keeps_recent(cache: SemanticCache):
    """When evicting, oldest entries go first — recent entries stay."""
    with patch(
        "app.services.reliability.semantic_cache._MEMORY_CACHE_MAX_SIZE",
        4,
    ):
        # Fill 6 entries:
        for i in range(6):
            await cache.set(
                prompt=f"prompt-{i}",
                response={"i": i},
                agent_name="rag_search",
                org_id="o",
            )
        # The most recent entries should still be there:
        assert await cache.get(prompt="prompt-5", agent_name="rag_search", org_id="o") is not None
