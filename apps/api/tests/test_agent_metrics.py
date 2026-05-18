"""Tests for the AgentMetricsCollector aggregation logic.

Pin: per-agent invocation recording, summary aggregation (avg
latency, total cost, error rate, avg accuracy with None-skip),
all-summaries sorted output, clear semantics.
"""

from __future__ import annotations

import pytest

from app.services.evaluation.agent_metrics import AgentMetricsCollector

# =========================================================================
# Empty / fresh collector
# =========================================================================


@pytest.fixture
def collector() -> AgentMetricsCollector:
    return AgentMetricsCollector()


def test_get_summary_no_invocations(collector: AgentMetricsCollector):
    """A never-recorded agent returns zero-counts and None for averages."""
    out = collector.get_summary("missing_agent")
    assert out["agent_name"] == "missing_agent"
    assert out["total_invocations"] == 0
    assert out["avg_latency_ms"] is None
    assert out["total_cost_usd"] == 0.0
    assert out["error_rate"] == 0.0
    assert out["avg_accuracy"] is None


def test_all_summaries_empty(collector: AgentMetricsCollector):
    assert collector.get_all_summaries() == []


# =========================================================================
# record_invocation + summary aggregation
# =========================================================================


def test_record_single_invocation(collector: AgentMetricsCollector):
    collector.record_invocation("agent_a", latency_ms=100)
    out = collector.get_summary("agent_a")
    assert out["total_invocations"] == 1
    assert out["avg_latency_ms"] == 100.0


def test_record_aggregates_avg_latency(collector: AgentMetricsCollector):
    """Three invocations: 100, 200, 300 → avg 200."""
    collector.record_invocation("agent_a", latency_ms=100)
    collector.record_invocation("agent_a", latency_ms=200)
    collector.record_invocation("agent_a", latency_ms=300)
    out = collector.get_summary("agent_a")
    assert out["avg_latency_ms"] == 200.0


def test_record_aggregates_total_cost(collector: AgentMetricsCollector):
    collector.record_invocation("agent_a", latency_ms=100, cost_usd=0.50)
    collector.record_invocation("agent_a", latency_ms=100, cost_usd=0.25)
    collector.record_invocation("agent_a", latency_ms=100, cost_usd=0.10)
    out = collector.get_summary("agent_a")
    assert out["total_cost_usd"] == pytest.approx(0.85)


def test_error_rate_no_failures(collector: AgentMetricsCollector):
    """All successes → error_rate = 0."""
    for _ in range(5):
        collector.record_invocation("agent_a", latency_ms=100, success=True)
    out = collector.get_summary("agent_a")
    assert out["error_rate"] == 0.0


def test_error_rate_all_failures(collector: AgentMetricsCollector):
    """All failures → error_rate = 1.0."""
    for _ in range(3):
        collector.record_invocation("agent_a", latency_ms=100, success=False)
    out = collector.get_summary("agent_a")
    assert out["error_rate"] == 1.0


def test_error_rate_partial(collector: AgentMetricsCollector):
    """3 successes + 2 failures = 5 total, 2/5 = 0.4 error rate."""
    for _ in range(3):
        collector.record_invocation("a", latency_ms=100, success=True)
    for _ in range(2):
        collector.record_invocation("a", latency_ms=100, success=False)
    out = collector.get_summary("a")
    assert out["error_rate"] == pytest.approx(0.4)


def test_avg_accuracy_skips_none_values(collector: AgentMetricsCollector):
    """[contract] accuracy=None means "not measured" — must be skipped
    from average, not treated as 0."""
    collector.record_invocation("a", latency_ms=100, accuracy=0.9)
    collector.record_invocation("a", latency_ms=100, accuracy=None)  # not measured
    collector.record_invocation("a", latency_ms=100, accuracy=0.8)
    collector.record_invocation("a", latency_ms=100, accuracy=None)
    out = collector.get_summary("a")
    # Average of 0.9 and 0.8 only (None entries skipped):
    assert out["avg_accuracy"] == pytest.approx(0.85)


def test_avg_accuracy_all_none(collector: AgentMetricsCollector):
    """If no invocation has accuracy data, avg returns None (not 0)."""
    collector.record_invocation("a", latency_ms=100)
    collector.record_invocation("a", latency_ms=100)
    out = collector.get_summary("a")
    assert out["avg_accuracy"] is None


def test_invocations_per_agent_isolated(collector: AgentMetricsCollector):
    """Two agents tracked separately."""
    collector.record_invocation("agent_a", latency_ms=100)
    collector.record_invocation("agent_b", latency_ms=500)
    out_a = collector.get_summary("agent_a")
    out_b = collector.get_summary("agent_b")
    assert out_a["total_invocations"] == 1
    assert out_a["avg_latency_ms"] == 100
    assert out_b["total_invocations"] == 1
    assert out_b["avg_latency_ms"] == 500


# =========================================================================
# get_all_summaries
# =========================================================================


def test_all_summaries_sorted_by_name(collector: AgentMetricsCollector):
    """Return in alphabetical agent-name order — stable for UI / tests."""
    collector.record_invocation("zeta", latency_ms=100)
    collector.record_invocation("alpha", latency_ms=100)
    collector.record_invocation("mu", latency_ms=100)
    summaries = collector.get_all_summaries()
    names = [s["agent_name"] for s in summaries]
    assert names == ["alpha", "mu", "zeta"]


def test_all_summaries_one_per_agent(collector: AgentMetricsCollector):
    """Multiple invocations for the same agent → still ONE summary
    entry per agent."""
    for _ in range(10):
        collector.record_invocation("agent_a", latency_ms=100)
    for _ in range(5):
        collector.record_invocation("agent_b", latency_ms=200)
    summaries = collector.get_all_summaries()
    assert len(summaries) == 2


# =========================================================================
# clear
# =========================================================================


def test_clear_empties_all_metrics(collector: AgentMetricsCollector):
    collector.record_invocation("agent_a", latency_ms=100)
    collector.record_invocation("agent_b", latency_ms=200)
    collector.clear()
    assert collector.get_all_summaries() == []
    # Re-querying after clear still returns the empty-shell summary:
    assert collector.get_summary("agent_a")["total_invocations"] == 0
