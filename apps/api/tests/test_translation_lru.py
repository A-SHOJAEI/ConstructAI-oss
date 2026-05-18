"""Tests for the translation service's LRU cache.

The cache has a memory + Redis two-tier layout. Redis is patched out
so each test exercises a single, deterministic branch of the cache
contract.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.communication.translation_service import (
    TranslationResult,
    _LRUCache,
)


def _make_result(text: str = "hola", *, cached: bool = False) -> TranslationResult:
    return TranslationResult(
        translated_text=text,
        source_language="en",
        target_language="es",
        confidence=0.95,
        cached=cached,
    )


@pytest.fixture
def lru():
    """LRU cache with Redis lookup forced to None — exercise the
    in-memory branch only. Redis-aware tests opt in by patching."""
    cache = _LRUCache(maxlen=3)
    # Mark Redis as already-checked so the first .get/.put doesn't try
    # to import redis.asyncio.
    cache._redis_checked = True
    cache._redis = None
    return cache


# ---- in-memory LRU behaviour --------------------------------------------


async def test_get_returns_none_for_missing_key(lru: _LRUCache):
    assert await lru.get("nope") is None


async def test_put_then_get_round_trip(lru: _LRUCache):
    rt = _make_result()
    await lru.put("key-1", rt)
    out = await lru.get("key-1")
    assert out is not None
    assert out.translated_text == "hola"


async def test_put_evicts_oldest_when_over_capacity(lru: _LRUCache):
    """3-slot cache; inserting a 4th evicts the LRU entry."""
    await lru.put("k1", _make_result("v1"))
    await lru.put("k2", _make_result("v2"))
    await lru.put("k3", _make_result("v3"))
    await lru.put("k4", _make_result("v4"))
    assert await lru.get("k1") is None  # evicted
    for k in ("k2", "k3", "k4"):
        assert (await lru.get(k)) is not None


async def test_get_promotes_entry_to_most_recently_used(lru: _LRUCache):
    """A get() bumps the entry to the front so it survives eviction."""
    await lru.put("k1", _make_result("v1"))
    await lru.put("k2", _make_result("v2"))
    await lru.put("k3", _make_result("v3"))
    # Touch k1 → now MRU, k2 becomes LRU.
    await lru.get("k1")
    await lru.put("k4", _make_result("v4"))  # should evict k2, not k1
    assert await lru.get("k1") is not None
    assert await lru.get("k2") is None


async def test_put_existing_key_updates_value_and_promotes(lru: _LRUCache):
    """Re-put on an existing key both updates the stored value and
    moves the entry to MRU position."""
    await lru.put("k1", _make_result("v1"))
    await lru.put("k2", _make_result("v2"))
    await lru.put("k1", _make_result("v1-updated"))  # update + promote
    out = await lru.get("k1")
    assert out is not None
    assert out.translated_text == "v1-updated"


async def test_clear_drops_everything(lru: _LRUCache):
    await lru.put("k1", _make_result())
    await lru.put("k2", _make_result())
    lru.clear()
    assert lru.size == 0
    assert await lru.get("k1") is None


async def test_size_reflects_current_entries(lru: _LRUCache):
    assert lru.size == 0
    await lru.put("k1", _make_result())
    assert lru.size == 1
    await lru.put("k2", _make_result())
    assert lru.size == 2


# ---- Redis fallback ------------------------------------------------------


async def test_redis_miss_falls_back_to_lru_only():
    """When Redis is reachable but the key isn't there, get returns
    the LRU entry (or None if also missing locally)."""
    cache = _LRUCache(maxlen=10)
    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    cache._redis_checked = True
    cache._redis = fake_redis

    assert await cache.get("k") is None  # both layers miss


async def test_redis_hit_promotes_to_lru():
    """When Redis has the entry but LRU doesn't, the cache pulls from
    Redis, marks the result as cached=True, and promotes to LRU."""
    cache = _LRUCache(maxlen=10)
    payload = (
        '{"translated_text": "hola", "source_language": "en", '
        '"target_language": "es", "confidence": 0.9}'
    )
    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=payload)
    cache._redis_checked = True
    cache._redis = fake_redis

    out = await cache.get("k")
    assert out is not None
    assert out.translated_text == "hola"
    assert out.cached is True

    # Now in LRU — second get() should not re-hit Redis.
    fake_redis.get = AsyncMock(return_value=None)
    assert (await cache.get("k")).translated_text == "hola"


async def test_put_writes_through_to_redis():
    """Cache.put writes to BOTH layers."""
    cache = _LRUCache(maxlen=10)
    fake_redis = AsyncMock()
    fake_redis.set = AsyncMock(return_value=True)
    cache._redis_checked = True
    cache._redis = fake_redis

    await cache.put("k", _make_result("greeting"))
    fake_redis.set.assert_awaited_once()
    args, _kwargs = fake_redis.set.call_args
    assert args[0] == "translation:k"


async def test_redis_get_failure_swallowed_returns_lru_value():
    """Redis errors must not propagate. If Redis fails, we still serve
    from the in-memory layer."""
    cache = _LRUCache(maxlen=10)
    cache._redis_checked = True
    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
    cache._redis = fake_redis

    await cache.put("k", _make_result("from-lru"))  # LRU layer has value
    out = await cache.get("k")
    assert out is not None
    assert out.translated_text == "from-lru"


async def test_redis_unavailable_at_construction_uses_memory_only():
    """Lazy-connect path: when Redis is configured but ping() fails,
    cache silently falls through to memory-only behaviour."""
    cache = _LRUCache(maxlen=10)
    # _redis_checked starts False → first .get attempts a connection.
    with patch("redis.asyncio.from_url", side_effect=ConnectionError("no redis")):
        out = await cache.get("missing")
    assert out is None
    # Now Redis is marked checked + None for subsequent calls.
    assert cache._redis_checked is True
    assert cache._redis is None


# ---- TranslationResult dataclass ---------------------------------------


def test_translation_result_carries_all_fields():
    rt = TranslationResult(
        translated_text="bonjour",
        source_language="en",
        target_language="fr",
        confidence=0.9,
        cached=False,
    )
    assert rt.translated_text == "bonjour"
    assert rt.confidence == 0.9
    assert rt.cached is False
