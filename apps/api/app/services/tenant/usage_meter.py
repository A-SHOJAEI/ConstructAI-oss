"""Per-tenant resource usage tracking for billing."""

from __future__ import annotations

import logging
from typing import ClassVar

logger = logging.getLogger(__name__)


class UsageMeter:
    """Track per-tenant resource usage for billing."""

    METRIC_TYPES: ClassVar[set[str]] = {
        "api_calls",
        "storage_bytes",
        "inference_minutes",
        "camera_streams",
        "llm_tokens",
        "documents_processed",
    }

    PLAN_LIMITS: ClassVar[dict[str, dict[str, int]]] = {
        "startup": {
            "api_calls": 10_000,
            "storage_bytes": 10 * 1024**3,  # 10 GB
            "camera_streams": 5,
            "llm_tokens": 1_000_000,
            "documents_processed": 500,
        },
        "growth": {
            "api_calls": 100_000,
            "storage_bytes": 100 * 1024**3,
            "camera_streams": 25,
            "llm_tokens": 10_000_000,
            "documents_processed": 5_000,
        },
        "enterprise": {
            "api_calls": -1,  # unlimited
            "storage_bytes": -1,
            "camera_streams": -1,
            "llm_tokens": -1,
            "documents_processed": -1,
        },
    }

    def __init__(self) -> None:
        self._usage: dict[str, dict[str, float]] = {}

    async def record(
        self,
        org_id: str,
        metric_type: str,
        value: float,
    ) -> None:
        """Record a usage metric."""
        if metric_type not in self.METRIC_TYPES:
            raise ValueError(
                f"Invalid metric type: {metric_type}",
            )
        if org_id not in self._usage:
            self._usage[org_id] = {}
        current = self._usage[org_id].get(metric_type, 0.0)
        self._usage[org_id][metric_type] = current + value
        logger.debug(
            "Recorded %s=%s for org %s",
            metric_type,
            value,
            org_id,
        )

    async def get_usage(self, org_id: str) -> dict:
        """Get current usage for a tenant."""
        return dict(self._usage.get(org_id, {}))

    async def check_limit(
        self,
        org_id: str,
        metric_type: str,
        billing_plan: str,
    ) -> tuple[bool, float]:
        """Check if tenant is within limits.

        Returns (within_limit, usage_percent).
        """
        limits = self.PLAN_LIMITS.get(billing_plan, {})
        limit = limits.get(metric_type, -1)
        if limit == -1:
            return True, 0.0
        current = self._usage.get(org_id, {}).get(
            metric_type,
            0.0,
        )
        percent = (current / limit * 100) if limit > 0 else 0.0
        return current <= limit, round(percent, 2)
