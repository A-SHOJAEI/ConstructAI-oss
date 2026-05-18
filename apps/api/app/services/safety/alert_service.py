"""Alert generation, deduplication, and storage."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# In-memory dedup cache (Redis is primary when available)
_dedup_cache: dict[str, dict] = {}
DEDUP_WINDOW_SECONDS = 60
_DEDUP_CACHE_MAX_SIZE = 10_000

# Periodic cleanup: track last cleanup time
_last_cleanup: datetime = datetime.now(UTC)
_CLEANUP_INTERVAL_SECONDS = 300  # 5 minutes

# Lock for async-safe access to dedup cache and cleanup timestamp
_dedup_lock = asyncio.Lock()


async def _get_redis():
    """Lazily connect to Redis. Returns None if unavailable."""
    try:
        from app.services.security.redis_state import _get_redis as _get_shared_redis

        return await _get_shared_redis()
    except Exception as e:
        logger.warning(f"Redis connection failed, falling back to in-memory dedup: {e}")
        return None


def _make_dedup_key(camera_id: str, zone_id: str, alert_type: str, track_id: str) -> str:
    raw = f"{camera_id}:{zone_id}:{alert_type}:{track_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _cleanup_expired_entries() -> None:
    """Remove expired entries from the in-memory dedup cache."""
    global _last_cleanup
    now = datetime.now(UTC)
    if (now - _last_cleanup).total_seconds() < _CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup = now
    cutoff = now - timedelta(seconds=DEDUP_WINDOW_SECONDS)
    expired = [k for k, v in _dedup_cache.items() if v["last_seen"] < cutoff]
    for k in expired:
        del _dedup_cache[k]
    if expired:
        logger.debug("Cleaned up %d expired dedup entries", len(expired))


async def is_duplicate(camera_id: str, zone_id: str, alert_type: str, track_id: str) -> bool:
    """Check if this alert is a duplicate within the dedup window."""
    key = _make_dedup_key(camera_id, zone_id, alert_type, track_id)
    redis_key = f"cai:dedup:{key}"

    # Try Redis SETNX first
    r = await _get_redis()
    if r is not None:
        try:
            was_set = await r.set(redis_key, "1", nx=True, ex=DEDUP_WINDOW_SECONDS)
            if not was_set:
                # Key already existed -- duplicate
                return True
            # Key was freshly set -- not a duplicate
            return False
        except Exception:
            logger.warning("Redis dedup check failed, falling back to in-memory")

    # Fallback to in-memory (guarded with asyncio.Lock for concurrent safety)
    async with _dedup_lock:
        _cleanup_expired_entries()
        now = datetime.now(UTC)
        entry = _dedup_cache.get(key)
        if entry and (now - entry["last_seen"]) < timedelta(seconds=DEDUP_WINDOW_SECONDS):
            entry["last_seen"] = now
            entry["count"] += 1
            return True
        # Cap the dedup cache size to prevent unbounded memory growth.
        if len(_dedup_cache) >= _DEDUP_CACHE_MAX_SIZE:
            evict_count = max(1, len(_dedup_cache) // 4)
            sorted_keys = sorted(
                _dedup_cache.keys(),
                key=lambda k: _dedup_cache[k]["last_seen"],
            )
            for k in sorted_keys[:evict_count]:
                del _dedup_cache[k]
            logger.warning(
                "Dedup cache reached max size (%d), evicted %d oldest entries",
                _DEDUP_CACHE_MAX_SIZE,
                evict_count,
            )
        _dedup_cache[key] = {"last_seen": now, "count": 1, "alert_id": None}
        return False


def create_alert_record(
    project_id: str,
    camera_id: str,
    zone_id: str | None,
    priority: str,
    alert_type: str,
    description: str,
    detections: list[dict],
    confidence: float,
    frame_s3_key: str | None = None,
) -> dict:
    """Create an alert record dict ready for DB insertion."""
    return {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "camera_id": camera_id,
        "zone_id": zone_id,
        "priority": priority,
        "alert_type": alert_type,
        "description": description,
        "detections": detections,
        "confidence": confidence,
        "frame_s3_key": frame_s3_key,
        "is_acknowledged": False,
        "is_false_positive": None,
        "created_at": datetime.now(UTC).isoformat(),
    }


async def process_safety_event(event: dict) -> dict | None:
    """Process a confirmed safety event into an alert (with dedup)."""
    from app.services.safety.severity_classifier import classify_severity

    camera_id = event["camera_id"]
    violation = event["violation"]
    detection = event["detection"]
    zone_id = violation.get("zone_id", "")
    violation_type = violation.get("violation", "other")
    zone_type = violation.get("zone_type", "general")
    track_id = str(detection.get("track_id", ""))

    # Deduplication
    alert_type = "ppe_violation" if "missing_" in violation_type else "zone_breach"
    if await is_duplicate(camera_id, zone_id, alert_type, track_id):
        return None

    # Classify severity
    severity = classify_severity(
        zone_type=zone_type,
        violation_type=violation_type,
        confidence=detection.get("confidence", 0.5),
        severity_override=violation.get("severity_override"),
    )

    description = _generate_description(violation_type, zone_type, detection)

    return create_alert_record(
        project_id=event.get("project_id", ""),
        camera_id=camera_id,
        zone_id=zone_id if zone_id else None,
        priority=severity,
        alert_type=alert_type,
        description=description,
        detections=[detection],
        confidence=detection.get("confidence", 0.5),
    )


def _generate_description(violation_type: str, zone_type: str, detection: dict) -> str:
    class_name = detection.get("class_name", "object")
    if violation_type == "zone_breach":
        return f"{class_name.title()} detected in {zone_type} zone"
    if violation_type.startswith("missing_"):
        item = violation_type.replace("missing_", "")
        return f"PPE violation: {class_name.title()} missing {item}"
    return f"Safety event: {violation_type} in {zone_type} zone"


def clear_dedup_cache():
    """Clear the in-memory deduplication cache (for testing)."""
    _dedup_cache.clear()


async def clear_dedup_cache_async() -> None:
    """Clear both in-memory cache and Redis dedup keys (for testing).

    Production primary store is Redis; the in-memory dict is just a fallback.
    Tests that exercise the dedup window must flush both, or stale keys from
    earlier tests in the same run cause a fresh event to look like a
    duplicate.
    """
    _dedup_cache.clear()
    r = await _get_redis()
    if r is None:
        return
    try:
        keys = [key async for key in r.scan_iter(match="cai:dedup:*")]
        if keys:
            await r.delete(*keys)
    except Exception:
        logger.debug("Failed to clear Redis dedup keys (non-fatal in tests)")
