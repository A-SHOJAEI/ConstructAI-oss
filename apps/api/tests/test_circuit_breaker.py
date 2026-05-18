"""Tests for the per-provider circuit breaker.

Pin state transitions (closed → open → half_open → closed),
fail-count threshold, reset timeout, and the safety-vs-routine
breaker config split.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.services.reliability.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitBreakerManager,
)

# =========================================================================
# CircuitBreaker
# =========================================================================


@pytest.fixture
def breaker() -> CircuitBreaker:
    return CircuitBreaker(name="test", fail_max=3, reset_timeout=30)


def test_initial_state_closed(breaker: CircuitBreaker):
    """Pin: brand-new breaker is closed (allowing traffic)."""
    assert breaker.state == "closed"
    assert breaker._fail_count == 0


def test_constructor_explicit_params():
    breaker = CircuitBreaker(name="custom", fail_max=10, reset_timeout=120)
    assert breaker.name == "custom"
    assert breaker.fail_max == 10
    assert breaker.reset_timeout == 120


@pytest.mark.asyncio
async def test_record_failure_increments_count(breaker: CircuitBreaker):
    await breaker.record_failure()
    assert breaker._fail_count == 1
    # Below threshold → still closed:
    assert breaker.state == "closed"


@pytest.mark.asyncio
async def test_record_failure_opens_at_threshold(breaker: CircuitBreaker):
    """[invariant] After fail_max failures, circuit opens."""
    for _ in range(3):
        await breaker.record_failure()
    assert breaker.state == "open"


@pytest.mark.asyncio
async def test_record_success_resets_count(breaker: CircuitBreaker):
    """A successful call zeros the fail count."""
    await breaker.record_failure()
    await breaker.record_failure()
    await breaker.record_success()
    assert breaker._fail_count == 0
    assert breaker.state == "closed"


@pytest.mark.asyncio
async def test_record_success_in_half_open_closes(breaker: CircuitBreaker):
    """When half-open, a success transitions back to closed."""
    breaker._state = "half_open"
    await breaker.record_success()
    assert breaker.state == "closed"


@pytest.mark.asyncio
async def test_state_transitions_to_half_open_after_timeout(breaker: CircuitBreaker):
    """[time-based recovery] After reset_timeout, open transitions
    to half_open on read."""
    # Open the circuit:
    for _ in range(3):
        await breaker.record_failure()
    assert breaker.state == "open"

    # Patch time.monotonic to advance past reset_timeout:
    fake_now = breaker._last_failure_time + 100
    with patch("time.monotonic", return_value=fake_now):
        assert breaker.state == "half_open"


@pytest.mark.asyncio
async def test_state_stays_open_within_timeout(breaker: CircuitBreaker):
    """Within reset_timeout, state stays open."""
    for _ in range(3):
        await breaker.record_failure()
    # Just 5 seconds later (reset_timeout is 30):
    fake_now = breaker._last_failure_time + 5
    with patch("time.monotonic", return_value=fake_now):
        assert breaker.state == "open"


@pytest.mark.asyncio
async def test_is_available_when_closed(breaker: CircuitBreaker):
    assert await breaker.is_available() is True


@pytest.mark.asyncio
async def test_is_available_when_open(breaker: CircuitBreaker):
    """Circuit open → not available."""
    for _ in range(3):
        await breaker.record_failure()
    assert await breaker.is_available() is False


@pytest.mark.asyncio
async def test_is_available_after_timeout_recovers(breaker: CircuitBreaker):
    """After reset_timeout, is_available transitions to half_open
    (returns True, allowing a probe call)."""
    for _ in range(3):
        await breaker.record_failure()

    fake_now = breaker._last_failure_time + 100
    with patch("time.monotonic", return_value=fake_now):
        assert await breaker.is_available() is True
    # And state is now half_open:
    assert breaker._state == "half_open"


def test_reset_clears_state(breaker: CircuitBreaker):
    """reset() returns the breaker to initial closed state."""
    breaker._state = "open"
    breaker._fail_count = 100
    breaker._last_failure_time = time.monotonic()

    breaker.reset()
    assert breaker._state == "closed"
    assert breaker._fail_count == 0
    assert breaker._last_failure_time is None


@pytest.mark.asyncio
async def test_record_failure_no_open_below_threshold(breaker: CircuitBreaker):
    """Just under fail_max → still closed."""
    for _ in range(2):  # fail_max is 3
        await breaker.record_failure()
    assert breaker._state == "closed"


# =========================================================================
# CircuitBreakerError
# =========================================================================


def test_circuit_breaker_error_is_exception_subclass():
    assert issubclass(CircuitBreakerError, Exception)


def test_circuit_breaker_error_carries_message():
    exc = CircuitBreakerError("Provider X is open")
    assert str(exc) == "Provider X is open"


# =========================================================================
# CircuitBreakerManager
# =========================================================================


@pytest.fixture
def manager() -> CircuitBreakerManager:
    return CircuitBreakerManager()


def test_default_safety_config_canonical():
    """[business invariant] Pin documented safety-critical config:
    fail_max=3 (open faster), reset_timeout=30s (recover faster)."""
    manager = CircuitBreakerManager()
    assert manager._safety_fail_max == 3
    assert manager._safety_reset == 30


def test_default_routine_config_canonical():
    """Pin documented routine config: fail_max=10 (more tolerant),
    reset_timeout=120s (longer cooldown)."""
    manager = CircuitBreakerManager()
    assert manager._routine_fail_max == 10
    assert manager._routine_reset == 120


def test_get_breaker_creates_routine_by_default(manager: CircuitBreakerManager):
    breaker = manager.get_breaker("openai")
    assert breaker.fail_max == 10
    assert breaker.reset_timeout == 120


def test_get_breaker_creates_safety_when_flagged(manager: CircuitBreakerManager):
    breaker = manager.get_breaker("safety_provider", is_safety_critical=True)
    assert breaker.fail_max == 3
    assert breaker.reset_timeout == 30


def test_get_breaker_returns_same_instance(manager: CircuitBreakerManager):
    """Two calls with same key → same breaker instance (singleton
    per provider+criticality)."""
    a = manager.get_breaker("openai")
    b = manager.get_breaker("openai")
    assert a is b


def test_get_breaker_different_criticality_different_instance(
    manager: CircuitBreakerManager,
):
    """Same provider name but different criticality → different
    breakers (so safety failures don't trip routine and vice versa)."""
    routine = manager.get_breaker("openai")
    safety = manager.get_breaker("openai", is_safety_critical=True)
    assert routine is not safety


def test_get_all_states_empty(manager: CircuitBreakerManager):
    assert manager.get_all_states() == {}


def test_get_all_states_after_creates(manager: CircuitBreakerManager):
    manager.get_breaker("openai")
    manager.get_breaker("anthropic", is_safety_critical=True)
    states = manager.get_all_states()
    assert len(states) == 2
    assert all(s == "closed" for s in states.values())


@pytest.mark.asyncio
async def test_get_all_states_reflects_open(manager: CircuitBreakerManager):
    breaker = manager.get_breaker("openai")
    for _ in range(10):  # routine fail_max
        await breaker.record_failure()
    states = manager.get_all_states()
    assert "openai:routine" in states
    assert states["openai:routine"] == "open"


@pytest.mark.asyncio
async def test_reset_all_clears_every_breaker(manager: CircuitBreakerManager):
    breaker_a = manager.get_breaker("a")
    for _ in range(10):
        await breaker_a.record_failure()
    assert breaker_a.state == "open"

    manager.reset_all()
    assert breaker_a.state == "closed"


def test_explicit_manager_config():
    """Explicit constructor args override defaults."""
    manager = CircuitBreakerManager(
        safety_fail_max=2,
        safety_reset=15,
        routine_fail_max=20,
        routine_reset=300,
    )
    assert manager._safety_fail_max == 2
    assert manager._routine_fail_max == 20
    safety = manager.get_breaker("provider", is_safety_critical=True)
    assert safety.fail_max == 2
    assert safety.reset_timeout == 15
