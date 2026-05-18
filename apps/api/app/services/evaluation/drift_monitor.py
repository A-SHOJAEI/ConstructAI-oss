"""Evidently AI drift detection for model monitoring."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class DriftMonitor:
    """Monitor data and model drift for agent outputs.

    In production, uses Evidently AI for statistical
    drift detection. Here uses simplified checks.
    """

    def __init__(self):
        self._reference_stats: dict[str, dict] = {}
        self._current_stats: dict[str, dict] = {}

    def set_reference(
        self,
        metric_name: str,
        values: list[float],
    ):
        """Set reference distribution for a metric."""
        if not values:
            return
        self._reference_stats[metric_name] = {
            "mean": sum(values) / len(values),
            "std": self._std(values),
            "min": min(values),
            "max": max(values),
            "count": len(values),
        }

    def add_current(
        self,
        metric_name: str,
        values: list[float],
    ):
        """Add current period values for comparison."""
        if not values:
            return
        self._current_stats[metric_name] = {
            "mean": sum(values) / len(values),
            "std": self._std(values),
            "min": min(values),
            "max": max(values),
            "count": len(values),
        }

    async def detect_drift(
        self,
        threshold: float = 2.0,
    ) -> list[dict]:
        """Detect drift between reference and current.

        Uses simplified z-score based drift detection.
        Returns list of detected drifts.
        """
        drifts = []
        for metric_name in self._reference_stats:
            ref = self._reference_stats[metric_name]
            cur = self._current_stats.get(metric_name)
            if not cur:
                continue

            ref_std = ref["std"] if ref["std"] > 0 else abs(ref["mean"]) * 0.01 or 0.001
            z_score = abs(cur["mean"] - ref["mean"]) / ref_std

            is_drifted = z_score > threshold

            drifts.append(
                {
                    "metric_name": metric_name,
                    "drifted": is_drifted,
                    "z_score": round(z_score, 4),
                    "reference_mean": round(ref["mean"], 4),
                    "current_mean": round(cur["mean"], 4),
                    "threshold": threshold,
                }
            )

            if is_drifted:
                logger.warning(
                    "Drift detected for %s: z=%.2f (ref=%.4f, cur=%.4f)",
                    metric_name,
                    z_score,
                    ref["mean"],
                    cur["mean"],
                )

        return drifts

    def _std(self, values: list[float]) -> float:
        """Calculate standard deviation."""
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        return variance**0.5

    def clear(self):
        """Clear all stats (for testing)."""
        self._reference_stats.clear()
        self._current_stats.clear()
