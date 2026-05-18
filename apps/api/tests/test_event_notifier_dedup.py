"""Tests for the event notifier dedup-key generation.

The notify() function is DB + Redis bound; pin the deterministic
hash that backs notification deduplication. A correct dedup key is
critical: if the hash isn't deterministic, every retry sends a new
notification (alert spam); if it's TOO sparse, similar events get
collapsed (missing alerts).
"""

from __future__ import annotations

import uuid

import pytest

from app.services.notifications.event_notifier import (
    _NOTIFICATION_DEDUP_TTL_SECONDS,
    _compute_dedup_key,
)

# =========================================================================
# TTL constant
# =========================================================================


def test_dedup_ttl_5_minutes():
    """Pin documented TTL: 5 minutes (300s) — short enough that
    genuine re-triggers reach the user, long enough to suppress
    repeat retries."""
    assert _NOTIFICATION_DEDUP_TTL_SECONDS == 300


# =========================================================================
# _compute_dedup_key — determinism
# =========================================================================


@pytest.mark.asyncio
async def test_dedup_key_same_inputs_same_key():
    """[deterministic] Same event/project/context → same key."""
    project_id = uuid.uuid4()
    context = {"rfi_id": "abc", "due_date": "2026-04-30"}

    a = await _compute_dedup_key("rfi.overdue", project_id, context)
    b = await _compute_dedup_key("rfi.overdue", project_id, context)
    assert a == b


@pytest.mark.asyncio
async def test_dedup_key_different_event_different_key():
    project_id = uuid.uuid4()
    context = {"rfi_id": "abc"}

    rfi_overdue = await _compute_dedup_key("rfi.overdue", project_id, context)
    rfi_responded = await _compute_dedup_key("rfi.responded", project_id, context)
    assert rfi_overdue != rfi_responded


@pytest.mark.asyncio
async def test_dedup_key_different_project_different_key():
    """Same event for different projects → different keys.
    Otherwise project A's overdue RFI would dedupe project B's."""
    context = {"rfi_id": "abc"}
    p1 = uuid.uuid4()
    p2 = uuid.uuid4()

    a = await _compute_dedup_key("rfi.overdue", p1, context)
    b = await _compute_dedup_key("rfi.overdue", p2, context)
    assert a != b


@pytest.mark.asyncio
async def test_dedup_key_context_order_invariant():
    """[critical] Dict key order must NOT affect the hash. Otherwise
    the same event constructed with different key orderings would
    produce different hashes and bypass dedup."""
    project_id = uuid.uuid4()

    a = await _compute_dedup_key(
        "rfi.overdue",
        project_id,
        {"rfi_id": "abc", "due_date": "2026-04-30"},
    )
    b = await _compute_dedup_key(
        "rfi.overdue",
        project_id,
        {"due_date": "2026-04-30", "rfi_id": "abc"},  # swapped order
    )
    assert a == b


@pytest.mark.asyncio
async def test_dedup_key_returns_redis_namespace_prefix():
    """Pin the Redis key prefix so a refactor doesn't quietly change
    the storage layout (would invalidate all in-flight TTLs)."""
    p = uuid.uuid4()
    key = await _compute_dedup_key("rfi.overdue", p, {"x": 1})
    assert key.startswith("cai:notify_dedup:")


@pytest.mark.asyncio
async def test_dedup_key_hash_length():
    """Hash component is 32 hex chars (128 bits) — pin to detect
    accidental hash-length changes."""
    p = uuid.uuid4()
    key = await _compute_dedup_key("rfi.overdue", p, {"x": 1})
    # Strip prefix:
    hash_part = key[len("cai:notify_dedup:") :]
    assert len(hash_part) == 32
    # All hex:
    int(hash_part, 16)


@pytest.mark.asyncio
async def test_dedup_key_handles_non_string_context_values():
    """Context values can be int, float, bool, None — JSON serializable
    via default=str. Hash must be deterministic regardless."""
    p = uuid.uuid4()
    a = await _compute_dedup_key(
        "rfi.overdue",
        p,
        {"count": 5, "is_open": True, "due_date": None},
    )
    b = await _compute_dedup_key(
        "rfi.overdue",
        p,
        {"count": 5, "is_open": True, "due_date": None},
    )
    assert a == b


@pytest.mark.asyncio
async def test_dedup_key_different_context_value_different_key():
    """Two RFIs with different IDs → different dedup keys (the
    whole point — RFI A overdue alert ≠ RFI B overdue alert)."""
    p = uuid.uuid4()
    a = await _compute_dedup_key("rfi.overdue", p, {"rfi_id": "rfi-1"})
    b = await _compute_dedup_key("rfi.overdue", p, {"rfi_id": "rfi-2"})
    assert a != b


@pytest.mark.asyncio
async def test_dedup_key_empty_context():
    """Empty context → still produces a valid key (just shorter
    payload)."""
    p = uuid.uuid4()
    key = await _compute_dedup_key("rfi.overdue", p, {})
    assert key.startswith("cai:notify_dedup:")


@pytest.mark.asyncio
async def test_dedup_key_handles_uuid_in_context():
    """UUID values via default=str — must produce deterministic key."""
    p = uuid.uuid4()
    inner_uuid = uuid.uuid4()
    a = await _compute_dedup_key("rfi.overdue", p, {"rfi_id": inner_uuid})
    b = await _compute_dedup_key("rfi.overdue", p, {"rfi_id": inner_uuid})
    assert a == b
