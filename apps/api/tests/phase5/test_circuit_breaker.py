"""Tests for circuit breaker open/close behavior."""

from __future__ import annotations

import pytest

from app.services.reliability.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerManager,
)


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_starts_closed(self):
        cb = CircuitBreaker("test", fail_max=3)
        assert cb.state == "closed"
        assert await cb.is_available() is True

    @pytest.mark.asyncio
    async def test_opens_after_failures(self):
        cb = CircuitBreaker("test", fail_max=3)
        await cb.record_failure()
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == "open"
        assert await cb.is_available() is False

    @pytest.mark.asyncio
    async def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker("test", fail_max=3)
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == "closed"
        assert await cb.is_available() is True

    @pytest.mark.asyncio
    async def test_success_resets_count(self):
        cb = CircuitBreaker("test", fail_max=3)
        await cb.record_failure()
        await cb.record_failure()
        await cb.record_success()
        assert cb.state == "closed"
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_reset(self):
        cb = CircuitBreaker("test", fail_max=3)
        await cb.record_failure()
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == "open"
        cb.reset()
        assert cb.state == "closed"


class TestCircuitBreakerManager:
    def test_get_safety_breaker(self):
        mgr = CircuitBreakerManager(
            safety_fail_max=3,
            safety_reset=30,
        )
        breaker = mgr.get_breaker(
            "anthropic",
            is_safety_critical=True,
        )
        assert breaker.fail_max == 3

    def test_get_routine_breaker(self):
        mgr = CircuitBreakerManager(
            routine_fail_max=10,
            routine_reset=120,
        )
        breaker = mgr.get_breaker("anthropic")
        assert breaker.fail_max == 10

    def test_same_breaker_returned(self):
        mgr = CircuitBreakerManager()
        b1 = mgr.get_breaker("openai")
        b2 = mgr.get_breaker("openai")
        assert b1 is b2

    def test_different_breakers_per_provider(self):
        mgr = CircuitBreakerManager()
        b1 = mgr.get_breaker("openai")
        b2 = mgr.get_breaker("anthropic")
        assert b1 is not b2

    def test_get_all_states(self):
        mgr = CircuitBreakerManager()
        mgr.get_breaker("openai")
        mgr.get_breaker("anthropic")
        states = mgr.get_all_states()
        assert len(states) == 2

    @pytest.mark.asyncio
    async def test_reset_all(self):
        mgr = CircuitBreakerManager(
            routine_fail_max=2,
        )
        b = mgr.get_breaker("openai")
        await b.record_failure()
        await b.record_failure()
        assert b.state == "open"
        mgr.reset_all()
        assert b.state == "closed"
