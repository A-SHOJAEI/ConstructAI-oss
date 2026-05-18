"""Tests for the Redis-backed security state (token blacklist + account lockout).

[security] These functions back JWT revocation and brute-force lockout.
Pin the in-memory fallback path (Redis-not-reachable) and the
production-startup gate that refuses to boot prod without Redis.

The Redis-connected paths can't be unit-tested without a real broker,
so we verify the fallback behavior by forcing Redis to be unavailable.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.security import redis_state

# =========================================================================
# fixtures
# =========================================================================


@pytest.fixture
def force_no_redis():
    """Force the module to skip Redis and use in-memory fallback."""
    # Reset module-level state:
    redis_state._redis_available = False
    redis_state._redis_client = None
    redis_state._memory_blacklist.clear()
    redis_state._memory_failed_attempts.clear()
    yield
    redis_state._redis_available = None
    redis_state._memory_blacklist.clear()
    redis_state._memory_failed_attempts.clear()


# =========================================================================
# Token blacklist (in-memory fallback)
# =========================================================================


async def test_blacklist_token_records_jti(force_no_redis):
    await redis_state.blacklist_token("test-jti-123")
    assert "test-jti-123" in redis_state._memory_blacklist


async def test_is_token_blacklisted_after_blacklist(force_no_redis):
    await redis_state.blacklist_token("revoked-token")
    assert await redis_state.is_token_blacklisted("revoked-token") is True


async def test_is_token_blacklisted_for_unknown_jti(force_no_redis):
    assert await redis_state.is_token_blacklisted("never-seen") is False


async def test_blacklist_with_explicit_ttl(force_no_redis):
    """Explicit ttl should be honored — the entry must expire after the
    given seconds."""
    await redis_state.blacklist_token("short-lived", ttl_seconds=1)
    assert await redis_state.is_token_blacklisted("short-lived") is True
    # Wait for it to expire and trigger eviction:
    await asyncio.sleep(1.1)
    redis_state._evict_expired_blacklist()
    assert await redis_state.is_token_blacklisted("short-lived") is False


async def test_evict_expired_blacklist_removes_old_entries(force_no_redis):
    """Past-expiry entries are removed; future-expiry are kept."""
    import time as _t

    redis_state._memory_blacklist["expired"] = _t.monotonic() - 100  # past
    redis_state._memory_blacklist["live"] = _t.monotonic() + 1000  # future

    redis_state._evict_expired_blacklist()
    assert "expired" not in redis_state._memory_blacklist
    assert "live" in redis_state._memory_blacklist


async def test_blacklist_size_cap_evicts_oldest(force_no_redis):
    """When the in-memory blacklist hits its cap, the oldest 10% should
    be evicted to make room for new entries."""
    # Force the cap to a small number for the test:
    with patch.object(redis_state, "_BLACKLIST_MAX_SIZE", 100):
        # Fill to capacity:
        import time as _t

        base = _t.monotonic()
        for i in range(100):
            redis_state._memory_blacklist[f"jti-{i}"] = base + 1000 + i
        # Now insert one more — should evict the oldest 10:
        await redis_state.blacklist_token("new-jti")
    # After eviction: original 100 - 10 oldest + 1 new = 91 (size <= cap):
    assert len(redis_state._memory_blacklist) <= 100
    assert "new-jti" in redis_state._memory_blacklist


# =========================================================================
# Account lockout
# =========================================================================


async def test_record_failed_attempt_appends(force_no_redis):
    await redis_state.record_failed_attempt("attacker@example.com")
    assert len(redis_state._memory_failed_attempts["attacker@example.com"]) == 1


async def test_record_multiple_failed_attempts_accumulate(force_no_redis):
    for _ in range(3):
        await redis_state.record_failed_attempt("user@example.com")
    assert len(redis_state._memory_failed_attempts["user@example.com"]) == 3


async def test_is_locked_out_below_threshold(force_no_redis):
    """4 failed attempts (threshold = 5) → not yet locked."""
    for _ in range(4):
        await redis_state.record_failed_attempt("user@example.com")
    assert await redis_state.is_locked_out("user@example.com") is False


async def test_is_locked_out_at_threshold(force_no_redis):
    """5 failed attempts (default threshold) → locked."""
    for _ in range(5):
        await redis_state.record_failed_attempt("attacker@example.com")
    assert await redis_state.is_locked_out("attacker@example.com") is True


async def test_is_locked_out_unknown_email_false(force_no_redis):
    """Account that's never had any failed attempts → not locked."""
    assert await redis_state.is_locked_out("clean@example.com") is False


async def test_clear_failed_attempts_unlocks(force_no_redis):
    """A successful login should clear the lockout history."""
    for _ in range(5):
        await redis_state.record_failed_attempt("recovered@example.com")
    assert await redis_state.is_locked_out("recovered@example.com") is True

    await redis_state.clear_failed_attempts("recovered@example.com")
    assert await redis_state.is_locked_out("recovered@example.com") is False


async def test_clear_failed_attempts_unknown_email_no_op(force_no_redis):
    """Clearing for an account that was never tracked must not crash."""
    await redis_state.clear_failed_attempts("never-existed@example.com")


async def test_lockout_window_expires_old_attempts(force_no_redis):
    """[P2-1] Old attempts beyond the 15-minute window must be pruned
    on the next is_locked_out check."""
    import time as _t

    # Inject 5 attempts older than the window:
    very_old = _t.monotonic() - redis_state._LOCKOUT_WINDOW - 100
    redis_state._memory_failed_attempts["old@example.com"] = [very_old] * 5
    # Should not be locked because the entries are too old:
    assert await redis_state.is_locked_out("old@example.com") is False


async def test_lockout_store_full_drops_new_entries(force_no_redis):
    """[P2-1] When the in-memory store is at 50K entries, new entries
    are DROPPED (not evicted). This is conservative — eviction could
    remove an active lockout, allowing brute force bypass."""
    # Simulate a full store with placeholder entries:
    for i in range(50_000):
        redis_state._memory_failed_attempts[f"existing-{i}@example.com"] = [0.0]

    await redis_state.record_failed_attempt("new-attacker@example.com")
    # The new attacker should NOT have been recorded:
    assert "new-attacker@example.com" not in redis_state._memory_failed_attempts


# =========================================================================
# require_redis_for_production
# =========================================================================


async def test_require_redis_dev_warns_but_does_not_raise(force_no_redis):
    """In dev environments, missing Redis should log a warning, not
    block startup."""

    class FakeSettings:
        ENVIRONMENT = "development"

    with patch.object(redis_state, "settings", FakeSettings()):
        # Must not raise:
        await redis_state.require_redis_for_production()


async def test_require_redis_test_warns_but_does_not_raise(force_no_redis):
    class FakeSettings:
        ENVIRONMENT = "test"

    with patch.object(redis_state, "settings", FakeSettings()):
        await redis_state.require_redis_for_production()


async def test_require_redis_production_raises_when_unavailable(force_no_redis):
    """[H-02] Production must REFUSE to start without Redis."""

    class FakeSettings:
        ENVIRONMENT = "production"

    with patch.object(redis_state, "settings", FakeSettings()):
        with pytest.raises(RuntimeError, match="Redis is required"):
            await redis_state.require_redis_for_production()


async def test_require_redis_staging_raises_when_unavailable(force_no_redis):
    """Staging is also production-equivalent — must refuse."""

    class FakeSettings:
        ENVIRONMENT = "staging"

    with patch.object(redis_state, "settings", FakeSettings()):
        with pytest.raises(RuntimeError, match="Redis is required"):
            await redis_state.require_redis_for_production()


async def test_require_redis_succeeds_when_redis_reachable():
    """If _get_redis returns a connected client, startup proceeds."""
    fake_client = AsyncMock()
    fake_client.ping = AsyncMock(return_value=True)

    async def fake_get_redis():
        return fake_client

    with patch.object(redis_state, "_get_redis", side_effect=fake_get_redis):
        # Whatever ENVIRONMENT, this must not raise:
        await redis_state.require_redis_for_production()


# =========================================================================
# Module constants
# =========================================================================


def test_lockout_threshold_is_five():
    """[security] Pin canonical lockout threshold — bumping it without
    explicit policy review would weaken brute-force protection."""
    assert redis_state._LOCKOUT_THRESHOLD == 5


def test_lockout_window_is_15_minutes():
    """15 min window — pin the documented value."""
    assert redis_state._LOCKOUT_WINDOW == 900
