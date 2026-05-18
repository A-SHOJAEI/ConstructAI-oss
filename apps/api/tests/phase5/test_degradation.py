"""Tests for degradation level transitions."""

from __future__ import annotations

from app.services.reliability.degradation_manager import (
    DEGRADATION_LEVELS,
    DegradationManager,
)


class TestDegradationManager:
    async def test_starts_at_level_0(self):
        mgr = DegradationManager()
        assert mgr.current_level == 0

    async def test_all_healthy_level_0(self):
        mgr = DegradationManager()
        level = await mgr.evaluate_health(
            {
                "anthropic": "closed",
                "openai": "closed",
            }
        )
        assert level == 0

    async def test_one_down_level_1(self):
        mgr = DegradationManager()
        level = await mgr.evaluate_health(
            {
                "anthropic": "open",
                "openai": "closed",
            }
        )
        assert level == 1

    async def test_all_down_level_3(self):
        mgr = DegradationManager()
        level = await mgr.evaluate_health(
            {
                "anthropic": "open",
                "openai": "open",
                "gemini": "open",
            }
        )
        assert level == 3

    async def test_half_open_level_1(self):
        mgr = DegradationManager()
        level = await mgr.evaluate_health(
            {
                "anthropic": "half_open",
                "openai": "closed",
            }
        )
        assert level == 1

    async def test_capabilities_level_0(self):
        mgr = DegradationManager()
        caps = await mgr.get_available_capabilities()
        assert caps["capabilities"]["cloud_llm"] is True
        assert caps["capabilities"]["vision_models"] is True

    async def test_capabilities_level_3(self):
        mgr = DegradationManager()
        await mgr.set_level(3)
        caps = await mgr.get_available_capabilities()
        assert caps["capabilities"]["cloud_llm"] is False
        assert caps["capabilities"]["local_llm"] is True

    async def test_manual_set_level(self):
        mgr = DegradationManager()
        await mgr.set_level(4)
        assert mgr.current_level == 4

    async def test_invalid_level(self):
        import pytest

        mgr = DegradationManager()
        with pytest.raises(ValueError):
            await mgr.set_level(99)

    def test_all_levels_defined(self):
        assert len(DEGRADATION_LEVELS) == 6
        for level in range(6):
            assert level in DEGRADATION_LEVELS

    def test_is_capability_available(self):
        mgr = DegradationManager()
        assert mgr.is_capability_available("cloud_llm")
