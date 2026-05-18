"""Tests for the CacheService Redis wrapper.

The wrapper degrades gracefully when Redis isn't available — every test
exercises a real branch of that contract by mocking the underlying
client. Production callers (intelligence brief, EVM snapshot, etc.)
depend on the "Redis-down doesn't break the request" property; pinning
each branch here protects it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.cache import (
    DOCUMENT_LIST_TTL,
    EVM_SNAPSHOT_TTL,
    PPI_DATA_TTL,
    PROJECT_LIST_TTL,
    WEATHER_TTL,
    CacheService,
)


@pytest.fixture
def working_redis():
    """Patch the redis client builder so ``_ensure_client`` succeeds.

    Returns the AsyncMock client so each test can configure
    get/set/delete behavior.
    """
    fake = AsyncMock()
    fake.ping = AsyncMock(return_value=True)
    with patch("app.services.cache.aioredis") as fake_module:
        fake_module.from_url.return_value = fake
        with patch("app.services.cache._HAS_REDIS", True):
            yield fake


@pytest.fixture
def broken_redis():
    """Make every connection attempt raise — exercises the
    Redis-unavailable degradation path."""
    with patch("app.services.cache.aioredis") as fake_module:
        fake_module.from_url.side_effect = ConnectionError("connection refused")
        with patch("app.services.cache._HAS_REDIS", True):
            yield


# ---- ensure_client / connectivity ---------------------------------------


async def test_get_returns_none_when_redis_module_missing():
    """If ``redis.asyncio`` isn't even installed, the cache must
    silently no-op rather than crash on import."""
    with patch("app.services.cache._HAS_REDIS", False):
        cache = CacheService()
        assert await cache.get("any-key") is None


async def test_set_returns_false_when_redis_unavailable(broken_redis):
    cache = CacheService()
    assert await cache.set("k", {"v": 1}) is False


async def test_delete_returns_false_when_redis_unavailable(broken_redis):
    cache = CacheService()
    assert await cache.delete("k") is False


# ---- get / set / delete happy paths -------------------------------------


async def test_set_then_get_round_trip(working_redis):
    storage: dict = {}

    async def _set(key, value, ex=None):
        storage[key] = value
        return True

    async def _get(key):
        return storage.get(key)

    working_redis.set = AsyncMock(side_effect=_set)
    working_redis.get = AsyncMock(side_effect=_get)

    cache = CacheService()
    assert await cache.set("user:1", {"name": "alice"}, ttl=60) is True
    assert await cache.get("user:1") == {"name": "alice"}


async def test_set_propagates_ttl_to_redis(working_redis):
    working_redis.set = AsyncMock(return_value=True)
    cache = CacheService()
    await cache.set("k", "v", ttl=42)
    _, kwargs = working_redis.set.call_args
    assert kwargs["ex"] == 42


async def test_get_returns_none_on_cache_miss(working_redis):
    working_redis.get = AsyncMock(return_value=None)
    cache = CacheService()
    assert await cache.get("missing-key") is None


async def test_get_returns_none_on_corrupt_json(working_redis):
    """Corrupt cache entries (manual tampering, encoding bug upstream)
    must not propagate as JSON errors — return None and let the caller
    re-compute via the factory."""
    working_redis.get = AsyncMock(return_value="<not json>")
    cache = CacheService()
    assert await cache.get("k") is None


async def test_set_returns_false_on_truly_unserializable_value(working_redis):
    """``cache.set`` uses ``default=str`` so most objects fall back to
    their ``str()`` representation — but values that can't even be
    represented as a string (e.g. a circular reference dict) still
    return False instead of raising."""
    cache = CacheService()
    circular: dict = {}
    circular["self"] = circular  # JSON encoder hits maximum recursion
    assert await cache.set("k", circular) is False


async def test_delete_returns_true_when_key_existed(working_redis):
    working_redis.delete = AsyncMock(return_value=1)
    cache = CacheService()
    assert await cache.delete("k") is True


async def test_delete_returns_false_when_key_missing(working_redis):
    working_redis.delete = AsyncMock(return_value=0)
    cache = CacheService()
    assert await cache.delete("k") is False


async def test_get_returns_none_on_redis_runtime_error(working_redis):
    """A transient Redis error after the connection is established must
    not leak to the caller — return None so the caller falls through to
    the source of truth."""
    working_redis.get = AsyncMock(side_effect=ConnectionError("redis dropped"))
    cache = CacheService()
    assert await cache.get("k") is None


# ---- get_or_set ---------------------------------------------------------


async def test_get_or_set_returns_cached_value_without_calling_factory(working_redis):
    working_redis.get = AsyncMock(return_value='{"hit": true}')

    factory_calls = []

    async def _factory():
        factory_calls.append(1)
        return {"hit": "from-factory"}

    cache = CacheService()
    out = await cache.get_or_set("k", _factory)
    assert out == {"hit": True}
    assert factory_calls == []  # factory never ran


async def test_get_or_set_invokes_async_factory_on_miss(working_redis):
    working_redis.get = AsyncMock(return_value=None)
    working_redis.set = AsyncMock(return_value=True)

    async def _factory():
        return {"computed": 42}

    cache = CacheService()
    out = await cache.get_or_set("k", _factory, ttl=60)
    assert out == {"computed": 42}
    working_redis.set.assert_awaited_once()


async def test_get_or_set_invokes_sync_factory_on_miss(working_redis):
    working_redis.get = AsyncMock(return_value=None)
    working_redis.set = AsyncMock(return_value=True)

    def _factory():  # synchronous
        return [1, 2, 3]

    cache = CacheService()
    assert await cache.get_or_set("k", _factory) == [1, 2, 3]


async def test_get_or_set_still_returns_value_when_cache_set_fails(working_redis):
    """Cache write failure must NOT propagate — the caller still gets
    the freshly computed value."""
    working_redis.get = AsyncMock(return_value=None)
    working_redis.set = AsyncMock(side_effect=ConnectionError("set failed"))

    async def _factory():
        return {"x": 1}

    cache = CacheService()
    assert await cache.get_or_set("k", _factory) == {"x": 1}


# ---- close --------------------------------------------------------------


async def test_close_swallows_errors(working_redis):
    working_redis.close = AsyncMock(side_effect=ConnectionError("boom"))
    cache = CacheService()
    await cache._ensure_client()
    # Must not raise:
    await cache.close()
    assert cache._client is None


async def test_close_is_safe_when_never_connected():
    cache = CacheService()
    # Never connected; close should be a no-op rather than raise.
    await cache.close()


# ---- module-level TTL constants ----------------------------------------


def test_ttl_constants_are_sane_durations():
    """Pinning the TTLs against documented business meaning protects
    against accidental changes that would either thrash Redis or serve
    stale data."""
    assert PPI_DATA_TTL == 86_400  # 24h — BLS updates weekly
    assert WEATHER_TTL == 10_800  # 3h
    assert PROJECT_LIST_TTL == 300  # 5m
    assert EVM_SNAPSHOT_TTL == 3_600  # 1h, aligned to snapshot job
    assert DOCUMENT_LIST_TTL == 600  # 10m
