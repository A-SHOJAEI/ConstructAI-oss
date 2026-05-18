"""Redis cache wrapper for frequently accessed data.

Provides a thin async wrapper around ``redis.asyncio`` with automatic
JSON serialization.  When Redis is unavailable (not installed or
connection refused) every operation degrades gracefully -- ``get``
returns ``None``, ``set`` / ``delete`` return ``False``, and
``get_or_set`` falls through to the factory function.

Usage::

    from app.services.cache import CacheService, PROJECT_LIST_TTL

    cache = CacheService()
    projects = await cache.get_or_set(
        f"projects:{org_id}",
        factory=lambda: fetch_projects(org_id),
        ttl=PROJECT_LIST_TTL,
    )
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional Redis import
# ---------------------------------------------------------------------------

try:
    import redis.asyncio as aioredis

    _HAS_REDIS = True
except ImportError:
    aioredis = None  # type: ignore[assignment]
    _HAS_REDIS = False

# ---------------------------------------------------------------------------
# Pre-defined TTLs (seconds)
# ---------------------------------------------------------------------------

COST_DB_TTL = 86_400  # 24 hours (matches daily cost sync)
PPI_DATA_TTL = 86_400  # 24 hours (BLS updates weekly; refresh daily to catch it)
WEATHER_TTL = 10_800  # 3 hours (weather changes frequently on construction sites)
PROJECT_LIST_TTL = 300  # 5 minutes (low-change data)
EVM_SNAPSHOT_TTL = 3_600  # 1 hour (aligns with snapshot schedule)
DOCUMENT_LIST_TTL = 600  # 10 minutes

# Type alias for factory callables (sync or async)
Factory = Union[Callable[[], Any], Callable[[], Awaitable[Any]]]


class CacheService:
    """Async Redis cache with JSON serialization.

    The service lazily connects on first use.  If Redis is not
    installed or the connection fails, all operations degrade
    gracefully with logged warnings.

    Args:
        redis_url: Redis connection URL.  Defaults to the
            ``REDIS_URL`` env var, then ``redis://localhost:6379/1``.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/1")
        self._client: Any | None = None
        self._connected = False

    async def _ensure_client(self) -> bool:
        """Lazily create and test the Redis connection.

        Returns ``True`` if the client is ready, ``False`` otherwise.
        """
        if self._connected and self._client is not None:
            return True

        if not _HAS_REDIS:
            logger.debug("redis.asyncio not installed; caching disabled.")
            return False

        try:
            self._client = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
            )
            # Quick connectivity check
            await self._client.ping()
            self._connected = True
            return True
        except Exception as exc:
            logger.warning("Redis connection failed (%s); caching disabled.", exc)
            self._client = None
            self._connected = False
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any | None:
        """Get a cached value by key.

        Returns ``None`` on cache miss, connection error, or if Redis
        is unavailable.
        """
        if not await self._ensure_client():
            return None
        assert self._client is not None  # narrowed by _ensure_client
        try:
            raw = await self._client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Cache deserialization error for key %s", key)
            return None
        except Exception as exc:
            logger.warning("Cache GET failed for key %s: %s", key, exc)
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int = 3600,
    ) -> bool:
        """Set a cached value with a TTL in seconds.

        Returns ``True`` on success, ``False`` on failure.
        """
        if not await self._ensure_client():
            return False
        assert self._client is not None
        try:
            serialized = json.dumps(value, default=str)
            await self._client.set(key, serialized, ex=ttl)
            return True
        except (TypeError, ValueError) as exc:
            logger.warning("Cache serialization error for key %s: %s", key, exc)
            return False
        except Exception as exc:
            logger.warning("Cache SET failed for key %s: %s", key, exc)
            return False

    async def delete(self, key: str) -> bool:
        """Delete a cached key.

        Returns ``True`` if the key was deleted, ``False`` otherwise.
        """
        if not await self._ensure_client():
            return False
        assert self._client is not None
        try:
            result = await self._client.delete(key)
            return bool(result)
        except Exception as exc:
            logger.warning("Cache DELETE failed for key %s: %s", key, exc)
            return False

    async def get_or_set(
        self,
        key: str,
        factory: Factory,
        ttl: int = 3600,
    ) -> Any:
        """Get a value from cache, or compute it using ``factory`` on miss.

        If the cached value exists, return it immediately.  Otherwise
        call the factory function (which may be sync or async), cache
        the result, and return it.

        Args:
            key: Cache key.
            factory: A callable (sync or async) that produces the value.
            ttl: Time-to-live for the cached entry (seconds).

        Returns:
            The cached or freshly computed value.
        """
        cached = await self.get(key)
        if cached is not None:
            return cached

        # Compute the value
        import asyncio

        result = factory()
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            result = await result

        # Cache it (best-effort)
        await self.set(key, result, ttl=ttl)
        return result

    async def close(self) -> None:
        """Close the Redis connection gracefully."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            finally:
                self._client = None
                self._connected = False
