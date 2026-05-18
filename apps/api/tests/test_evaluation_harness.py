"""Tests for the nightly evaluation harness.

Pin agent benchmark targets, the metric-type pass/fail logic
(MAPE is lower-is-better, others are higher-is-better), and the
filter + result-collection contract.
"""

from __future__ import annotations

import pytest

from app.services.evaluation.eval_harness import (
    BENCHMARK_TARGETS,
    EvaluationHarness,
)

# =========================================================================
# BENCHMARK_TARGETS — pin canonical agent benchmarks
# =========================================================================


def test_benchmark_targets_canonical_agents():
    """Pin documented agents — refactor must not silently drop one."""
    expected = {
        "estimating_agent",
        "safety_agent",
        "scheduling_agent",
        "document_agent",
        "quality_agent",
    }
    assert set(BENCHMARK_TARGETS.keys()) == expected


def test_estimating_agent_has_mape_targets():
    """Estimating agent uses MAPE metric (lower is better)."""
    targets = BENCHMARK_TARGETS["estimating_agent"]
    assert "mape_conceptual" in targets
    assert "mape_detailed" in targets
    assert targets["mape_conceptual"]["metric"] == "mape"
    assert targets["mape_detailed"]["metric"] == "mape"


def test_estimating_detailed_target_lower_than_conceptual():
    """Detailed estimates have stricter accuracy target than
    conceptual — pin the relationship."""
    targets = BENCHMARK_TARGETS["estimating_agent"]
    assert targets["mape_detailed"]["target"] < targets["mape_conceptual"]["target"]


def test_safety_agent_map_target():
    """[business invariant] Safety mAP@0.5 ≥ 0.85 — high bar
    because safety alerts must be reliable."""
    targets = BENCHMARK_TARGETS["safety_agent"]
    assert targets["map_50"]["target"] == 0.85


def test_scheduling_critical_path_accuracy_high():
    """Critical-path accuracy ≥ 0.90 — schedule integrity is
    business-critical."""
    targets = BENCHMARK_TARGETS["scheduling_agent"]
    assert targets["critical_path_accuracy"]["target"] >= 0.85


def test_document_agent_dual_metrics():
    """Document agent has two metrics: precision_at_5 and
    classification_accuracy."""
    targets = BENCHMARK_TARGETS["document_agent"]
    assert "precision_at_5" in targets
    assert "classification_accuracy" in targets


def test_each_benchmark_has_target_and_metric():
    """Pin schema invariants."""
    for agent, benchmarks in BENCHMARK_TARGETS.items():
        for metric_name, config in benchmarks.items():
            assert "target" in config, f"{agent}/{metric_name} missing target"
            assert "metric" in config, f"{agent}/{metric_name} missing metric"
            assert isinstance(config["target"], int | float)


# =========================================================================
# _check_pass — metric-type-specific pass logic
# =========================================================================


@pytest.fixture
def harness() -> EvaluationHarness:
    return EvaluationHarness()


def test_check_pass_mape_lower_is_better(harness: EvaluationHarness):
    """[business invariant] MAPE (Mean Absolute Percentage Error) is
    LOWER-is-better. Pin so a refactor doesn't flip the comparison
    and falsely pass a high-error agent."""
    # Value 0.10 (10% error) ≤ target 0.15 → pass
    assert harness._check_pass("mape", 0.10, 0.15) is True
    # Value 0.20 > target 0.15 → fail
    assert harness._check_pass("mape", 0.20, 0.15) is False


def test_check_pass_mape_at_exact_target_passes(harness: EvaluationHarness):
    """At exactly the target, ≤ comparison passes."""
    assert harness._check_pass("mape", 0.15, 0.15) is True


def test_check_pass_other_metrics_higher_is_better(harness: EvaluationHarness):
    """Accuracy, precision, mAP — all higher-is-better."""
    # Value 0.90 ≥ target 0.85 → pass
    assert harness._check_pass("accuracy", 0.90, 0.85) is True
    assert harness._check_pass("precision", 0.85, 0.80) is True
    assert harness._check_pass("map", 0.92, 0.85) is True
    # Value 0.70 < target 0.85 → fail
    assert harness._check_pass("accuracy", 0.70, 0.85) is False


def test_check_pass_other_at_exact_target_passes(harness: EvaluationHarness):
    """At exactly the target, ≥ comparison passes."""
    assert harness._check_pass("accuracy", 0.85, 0.85) is True


def test_check_pass_unknown_metric_treated_as_higher_is_better(harness: EvaluationHarness):
    """Unknown metric type defaults to higher-is-better — pin the
    documented fallback."""
    assert harness._check_pass("mystery_metric", 0.95, 0.85) is True
    assert harness._check_pass("mystery_metric", 0.80, 0.85) is False


# =========================================================================
# run_nightly_evaluation
# =========================================================================


@pytest.mark.asyncio
async def test_run_nightly_runs_all_agents(harness: EvaluationHarness):
    """Without a filter, runs every benchmark in BENCHMARK_TARGETS."""
    results = await harness.run_nightly_evaluation()
    # Total benchmarks across all agents:
    total = sum(len(b) for b in BENCHMARK_TARGETS.values())
    assert len(results) == total


@pytest.mark.asyncio
async def test_run_nightly_filter_by_agent_names(harness: EvaluationHarness):
    """Filtering to specific agents only runs their benchmarks."""
    results = await harness.run_nightly_evaluation(agent_names=["safety_agent"])
    # Safety agent has 1 benchmark:
    assert len(results) == 1
    assert results[0]["agent_name"] == "safety_agent"


@pytest.mark.asyncio
async def test_run_nightly_unknown_agent_returns_empty(harness: EvaluationHarness):
    """Filter that matches no agents → empty results."""
    results = await harness.run_nightly_evaluation(agent_names=["nonexistent"])
    assert results == []


@pytest.mark.asyncio
async def test_run_nightly_results_have_required_fields(harness: EvaluationHarness):
    results = await harness.run_nightly_evaluation(agent_names=["safety_agent"])
    for result in results:
        assert "agent_name" in result
        assert "metric_name" in result
        assert "metric_value" in result
        assert "benchmark_target" in result
        assert "evaluation_date" in result


@pytest.mark.asyncio
async def test_run_nightly_appends_to_results(harness: EvaluationHarness):
    """Each run appends to the harness results buffer."""
    await harness.run_nightly_evaluation(agent_names=["safety_agent"])
    await harness.run_nightly_evaluation(agent_names=["safety_agent"])
    assert len(harness.get_results()) == 2


@pytest.mark.asyncio
async def test_run_nightly_filter_by_multiple_agents(harness: EvaluationHarness):
    """Multiple-agent filter runs benchmarks for each."""
    results = await harness.run_nightly_evaluation(agent_names=["safety_agent", "quality_agent"])
    agent_names = {r["agent_name"] for r in results}
    assert agent_names == {"safety_agent", "quality_agent"}


# =========================================================================
# get_results / clear
# =========================================================================


def test_get_results_empty_initially(harness: EvaluationHarness):
    assert harness.get_results() == []


def test_get_results_returns_list(harness: EvaluationHarness):
    """Pin: returns a list (not the internal collection)."""
    out = harness.get_results()
    assert isinstance(out, list)


def test_get_results_returns_copy(harness: EvaluationHarness):
    """Caller mutation must NOT leak into harness state."""
    harness._results.append({"agent_name": "test"})
    out = harness.get_results()
    out.clear()
    # Internal state preserved:
    assert len(harness._results) == 1


@pytest.mark.asyncio
async def test_clear_resets_results(harness: EvaluationHarness):
    await harness.run_nightly_evaluation(agent_names=["safety_agent"])
    assert harness.get_results()
    harness.clear()
    assert harness.get_results() == []
