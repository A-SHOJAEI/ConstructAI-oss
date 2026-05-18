"""Redis-backed state for token blacklist and account lockout.

Falls back to in-memory stores when Redis is unavailable.

NOTE: The in-memory fallback is for DEVELOPMENT ONLY. In staging/production,
Redis MUST be available. Call ``require_redis_for_production()`` during app
startup to enforce this. The in-memory stores are not shared across workers
and are lost on restart, which would allow blacklisted tokens to be reused.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

from app.config import settings

logger = logging.getLogger(__name__)

_redis_client = None
_redis_available: bool | None = None


async def _get_redis():
    """Lazily connect to Redis. Returns None if unavailable."""
    global _redis_client, _redis_available

    if _redis_available is False:
        return None
    if _redis_client is not None:
        return _redis_client

    try:
        import redis.asyncio as aioredis

        from app.config import settings

        _redis_client = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2
        )
        await _redis_client.ping()
        _redis_available = True
        logger.info("Redis state store connected")
        return _redis_client
    except Exception:
        _redis_available = False
        logger.warning("Redis unavailable — using in-memory state (not suitable for production)")
        return None


# --------------------------------------------------------------------------- #
# Token Blacklist
# --------------------------------------------------------------------------- #

# RT6-AUTH-06: Use dict[jti, timestamp] with eviction instead of unbounded set
_memory_blacklist: dict[str, float] = {}
# SECURITY [M-05]: Couple blacklist TTL to token expiry (+60s safety margin)
try:
    from app.config import settings as _blacklist_settings

    _BLACKLIST_TTL = _blacklist_settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60 + 60
except (ImportError, AttributeError):
    _BLACKLIST_TTL = 1800  # fallback: 30 minutes
_BLACKLIST_MAX_SIZE = 50_000


def _evict_expired_blacklist() -> None:
    """Remove expired entries from the in-memory blacklist.

    Each entry stores its expiry timestamp (monotonic). Entries where
    the stored value <= now have expired and should be evicted.
    """
    now = time.monotonic()
    expired = [k for k, expiry in _memory_blacklist.items() if expiry <= now]
    for k in expired:
        del _memory_blacklist[k]


async def blacklist_token(jti: str, ttl_seconds: int | None = None) -> None:
    """Add a token JTI to the blacklist.

    Args:
        jti: The JWT ID to blacklist.
        ttl_seconds: Time-to-live in seconds. Defaults to _BLACKLIST_TTL
            (derived from ACCESS_TOKEN_EXPIRE_MINUTES + 60s safety margin).
    """
    if ttl_seconds is None:
        ttl_seconds = _BLACKLIST_TTL
    r = await _get_redis()
    if r is not None:
        try:
            await r.set(f"cai:blacklist:{jti}", "1", ex=ttl_seconds)
            return
        except Exception:
            logger.warning("Redis blacklist_token failed, using memory fallback")

    _evict_expired_blacklist()
    # Cap size to prevent unbounded growth
    if len(_memory_blacklist) >= _BLACKLIST_MAX_SIZE:
        # Evict oldest 10% of entries
        sorted_entries = sorted(_memory_blacklist.items(), key=lambda x: x[1])
        for k, _ in sorted_entries[: _BLACKLIST_MAX_SIZE // 10]:
            del _memory_blacklist[k]
    _memory_blacklist[jti] = time.monotonic() + ttl_seconds


async def is_token_blacklisted(jti: str) -> bool:
    """Check if a token JTI has been blacklisted."""
    r = await _get_redis()
    if r is not None:
        try:
            result = await r.get(f"cai:blacklist:{jti}")
            return result is not None
        except Exception:
            logger.warning("Redis is_token_blacklisted failed, using memory fallback")

    _evict_expired_blacklist()
    return jti in _memory_blacklist


# --------------------------------------------------------------------------- #
# Account Lockout
# --------------------------------------------------------------------------- #

_memory_failed_attempts: dict[str, list[float]] = defaultdict(list)

_LOCKOUT_THRESHOLD = 5
_LOCKOUT_WINDOW = 900  # 15 minutes


async def record_failed_attempt(email: str) -> None:
    """Record a failed login attempt for the given email."""
    r = await _get_redis()
    if r is not None:
        try:
            key = f"cai:lockout:{email}"
            now = time.time()
            pipe = r.pipeline()
            pipe.zadd(key, {str(now): now})
            pipe.zremrangebyscore(key, "-inf", now - _LOCKOUT_WINDOW)
            pipe.expire(key, _LOCKOUT_WINDOW + 1)
            await pipe.execute()
            return
        except Exception:
            logger.warning("Redis record_failed_attempt failed, using memory fallback")

    # SECURITY [P2-1]: Reject new entries when store is full instead of evicting.
    # Eviction could remove active lockout records, allowing brute-force bypass.
    if len(_memory_failed_attempts) >= 50_000:
        logger.warning(
            "SECURITY: In-memory lockout store full, rejecting new entry for %s",
            email[:3] + "***",
        )
        return  # Drop — conservative: no existing entries evicted
    _memory_failed_attempts[email].append(time.monotonic())


async def is_locked_out(email: str) -> bool:
    """Check if an email account is locked due to too many failed attempts."""
    r = await _get_redis()
    if r is not None:
        try:
            key = f"cai:lockout:{email}"
            now = time.time()
            await r.zremrangebyscore(key, "-inf", now - _LOCKOUT_WINDOW)
            count = await r.zcard(key)
            return count >= _LOCKOUT_THRESHOLD
        except Exception:
            logger.warning("Redis is_locked_out failed, using memory fallback")

    cutoff = time.monotonic() - _LOCKOUT_WINDOW
    _memory_failed_attempts[email] = [t for t in _memory_failed_attempts[email] if t > cutoff]
    return len(_memory_failed_attempts[email]) >= _LOCKOUT_THRESHOLD


async def clear_failed_attempts(email: str) -> None:
    """Clear failed login attempts after a successful login."""
    r = await _get_redis()
    if r is not None:
        try:
            await r.delete(f"cai:lockout:{email}")
            return
        except Exception:
            logger.warning("Redis clear_failed_attempts failed, using memory fallback")

    _memory_failed_attempts.pop(email, None)


# --------------------------------------------------------------------------- #
# SECURITY [H-02]: Production Redis availability check
# --------------------------------------------------------------------------- #


async def require_redis_for_production() -> None:
    """Verify Redis is reachable when running in staging or production.

    Call this during application startup (e.g. in a FastAPI lifespan event).
    In development/test environments it logs a warning but does not block startup.
    In staging/production it raises RuntimeError to prevent the application from
    starting with an in-memory-only blacklist, which would be lost on restart
    and not shared across workers.
    """
    r = await _get_redis()
    if r is not None:
        logger.info("Redis connectivity check passed")
        return

    env = getattr(settings, "ENVIRONMENT", "development").lower()
    if env in ("staging", "production"):
        logger.critical(
            "SECURITY [H-02]: Redis is NOT reachable in %s environment. "
            "Token blacklist and account lockout will use in-memory fallback "
            "which is lost on restart and not shared across workers. "
            "Refusing to start.",
            env,
        )
        raise RuntimeError(
            f"Redis is required in {env} environment for security state "
            "(token blacklist, account lockout). Cannot start safely without Redis."
        )
    else:
        logger.warning(
            "SECURITY [H-02]: Redis is not reachable in %s environment. "
            "In-memory fallback will be used. This is acceptable for development "
            "but MUST NOT be used in staging/production.",
            env,
        )
