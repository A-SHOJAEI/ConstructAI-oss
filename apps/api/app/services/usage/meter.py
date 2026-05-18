"""Usage metering for ConstructAI platform.

Tracks API calls, storage usage, AI inference counts, and user activity
for billing and analytics purposes.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime

logger = logging.getLogger(__name__)

_REDIS_TTL_SECONDS = 30 * 24 * 3600  # 30 days


@dataclass
class UsageRecord:
    org_id: str
    metric: str
    value: float
    recorded_at: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.recorded_at:
            self.recorded_at = datetime.now(UTC).isoformat()


@dataclass
class UsageSummary:
    org_id: str
    period_start: str
    period_end: str
    api_calls: int = 0
    ai_inferences: int = 0
    storage_bytes: int = 0
    active_users: int = 0
    projects: int = 0
    documents_processed: int = 0
    safety_alerts: int = 0


async def _get_redis():
    """Lazily connect to Redis. Returns None if unavailable."""
    try:
        from app.services.security.redis_state import _get_redis as _get_shared_redis

        return await _get_shared_redis()
    except Exception:
        return None


_MAX_RECORDS = 50_000
_TRIM_SIZE = 10_000


class UsageMeter:
    """Track and aggregate platform usage metrics."""

    def __init__(self):
        self._records: list[UsageRecord] = []

    async def record(
        self,
        org_id: str,
        metric: str,
        value: float = 1.0,
        metadata: dict | None = None,
    ) -> None:
        """Record a usage event."""
        record = UsageRecord(
            org_id=org_id,
            metric=metric,
            value=value,
            metadata=metadata or {},
        )
        if len(self._records) >= _MAX_RECORDS:
            self._records = self._records[_TRIM_SIZE:]
        self._records.append(record)

        # Persist to Redis for cross-process durability
        r = await _get_redis()
        if r is not None:
            try:
                key = f"cai:usage:{org_id}"
                await r.rpush(key, json.dumps(asdict(record)))
                await r.expire(key, _REDIS_TTL_SECONDS)
            except Exception:
                logger.warning("Redis usage record push failed, in-memory only")

        logger.debug(
            "Usage: org=%s metric=%s value=%.1f",
            org_id,
            metric,
            value,
        )

    async def record_api_call(self, org_id: str, endpoint: str) -> None:
        """Record an API call."""
        await self.record(org_id, "api_call", metadata={"endpoint": endpoint})

    async def record_ai_inference(self, org_id: str, model: str, latency_ms: float) -> None:
        """Record an AI inference."""
        await self.record(
            org_id,
            "ai_inference",
            metadata={"model": model, "latency_ms": latency_ms},
        )

    async def record_storage(self, org_id: str, bytes_added: int) -> None:
        """Record storage usage change."""
        await self.record(org_id, "storage_bytes", value=float(bytes_added))

    async def record_document_processed(self, org_id: str, doc_type: str) -> None:
        """Record a document processing event."""
        await self.record(org_id, "document_processed", metadata={"type": doc_type})

    async def get_summary(
        self,
        org_id: str,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> UsageSummary:
        """Get aggregated usage summary for an organization."""
        start = period_start or date.today().replace(day=1).isoformat()
        end = period_end or date.today().isoformat()

        records: list[UsageRecord] = []

        # Try Redis first for durable records
        r = await _get_redis()
        if r is not None:
            try:
                key = f"cai:usage:{org_id}"
                raw_items = await r.lrange(key, 0, -1)
                for raw in raw_items:
                    data = json.loads(raw)
                    rec = UsageRecord(**data)
                    if start <= rec.recorded_at[:10] <= end:
                        records.append(rec)
            except Exception:
                logger.warning("Redis usage read failed, falling back to in-memory")
                records = []

        # Fallback to in-memory if Redis returned nothing
        if not records:
            records = [
                r
                for r in self._records
                if r.org_id == org_id and start <= r.recorded_at[:10] <= end
            ]

        api_calls = sum(1 for r in records if r.metric == "api_call")
        ai_inferences = sum(1 for r in records if r.metric == "ai_inference")
        storage = sum(int(r.value) for r in records if r.metric == "storage_bytes")
        docs = sum(1 for r in records if r.metric == "document_processed")

        return UsageSummary(
            org_id=org_id,
            period_start=start,
            period_end=end,
            api_calls=api_calls,
            ai_inferences=ai_inferences,
            storage_bytes=storage,
            documents_processed=docs,
        )


# Singleton instance
_meter: UsageMeter | None = None
_meter_lock = threading.Lock()


def get_usage_meter() -> UsageMeter:
    """Get or create the usage meter singleton."""
    global _meter
    if _meter is None:
        with _meter_lock:
            if _meter is None:
                _meter = UsageMeter()
    return _meter
