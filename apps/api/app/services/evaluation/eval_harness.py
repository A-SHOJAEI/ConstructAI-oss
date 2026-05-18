"""Nightly evaluation runner for all agents."""

from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)

# Agent benchmark targets
BENCHMARK_TARGETS = {
    "estimating_agent": {
        "mape_conceptual": {"target": 0.15, "metric": "mape"},
        "mape_detailed": {"target": 0.10, "metric": "mape"},
    },
    "safety_agent": {
        "map_50": {"target": 0.85, "metric": "map"},
    },
    "scheduling_agent": {
        "critical_path_accuracy": {
            "target": 0.90,
            "metric": "accuracy",
        },
    },
    "document_agent": {
        "precision_at_5": {
            "target": 0.80,
            "metric": "precision",
        },
        "classification_accuracy": {
            "target": 0.90,
            "metric": "accuracy",
        },
    },
    "quality_agent": {
        "defect_detection_accuracy": {
            "target": 0.85,
            "metric": "accuracy",
        },
    },
}


class EvaluationHarness:
    """Nightly batch evaluation runner.

    Runs construction-specific benchmarks for all agents.
    Results stored in agent_evaluations table.
    """

    def __init__(self):
        self._results: list[dict] = []

    async def run_nightly_evaluation(
        self,
        agent_names: list[str] | None = None,
    ) -> list[dict]:
        """Run all registered benchmarks.

        Args:
            agent_names: Optional filter for specific agents.
                If None, runs all.

        Returns list of evaluation results.
        """
        targets = BENCHMARK_TARGETS
        if agent_names:
            targets = {k: v for k, v in targets.items() if k in agent_names}

        results = []
        for agent_name, benchmarks in targets.items():
            for metric_name, config in benchmarks.items():
                try:
                    result = await self._run_benchmark(
                        agent_name,
                        metric_name,
                        config,
                    )
                    results.append(result)
                except Exception as e:
                    logger.error(
                        "Benchmark %s/%s failed: %s",
                        agent_name,
                        metric_name,
                        str(e),
                    )
                    results.append(
                        {
                            "agent_name": agent_name,
                            "metric_name": metric_name,
                            "metric_value": 0.0,
                            "benchmark_target": config["target"],
                            "evaluation_date": str(date.today()),
                            "details": {"error": str(e)},
                        }
                    )

        self._results.extend(results)
        logger.info(
            "Nightly evaluation complete: %d results",
            len(results),
        )
        return results

    async def _run_benchmark(
        self,
        agent_name: str,
        metric_name: str,
        config: dict,
    ) -> dict:
        """Run a single benchmark.

        In production, loads test dataset and runs agent.
        Currently returns not_implemented status.
        """
        return {
            "agent_name": agent_name,
            "metric_name": metric_name,
            "metric_value": 0.0,
            "benchmark_target": config["target"],
            "evaluation_date": str(date.today()),
            "status": "not_implemented",
            "message": "Benchmark execution not yet implemented",
            "details": {
                "metric_type": config["metric"],
                "passed": False,
            },
        }

    def _check_pass(
        self,
        metric_type: str,
        value: float,
        target: float,
    ) -> bool:
        """Check if metric passes benchmark."""
        if metric_type == "mape":
            return value <= target
        return value >= target

    def get_results(self) -> list[dict]:
        """Get all evaluation results."""
        return list(self._results)

    def clear(self):
        """Clear results (for testing)."""
        self._results.clear()
