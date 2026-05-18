"""Tests for evaluation runner."""

from __future__ import annotations

from app.services.evaluation.eval_harness import (
    BENCHMARK_TARGETS,
    EvaluationHarness,
)


class TestEvaluationHarness:
    async def test_run_all_benchmarks(self):
        harness = EvaluationHarness()
        results = await harness.run_nightly_evaluation()
        assert len(results) > 0
        for r in results:
            assert "agent_name" in r
            assert "metric_value" in r

    async def test_run_specific_agent(self):
        harness = EvaluationHarness()
        results = await harness.run_nightly_evaluation(
            agent_names=["safety_agent"],
        )
        assert len(results) == 1
        assert results[0]["agent_name"] == "safety_agent"

    async def test_benchmark_targets_exist(self):
        assert "estimating_agent" in BENCHMARK_TARGETS
        assert "safety_agent" in BENCHMARK_TARGETS
        assert "document_agent" in BENCHMARK_TARGETS

    async def test_results_have_targets(self):
        harness = EvaluationHarness()
        results = await harness.run_nightly_evaluation()
        for r in results:
            assert "benchmark_target" in r
            assert r["benchmark_target"] > 0

    async def test_results_stored(self):
        harness = EvaluationHarness()
        await harness.run_nightly_evaluation()
        stored = harness.get_results()
        assert len(stored) > 0

    async def test_clear_results(self):
        harness = EvaluationHarness()
        await harness.run_nightly_evaluation()
        harness.clear()
        assert len(harness.get_results()) == 0
