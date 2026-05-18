"""Tests for the rate-limiter backends.

Pure async unit tests — no HTTP / middleware layer. Pin the
sliding-window math, the eviction-on-overflow protection, and the
backend factory so a refactor can't silently weaken DoS defenses.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.middleware.rate_limiter import (
    MemoryRateLimiterBackend,
    RateLimiterBackend,
    RedisRateLimiterBackend,
    _create_backend,
)

# =========================================================================
# MemoryRateLimiterBackend
# =========================================================================


@pytest.fixture
def backend() -> MemoryRateLimiterBackend:
    return MemoryRateLimiterBackend()


async def test_check_and_increment_returns_zero_for_first_request(
    backend: MemoryRateLimiterBackend,
):
    """First request to a key sees ``count=0`` (no prior requests in
    the window). The middleware compares against the limit, so 0 is
    "fully allowed"."""
    count = await backend.check_and_increment("ip-1", window_seconds=60, limit=10)
    assert count == 0


async def test_each_request_increments_count(backend: MemoryRateLimiterBackend):
    """Sequence of N requests must report count 0..N-1 — that's how the
    middleware decides whether the *next* request is over the limit."""
    counts = []
    for _ in range(5):
        counts.append(await backend.check_and_increment("ip-1", 60, 10))
    assert counts == [0, 1, 2, 3, 4]


async def test_isolated_keys_dont_pollute_each_other(backend: MemoryRateLimiterBackend):
    """Per-IP / per-tenant isolation is the whole point — one IP's
    flood must not affect another IP's count."""
    for _ in range(3):
        await backend.check_and_increment("ip-A", 60, 10)
    count = await backend.check_and_increment("ip-B", 60, 10)
    assert count == 0  # ip-B starts from scratch


async def test_old_entries_outside_window_are_pruned(
    backend: MemoryRateLimiterBackend, monkeypatch
):
    """Sliding window: requests older than ``window_seconds`` no longer
    count against the limit."""
    # Manually seed an old entry so we don't have to wait.
    backend._requests["ip-1"] = [time.monotonic() - 120]  # 2 minutes ago
    count = await backend.check_and_increment("ip-1", window_seconds=60, limit=10)
    # Old entry was pruned → count starts fresh at 0.
    assert count == 0


async def test_evicts_oldest_keys_when_capacity_exceeded(monkeypatch):
    """When the dict outgrows ``_MAX_KEYS``, the backend drops the 10%
    oldest keys instead of growing without bound — protects against
    memory-exhaustion via random key flooding."""
    backend = MemoryRateLimiterBackend()
    monkeypatch.setattr(backend, "_MAX_KEYS", 10)

    for i in range(11):  # one more than capacity
        await backend.check_and_increment(f"ip-{i:02d}", 60, 10)
    assert len(backend._requests) <= 10


async def test_concurrent_increments_are_safe(backend: MemoryRateLimiterBackend):
    """The asyncio.Lock around check_and_increment means N concurrent
    requests on the same key see a strictly-increasing sequence."""

    async def _hit():
        return await backend.check_and_increment("ip-1", 60, 100)

    counts = await asyncio.gather(*(_hit() for _ in range(20)))
    assert sorted(counts) == list(range(20))


# =========================================================================
# RedisRateLimiterBackend
# =========================================================================


async def test_redis_backend_uses_sorted_set_per_key():
    """The Redis backend prefixes keys with ``ratelimit:`` and uses
    a sorted-set per key for the sliding window."""
    rb = RedisRateLimiterBackend("redis://test/0")
    fake_redis = MagicMock()
    pipe = MagicMock()
    pipe.zremrangebyscore = MagicMock()
    pipe.zcard = MagicMock()
    pipe.zadd = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[None, 5, None, None])  # ZCARD = 5
    fake_redis.pipeline = MagicMock(return_value=pipe)
    rb._redis = fake_redis

    count = await rb.check_and_increment("ip-1", window_seconds=60, limit=10)
    assert count == 5

    # ZCARD reads BEFORE the new entry is added — ensures the count
    # reported is the count BEFORE this request, matching the memory
    # backend's semantic.
    pipe.zremrangebyscore.assert_called_once()
    pipe.zcard.assert_called_once()
    pipe.zadd.assert_called_once()
    # TTL slightly larger than window so Redis evicts the key on idle.
    pipe.expire.assert_called_once_with("ratelimit:ip-1", 61)


async def test_redis_backend_lazily_connects():
    """The connection is created on first use; repeated calls reuse it."""
    rb = RedisRateLimiterBackend("redis://test/0")
    assert rb._redis is None  # not yet connected

    fake_redis = MagicMock()
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[None, 0, None, None])
    fake_redis.pipeline = MagicMock(return_value=pipe)

    # Patch the actual ``redis.asyncio.from_url`` factory the production
    # code reaches via inline import.
    with patch("redis.asyncio.from_url", return_value=fake_redis) as from_url:
        await rb.check_and_increment("ip-1", 60, 10)
        assert rb._redis is fake_redis
        # Second call must NOT reconnect:
        await rb.check_and_increment("ip-1", 60, 10)
        from_url.assert_called_once()


# =========================================================================
# _create_backend factory
# =========================================================================


def test_create_backend_redis_returns_redis_backend():
    backend = _create_backend("redis", "redis://localhost/0")
    assert isinstance(backend, RedisRateLimiterBackend)


def test_create_backend_memory_returns_memory_backend():
    backend = _create_backend("memory", "")
    assert isinstance(backend, MemoryRateLimiterBackend)


def test_create_backend_unknown_value_falls_back_to_memory():
    """An unrecognised backend name should NOT raise — the factory
    falls back to the in-memory backend so misconfiguration doesn't
    take the app down on startup."""
    backend = _create_backend("filesystem-imaginary", "")
    assert isinstance(backend, MemoryRateLimiterBackend)


def test_backend_abc_cannot_be_instantiated_directly():
    """The base class is abstract — a refactor can't accidentally drop
    the @abstractmethod and create silently broken subclasses."""
    with pytest.raises(TypeError):
        RateLimiterBackend()  # type: ignore[abstract]
