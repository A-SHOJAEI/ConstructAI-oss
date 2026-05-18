"""Per-agent accuracy, latency, cost metric tracking."""

from __future__ import annotations

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class AgentMetricsCollector:
    """Collect and aggregate per-agent metrics.

    Tracks accuracy, latency, cost, and error rates.
    """

    def __init__(self):
        self._metrics: dict[str, list[dict]] = defaultdict(list)

    def record_invocation(
        self,
        agent_name: str,
        latency_ms: int,
        cost_usd: float = 0.0,
        success: bool = True,
        accuracy: float | None = None,
    ):
        """Record a single agent invocation."""
        self._metrics[agent_name].append(
            {
                "latency_ms": latency_ms,
                "cost_usd": cost_usd,
                "success": success,
                "accuracy": accuracy,
            }
        )

    def get_summary(
        self,
        agent_name: str,
    ) -> dict:
        """Get aggregated metrics for an agent."""
        records = self._metrics.get(agent_name, [])
        if not records:
            return {
                "agent_name": agent_name,
                "total_invocations": 0,
                "avg_latency_ms": None,
                "total_cost_usd": 0.0,
                "error_rate": 0.0,
                "avg_accuracy": None,
            }

        total = len(records)
        successes = sum(1 for r in records if r["success"])
        accuracies = [r["accuracy"] for r in records if r["accuracy"] is not None]

        return {
            "agent_name": agent_name,
            "total_invocations": total,
            "avg_latency_ms": (sum(r["latency_ms"] for r in records) / total),
            "total_cost_usd": sum(r["cost_usd"] for r in records),
            "error_rate": ((total - successes) / total if total else 0.0),
            "avg_accuracy": (sum(accuracies) / len(accuracies) if accuracies else None),
        }

    def get_all_summaries(self) -> list[dict]:
        """Get summaries for all tracked agents."""
        return [self.get_summary(name) for name in sorted(self._metrics.keys())]

    def clear(self):
        """Clear all metrics (for testing)."""
        self._metrics.clear()
