"""Tests for the dashboard cache manager.

Pin TTL semantics, get/set/invalidate behavior, and the documented
cache type catalog.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.services.performance.cache_manager import DashboardCacheManager

# =========================================================================
# CACHE_KEYS — pin canonical cache types
# =========================================================================


def test_cache_keys_canonical_types():
    """Pin documented cache types — refactor must not silently drop one."""
    expected = {
        "portfolio_summary",
        "project_health",
        "agent_metrics",
        "evm_latest",
    }
    assert set(DashboardCacheManager.CACHE_KEYS.keys()) == expected


def test_cache_keys_ttls_in_seconds():
    """All TTLs are positive integers in seconds."""
    for key, ttl in DashboardCacheManager.CACHE_KEYS.items():
        assert isinstance(ttl, int)
        assert ttl > 0


def test_project_health_has_short_ttl():
    """Project health updates fast — pin shortest TTL."""
    assert DashboardCacheManager.CACHE_KEYS["project_health"] == 60


def test_portfolio_summary_has_5min_ttl():
    """Documented value: 5-minute portfolio cache."""
    assert DashboardCacheManager.CACHE_KEYS["portfolio_summary"] == 300


# =========================================================================
# get / set lifecycle
# =========================================================================


@pytest.fixture
def cache() -> DashboardCacheManager:
    return DashboardCacheManager()


@pytest.mark.asyncio
async def test_get_missing_key_returns_none(cache: DashboardCacheManager):
    out = await cache.get("portfolio_summary", "key-1")
    assert out is None


@pytest.mark.asyncio
async def test_set_then_get_round_trip(cache: DashboardCacheManager):
    payload = {"projects": 5, "alerts": 2}
    await cache.set("portfolio_summary", "org-123", payload)
    out = await cache.get("portfolio_summary", "org-123")
    assert out == payload


@pytest.mark.asyncio
async def test_get_expired_entry_returns_none(cache: DashboardCacheManager):
    """Past-expiry entries are evicted on read and return None."""
    payload = {"data": 1}
    # Patch time so set stores with expiry in the past:
    with patch.object(time, "time", return_value=1000.0):
        await cache.set("project_health", "p-1", payload)

    # Now jump forward beyond the 60s TTL:
    with patch.object(time, "time", return_value=1100.0):
        out = await cache.get("project_health", "p-1")
    assert out is None


@pytest.mark.asyncio
async def test_get_within_ttl_returns_value(cache: DashboardCacheManager):
    payload = {"score": 95}
    with patch.object(time, "time", return_value=1000.0):
        await cache.set("project_health", "p-1", payload)

    # 30s later — within 60s TTL:
    with patch.object(time, "time", return_value=1030.0):
        out = await cache.get("project_health", "p-1")
    assert out == payload


@pytest.mark.asyncio
async def test_set_unknown_cache_type_uses_60s_default(cache: DashboardCacheManager):
    """Setting a cache_type not in CACHE_KEYS uses the 60-second
    default TTL — pin so a missing entry doesn't silently use 0s."""
    payload = {"x": 1}
    with patch.object(time, "time", return_value=1000.0):
        await cache.set("alien_type", "k", payload)

    # 30s later — should still be there:
    with patch.object(time, "time", return_value=1030.0):
        out = await cache.get("alien_type", "k")
    assert out == payload

    # 65s later — past 60s default TTL:
    with patch.object(time, "time", return_value=1065.0):
        out = await cache.get("alien_type", "k")
    assert out is None


# =========================================================================
# invalidate
# =========================================================================


@pytest.mark.asyncio
async def test_invalidate_removes_entry(cache: DashboardCacheManager):
    await cache.set("portfolio_summary", "org-1", {"data": 1})
    assert await cache.get("portfolio_summary", "org-1") is not None

    await cache.invalidate("portfolio_summary", "org-1")
    assert await cache.get("portfolio_summary", "org-1") is None


@pytest.mark.asyncio
async def test_invalidate_unknown_no_op(cache: DashboardCacheManager):
    """Invalidating a non-existent key must not crash."""
    await cache.invalidate("portfolio_summary", "never-exists")


@pytest.mark.asyncio
async def test_invalidate_all_clears_only_specified_type(cache: DashboardCacheManager):
    """invalidate_all narrows to one cache type — other types untouched."""
    await cache.set("portfolio_summary", "org-1", {"a": 1})
    await cache.set("portfolio_summary", "org-2", {"b": 2})
    await cache.set("project_health", "p-1", {"c": 3})

    await cache.invalidate_all("portfolio_summary")

    # Both portfolio entries gone:
    assert await cache.get("portfolio_summary", "org-1") is None
    assert await cache.get("portfolio_summary", "org-2") is None
    # Project health still present:
    assert await cache.get("project_health", "p-1") == {"c": 3}


@pytest.mark.asyncio
async def test_invalidate_all_unknown_type_no_op(cache: DashboardCacheManager):
    await cache.invalidate_all("never-exists")


# =========================================================================
# get_stats
# =========================================================================


@pytest.mark.asyncio
async def test_get_stats_initial(cache: DashboardCacheManager):
    stats = cache.get_stats()
    assert stats["total_entries"] == 0
    # Lists the 4 documented cache types:
    assert set(stats["cache_types"]) == set(DashboardCacheManager.CACHE_KEYS.keys())


@pytest.mark.asyncio
async def test_get_stats_after_writes(cache: DashboardCacheManager):
    await cache.set("portfolio_summary", "k1", {"a": 1})
    await cache.set("project_health", "k2", {"b": 2})
    stats = cache.get_stats()
    assert stats["total_entries"] == 2


# =========================================================================
# Cross-key isolation
# =========================================================================


@pytest.mark.asyncio
async def test_same_key_different_cache_types_isolated(cache: DashboardCacheManager):
    """The same logical key under different cache types must not
    collide (e.g. project_health "p-1" and portfolio "p-1" are
    different things)."""
    await cache.set("portfolio_summary", "p-1", {"src": "portfolio"})
    await cache.set("project_health", "p-1", {"src": "health"})

    out_portfolio = await cache.get("portfolio_summary", "p-1")
    out_health = await cache.get("project_health", "p-1")
    assert out_portfolio["src"] == "portfolio"
    assert out_health["src"] == "health"


@pytest.mark.asyncio
async def test_set_overwrites_existing_entry(cache: DashboardCacheManager):
    await cache.set("portfolio_summary", "org-1", {"version": 1})
    await cache.set("portfolio_summary", "org-1", {"version": 2})
    out = await cache.get("portfolio_summary", "org-1")
    assert out == {"version": 2}
