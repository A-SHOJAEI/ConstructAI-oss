"""Tests for semantic cache hit/miss behavior."""

from __future__ import annotations

from app.services.reliability.semantic_cache import (
    NO_CACHE_AGENTS,
    SemanticCache,
)


class TestSemanticCache:
    async def test_cache_hit(self):
        cache = SemanticCache()
        await cache.set(
            "concrete strength requirements",
            {"answer": "4000 PSI"},
            "document_agent",
        )
        result = await cache.get(
            "concrete strength requirements",
            "document_agent",
        )
        assert result is not None
        assert result["answer"] == "4000 PSI"

    async def test_cache_miss(self):
        cache = SemanticCache()
        result = await cache.get(
            "something never cached",
            "document_agent",
        )
        assert result is None

    async def test_no_cache_for_safety(self):
        cache = SemanticCache()
        await cache.set(
            "safety alert data",
            {"alert": "danger"},
            "safety_alert",
        )
        result = await cache.get(
            "safety alert data",
            "safety_alert",
        )
        assert result is None

    async def test_invalidate(self):
        cache = SemanticCache()
        await cache.set(
            "test prompt",
            {"data": "cached"},
            "document_agent",
        )
        await cache.invalidate("test prompt")
        result = await cache.get(
            "test prompt",
            "document_agent",
        )
        assert result is None

    async def test_clear_all(self):
        cache = SemanticCache()
        await cache.set("p1", {"d": 1}, "document_agent")
        await cache.set("p2", {"d": 2}, "document_agent")
        cache.clear()
        r1 = await cache.get("p1", "document_agent")
        r2 = await cache.get("p2", "document_agent")
        assert r1 is None
        assert r2 is None

    def test_safety_agents_in_no_cache(self):
        assert "safety_alert" in NO_CACHE_AGENTS
        assert "safety_agent" in NO_CACHE_AGENTS
