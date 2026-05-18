"""Tests for LiteLLM fallback chain."""

from __future__ import annotations

from app.services.reliability.llm_gateway import (
    LLMGateway,
)


class MockSuccessGateway(LLMGateway):
    """Gateway that succeeds on first try."""

    async def _call_model(self, model, messages, **kwargs):
        return {
            "content": "Success response",
            "model": model,
            "input_tokens": 100,
            "output_tokens": 50,
        }


class MockFailFirstGateway(LLMGateway):
    """Gateway that fails first model, succeeds on second.

    The fallback loop in LLMGateway re-raises RuntimeError without
    attempting the next provider (so cost-limit / budget errors don't
    silently swallow), but it DOES catch generic exceptions and fall
    back. Use a generic Exception here to exercise the fallback path.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._call_count = 0

    async def _call_model(self, model, messages, **kwargs):
        self._call_count += 1
        if self._call_count == 1:
            raise Exception("429 Rate limited")
        return {
            "content": "Fallback response",
            "model": model,
            "input_tokens": 100,
            "output_tokens": 50,
        }


class MockAllFailGateway(LLMGateway):
    """Gateway where all models fail (use generic Exception so the
    fallback loop walks the chain rather than re-raising)."""

    async def _call_model(self, model, messages, **kwargs):
        raise Exception("Service unavailable")


class TestLLMGateway:
    async def test_success_first_try(self):
        gw = MockSuccessGateway()
        result = await gw.complete(
            [{"role": "user", "content": "test"}],
            "document_agent",
        )
        assert result["content"] == "Success response"

    async def test_fallback_on_failure(self):
        gw = MockFailFirstGateway()
        result = await gw.complete(
            [{"role": "user", "content": "test"}],
            "document_agent",
        )
        assert result["content"] == "Fallback response"

    async def test_all_fail_raises(self):
        import pytest

        gw = MockAllFailGateway()
        # M-27: error message is now sanitized server-side; surface a generic
        # "AI service is temporarily unavailable" string.
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            await gw.complete(
                [{"role": "user", "content": "test"}],
                "document_agent",
            )

    async def test_usage_tracking(self):
        gw = MockSuccessGateway()
        await gw.complete(
            [{"role": "user", "content": "test"}],
            "document_agent",
        )
        log = gw.get_usage_log()
        assert len(log) == 1
        assert log[0]["agent_name"] == "document_agent"

    async def test_usage_summary(self):
        gw = MockSuccessGateway()
        await gw.complete(
            [{"role": "user", "content": "q1"}],
            "document_agent",
        )
        await gw.complete(
            [{"role": "user", "content": "q2"}],
            "estimating_agent",
        )
        summary = gw.get_usage_summary("document_agent")
        assert summary["total_requests"] == 1

    async def test_cost_estimation(self):
        gw = MockSuccessGateway()
        await gw.complete(
            [{"role": "user", "content": "test"}],
            "document_agent",
        )
        log = gw.get_usage_log()
        assert log[0]["cost_usd"] >= 0
