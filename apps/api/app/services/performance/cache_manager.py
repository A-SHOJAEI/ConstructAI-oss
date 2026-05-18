"""Redis caching strategy for dashboard aggregations."""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class DashboardCacheManager:
    """Redis caching strategy for dashboard aggregations."""

    CACHE_KEYS: ClassVar[dict[str, int]] = {
        "portfolio_summary": 300,  # 5 min TTL
        "project_health": 60,  # 1 min TTL
        "agent_metrics": 120,  # 2 min TTL
        "evm_latest": 300,  # 5 min TTL
    }

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
        self._local_cache: dict[str, dict] = {}

    async def get(
        self,
        cache_type: str,
        key: str,
    ) -> dict | None:
        """Get cached value, checking TTL expiry."""
        full_key = f"{cache_type}:{key}"
        entry = self._local_cache.get(full_key)
        if entry:
            expiry_time = entry.get("expiry_time")
            if expiry_time is not None and time.time() >= expiry_time:
                del self._local_cache[full_key]
                logger.debug("Cache expired: %s", full_key)
                return None
            logger.debug("Cache hit: %s", full_key)
            return entry.get("value")
        logger.debug("Cache miss: %s", full_key)
        return None

    async def set(
        self,
        cache_type: str,
        key: str,
        value: dict,
    ) -> None:
        """Set cached value with TTL from CACHE_KEYS."""
        full_key = f"{cache_type}:{key}"
        ttl = self.CACHE_KEYS.get(cache_type, 60)
        self._local_cache[full_key] = {
            "value": value,
            "ttl": ttl,
            "expiry_time": time.time() + ttl,
        }
        logger.debug(
            "Cached %s with TTL %ds",
            full_key,
            ttl,
        )

    async def invalidate(
        self,
        cache_type: str,
        key: str,
    ) -> None:
        """Invalidate a cached entry."""
        full_key = f"{cache_type}:{key}"
        self._local_cache.pop(full_key, None)
        logger.debug("Invalidated cache: %s", full_key)

    async def invalidate_all(self, cache_type: str) -> None:
        """Invalidate all entries of a cache type."""
        keys_to_remove = [k for k in self._local_cache if k.startswith(f"{cache_type}:")]
        for k in keys_to_remove:
            del self._local_cache[k]
        logger.info(
            "Invalidated %d entries for %s",
            len(keys_to_remove),
            cache_type,
        )

    def get_stats(self) -> dict:
        """Get cache statistics."""
        return {
            "total_entries": len(self._local_cache),
            "cache_types": list(self.CACHE_KEYS.keys()),
        }
