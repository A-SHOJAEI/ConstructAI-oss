"""Tests for the LangGraph workflow checkpointer.

Pin in-memory checkpointer behavior + factory fallback when
PostgresSaver isn't available.
"""

from __future__ import annotations

import pytest

from app.services.memory.checkpointer import (
    CheckpointerFactory,
    InMemoryCheckpointer,
)

# =========================================================================
# CheckpointerFactory
# =========================================================================


@pytest.mark.asyncio
async def test_factory_no_db_url_returns_in_memory():
    """Without a DB URL, factory returns the in-memory implementation."""
    cp = await CheckpointerFactory.create(db_url=None)
    assert isinstance(cp, InMemoryCheckpointer)


@pytest.mark.asyncio
async def test_factory_invalid_db_url_falls_back_to_memory():
    """[fallback] If PostgresSaver setup fails (bad URL, missing
    package), factory must NOT crash — returns in-memory."""
    cp = await CheckpointerFactory.create(db_url="postgresql://invalid/url")
    # Should fall back to in-memory:
    assert isinstance(cp, InMemoryCheckpointer)


# =========================================================================
# InMemoryCheckpointer
# =========================================================================


@pytest.fixture
def checkpointer() -> InMemoryCheckpointer:
    return InMemoryCheckpointer()


@pytest.mark.asyncio
async def test_aget_unknown_thread_returns_none(checkpointer: InMemoryCheckpointer):
    out = await checkpointer.aget({"configurable": {"thread_id": "never-existed"}})
    assert out is None


@pytest.mark.asyncio
async def test_aput_then_aget_round_trip(checkpointer: InMemoryCheckpointer):
    config = {"configurable": {"thread_id": "thread-1"}}
    checkpoint = {"step": 5, "state": {"current": "node_a"}}
    metadata = {"agent": "estimator"}

    await checkpointer.aput(config, checkpoint, metadata)
    out = await checkpointer.aget(config)

    assert out is not None
    assert out["checkpoint"] == checkpoint
    assert out["metadata"] == metadata


@pytest.mark.asyncio
async def test_aput_returns_config(checkpointer: InMemoryCheckpointer):
    """aput returns the config as-is (LangGraph contract)."""
    config = {"configurable": {"thread_id": "x"}}
    out = await checkpointer.aput(config, {}, {})
    assert out == config


@pytest.mark.asyncio
async def test_aput_metadata_defaults_to_empty_dict(
    checkpointer: InMemoryCheckpointer,
):
    """If caller doesn't supply metadata, stored entry has empty dict
    (not None)."""
    config = {"configurable": {"thread_id": "x"}}
    await checkpointer.aput(config, {"step": 1})
    out = await checkpointer.aget(config)
    assert out["metadata"] == {}


@pytest.mark.asyncio
async def test_aput_overwrites_previous_checkpoint(checkpointer: InMemoryCheckpointer):
    """Same thread_id → second aput replaces first."""
    config = {"configurable": {"thread_id": "thread-1"}}
    await checkpointer.aput(config, {"version": 1}, {})
    await checkpointer.aput(config, {"version": 2}, {})

    out = await checkpointer.aget(config)
    assert out["checkpoint"]["version"] == 2


@pytest.mark.asyncio
async def test_per_thread_isolation(checkpointer: InMemoryCheckpointer):
    """Different thread_ids stored independently."""
    config_a = {"configurable": {"thread_id": "thread-a"}}
    config_b = {"configurable": {"thread_id": "thread-b"}}

    await checkpointer.aput(config_a, {"data": "a"})
    await checkpointer.aput(config_b, {"data": "b"})

    out_a = await checkpointer.aget(config_a)
    out_b = await checkpointer.aget(config_b)
    assert out_a["checkpoint"]["data"] == "a"
    assert out_b["checkpoint"]["data"] == "b"


@pytest.mark.asyncio
async def test_aget_handles_missing_configurable_key(
    checkpointer: InMemoryCheckpointer,
):
    """Pin: empty config → uses thread_id="" lookup, returns None
    (not crash)."""
    out = await checkpointer.aget({})
    assert out is None


@pytest.mark.asyncio
async def test_aget_missing_thread_id_uses_empty_string(
    checkpointer: InMemoryCheckpointer,
):
    """[edge case] config without thread_id key → uses "" as lookup
    key. If anything was stored under "", it's returned."""
    config_no_id = {"configurable": {}}
    await checkpointer.aput(config_no_id, {"data": "default"})
    out = await checkpointer.aget(config_no_id)
    assert out is not None
    assert out["checkpoint"]["data"] == "default"


@pytest.mark.asyncio
async def test_alist_empty(checkpointer: InMemoryCheckpointer):
    out = await checkpointer.alist()
    assert out == []


@pytest.mark.asyncio
async def test_alist_returns_all_checkpoints(checkpointer: InMemoryCheckpointer):
    await checkpointer.aput({"configurable": {"thread_id": "a"}}, {"x": 1})
    await checkpointer.aput({"configurable": {"thread_id": "b"}}, {"x": 2})
    await checkpointer.aput({"configurable": {"thread_id": "c"}}, {"x": 3})
    out = await checkpointer.alist()
    assert len(out) == 3


@pytest.mark.asyncio
async def test_clear_resets_storage(checkpointer: InMemoryCheckpointer):
    await checkpointer.aput({"configurable": {"thread_id": "x"}}, {"data": 1})
    checkpointer.clear()
    assert await checkpointer.alist() == []
    assert await checkpointer.aget({"configurable": {"thread_id": "x"}}) is None
