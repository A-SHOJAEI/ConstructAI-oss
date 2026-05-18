"""Tests for PostgresSaver/in-memory checkpointer."""

from __future__ import annotations

from app.services.memory.checkpointer import (
    CheckpointerFactory,
    InMemoryCheckpointer,
)


class TestInMemoryCheckpointer:
    async def test_put_and_get(self):
        cp = InMemoryCheckpointer()
        config = {
            "configurable": {"thread_id": "thread-1"},
        }
        await cp.aput(
            config,
            {"state": "running"},
            {"step": 1},
        )
        result = await cp.aget(config)
        assert result is not None
        assert result["checkpoint"]["state"] == "running"

    async def test_get_missing(self):
        cp = InMemoryCheckpointer()
        config = {
            "configurable": {"thread_id": "nonexistent"},
        }
        result = await cp.aget(config)
        assert result is None

    async def test_list_checkpoints(self):
        cp = InMemoryCheckpointer()
        config1 = {
            "configurable": {"thread_id": "t1"},
        }
        config2 = {
            "configurable": {"thread_id": "t2"},
        }
        await cp.aput(config1, {"state": "a"})
        await cp.aput(config2, {"state": "b"})
        results = await cp.alist()
        assert len(results) == 2

    async def test_clear(self):
        cp = InMemoryCheckpointer()
        config = {
            "configurable": {"thread_id": "t1"},
        }
        await cp.aput(config, {"state": "x"})
        cp.clear()
        results = await cp.alist()
        assert len(results) == 0


class TestCheckpointerFactory:
    async def test_create_in_memory(self):
        cp = await CheckpointerFactory.create(db_url=None)
        assert isinstance(cp, InMemoryCheckpointer)

    async def test_create_without_postgres(self):
        # Without langgraph-checkpoint-postgres, falls back
        cp = await CheckpointerFactory.create(
            db_url="postgresql://fake:fake@localhost/fake",
        )
        assert isinstance(cp, InMemoryCheckpointer)
