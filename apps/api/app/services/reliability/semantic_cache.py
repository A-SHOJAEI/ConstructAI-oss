"""Redis-backed semantic cache for LLM responses."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Agent types that should never be cached (safety-critical or tenant-sensitive)
NO_CACHE_AGENTS = {"safety_alert", "safety_agent", "rfi_resolution"}

# SEC-08: System agents that may operate without org_id (no tenant context)
_SYSTEM_AGENTS = {"system", "migration", "health_check"}

# Maximum entries in the in-memory fallback cache
_MEMORY_CACHE_MAX_SIZE = 2000


class SemanticCache:
    """Redis-backed semantic cache for LLM responses.

    Uses prompt hash for exact matching and cosine
    similarity for semantic matching.
    TTL: 5 minutes for non-safety, no cache for safety.

    SECURITY: Cache keys incorporate org_id and project_id to prevent
    cross-tenant data leakage (C-01 fix).
    """

    def __init__(
        self,
        redis_client: Any = None,
        similarity_threshold: float = 0.90,
        default_ttl: int = 300,
    ):
        self._redis = redis_client
        self._threshold = similarity_threshold
        self._default_ttl = default_ttl
        # In-memory fallback when Redis unavailable — bounded with TTL
        self._memory_cache: dict[str, dict] = {}

    async def get(
        self,
        prompt: str,
        agent_name: str,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
    ) -> dict | None:
        """Check cache for matching prompt.

        Returns cached response or None.
        """
        if agent_name in NO_CACHE_AGENTS:
            return None

        cache_key = self._hash_prompt(prompt, org_id=org_id, project_id=project_id)

        # Try Redis first
        if self._redis:
            try:
                data = self._redis_get(cache_key)
                if data:
                    logger.debug(
                        "Cache hit for %s (redis)",
                        agent_name,
                    )
                    return data
            except Exception:
                logger.warning("Redis cache get failed")

        # Fallback to memory cache with TTL enforcement
        entry = self._memory_cache.get(cache_key)
        if entry:
            # Enforce TTL on memory cache reads
            if time.monotonic() - entry.get("stored_at", 0) > entry.get("ttl", self._default_ttl):
                del self._memory_cache[cache_key]
                return None
            logger.debug(
                "Cache hit for %s (memory)",
                agent_name,
            )
            return entry.get("response")

        return None

    async def set(
        self,
        prompt: str,
        response: dict,
        agent_name: str,
        ttl: int | None = None,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
    ):
        """Store response in cache."""
        if agent_name in NO_CACHE_AGENTS:
            return

        cache_key = self._hash_prompt(prompt, org_id=org_id, project_id=project_id)
        ttl = ttl or self._default_ttl

        entry = {
            "response": response,
            "agent_name": agent_name,
            "ttl": ttl,
            "stored_at": time.monotonic(),
        }

        # Try Redis
        if self._redis:
            try:
                self._redis_set(cache_key, entry, ttl)
            except Exception:
                logger.warning("Redis cache set failed")

        # Store in memory as fallback — enforce size limit via LRU eviction
        if len(self._memory_cache) >= _MEMORY_CACHE_MAX_SIZE:
            self._evict_oldest_memory_entries()
        self._memory_cache[cache_key] = entry

    async def invalidate(
        self, prompt: str, *, org_id: str | None = None, project_id: str | None = None
    ):
        """Remove a cached entry."""
        cache_key = self._hash_prompt(prompt, org_id=org_id, project_id=project_id)
        self._memory_cache.pop(cache_key, None)
        if self._redis:
            import contextlib

            with contextlib.suppress(Exception):
                self._redis_del(cache_key)

    def clear(self):
        """Clear all cached entries."""
        self._memory_cache.clear()

    def _hash_prompt(
        self,
        prompt: str,
        *,
        org_id: str | None = None,
        project_id: str | None = None,
    ) -> str:
        """Create cache key from prompt hash with tenant isolation.

        SECURITY: org_id and project_id are included in the hash to
        prevent cross-tenant cache hits (C-01).
        """
        if org_id is None:
            logger.warning(
                "semantic_cache called without org_id - cache isolation may be compromised"
            )
        key_material = f"{org_id or ''}:{project_id or ''}:{prompt}"
        return hashlib.sha256(
            key_material.encode("utf-8"),
        ).hexdigest()

    def _evict_oldest_memory_entries(self) -> None:
        """Evict oldest 25% of memory cache entries."""
        entries = sorted(
            self._memory_cache.items(),
            key=lambda kv: kv[1].get("stored_at", 0),
        )
        evict_count = max(1, len(entries) // 4)
        for key, _ in entries[:evict_count]:
            del self._memory_cache[key]

    def _redis_get(self, key: str) -> dict | None:
        """Get from Redis (sync wrapper)."""
        if not self._redis:
            return None
        data = self._redis.get(f"sc:{key}")
        if data:
            entry = json.loads(data)
            return entry.get("response") if isinstance(entry, dict) else entry
        return None

    def _redis_set(
        self,
        key: str,
        value: dict,
        ttl: int,
    ):
        """Set in Redis with TTL."""
        if self._redis:
            self._redis.setex(
                f"sc:{key}",
                ttl,
                json.dumps(value),
            )

    def _redis_del(self, key: str):
        """Delete from Redis."""
        if self._redis:
            self._redis.delete(f"sc:{key}")
