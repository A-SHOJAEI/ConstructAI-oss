"""Per-provider circuit breakers."""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open."""


class CircuitBreaker:
    """Simple circuit breaker implementation.

    States: closed -> open -> half_open -> closed

    State mutations are protected by an asyncio.Lock for thread safety
    in concurrent async contexts.
    """

    def __init__(
        self,
        name: str,
        fail_max: int = 5,
        reset_timeout: int = 60,
    ):
        self.name = name
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self._fail_count = 0
        self._state = "closed"
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        """Get current state, checking for timeout."""
        if self._state == "open" and self._last_failure_time:
            if time.monotonic() - self._last_failure_time >= self.reset_timeout:
                return "half_open"
        return self._state

    async def is_available(self) -> bool:
        """Check if the circuit is available for requests."""
        async with self._lock:
            if self._state == "open" and self._last_failure_time:
                if time.monotonic() - self._last_failure_time >= self.reset_timeout:
                    self._state = "half_open"
            return self._state in ("closed", "half_open")

    async def record_success(self):
        """Record a successful call."""
        async with self._lock:
            if self._state == "half_open":
                logger.info(
                    "Circuit breaker %s: half_open -> closed",
                    self.name,
                )
            self._fail_count = 0
            self._state = "closed"

    async def record_failure(self):
        """Record a failed call."""
        async with self._lock:
            self._fail_count += 1
            self._last_failure_time = time.monotonic()
            if self._fail_count >= self.fail_max:
                self._state = "open"
                logger.warning(
                    "Circuit breaker %s opened after %d failures",
                    self.name,
                    self._fail_count,
                )

    def reset(self):
        """Reset the circuit breaker."""
        self._fail_count = 0
        self._state = "closed"
        self._last_failure_time = None


class CircuitBreakerManager:
    """Manage per-provider circuit breakers.

    Safety-critical: fail_max=3, reset_timeout=30s
    Routine: fail_max=10, reset_timeout=120s
    """

    def __init__(
        self,
        safety_fail_max: int = 3,
        safety_reset: int = 30,
        routine_fail_max: int = 10,
        routine_reset: int = 120,
    ):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._safety_fail_max = safety_fail_max
        self._safety_reset = safety_reset
        self._routine_fail_max = routine_fail_max
        self._routine_reset = routine_reset

    def get_breaker(
        self,
        provider: str,
        is_safety_critical: bool = False,
    ) -> CircuitBreaker:
        """Get or create circuit breaker for a provider."""
        key = f"{provider}:{'safety' if is_safety_critical else 'routine'}"
        if key not in self._breakers:
            if is_safety_critical:
                self._breakers[key] = CircuitBreaker(
                    name=key,
                    fail_max=self._safety_fail_max,
                    reset_timeout=self._safety_reset,
                )
            else:
                self._breakers[key] = CircuitBreaker(
                    name=key,
                    fail_max=self._routine_fail_max,
                    reset_timeout=self._routine_reset,
                )
        return self._breakers[key]

    def get_all_states(self) -> dict[str, str]:
        """Get state of all breakers."""
        return {name: breaker.state for name, breaker in self._breakers.items()}

    def reset_all(self):
        """Reset all circuit breakers."""
        for breaker in self._breakers.values():
            breaker.reset()
