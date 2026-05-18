from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ABTestingFramework:
    """A/B testing framework for agent variants."""

    def __init__(self):
        self._experiments: dict[str, dict] = {}
        self._results: dict[str, list[dict]] = {}

    async def create_experiment(
        self,
        name: str,
        variants: list[str],
        traffic_split: list[int] | None = None,
    ) -> dict:
        """Create an A/B test experiment."""
        if not variants or len(variants) < 2:
            raise ValueError("Need at least 2 variants")
        split = traffic_split or ([100 // len(variants)] * len(variants))
        experiment = {
            "name": name,
            "variants": variants,
            "traffic_split": split,
            "status": "active",
        }
        self._experiments[name] = experiment
        self._results[name] = []
        logger.info(
            "Created experiment %s with variants %s",
            name,
            variants,
        )
        return experiment

    def assign_variant(
        self,
        experiment_name: str,
        user_id: str,
    ) -> str:
        """Assign a user to an experiment variant (deterministic)."""
        experiment = self._experiments.get(experiment_name)
        if not experiment or experiment["status"] != "active":
            return experiment["variants"][0] if experiment else "control"
        hash_val = hash(f"{experiment_name}:{user_id}") % 100
        cumulative = 0
        for i, pct in enumerate(experiment["traffic_split"]):
            cumulative += pct
            if hash_val < cumulative:
                return experiment["variants"][i]
        return experiment["variants"][-1]

    async def record_result(
        self,
        experiment_name: str,
        variant: str,
        metric_name: str,
        value: float,
    ):
        """Record a metric result for an experiment variant."""
        if experiment_name not in self._results:
            self._results[experiment_name] = []
        self._results[experiment_name].append(
            {
                "variant": variant,
                "metric": metric_name,
                "value": value,
            }
        )

    async def get_results(
        self,
        experiment_name: str,
    ) -> dict:
        """Get aggregated results for an experiment."""
        results = self._results.get(experiment_name, [])
        variants_data: dict[str, list[float]] = {}
        for r in results:
            v = r["variant"]
            if v not in variants_data:
                variants_data[v] = []
            variants_data[v].append(r["value"])
        summary = {}
        for variant, values in variants_data.items():
            summary[variant] = {
                "count": len(values),
                "mean": (sum(values) / len(values) if values else 0),
            }
        return {
            "experiment": experiment_name,
            "variants": summary,
        }
