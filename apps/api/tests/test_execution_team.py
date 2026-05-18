"""Tests for the Execution Team LangGraph supervisor.

Pin the documented controls/productivity keyword buckets — note the
``forecast`` overlap (it appears in both buckets, which means a
single 'forecast' keyword resolves to 'full' due to tied score).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.agents.execution_team import (
    _TASK_KEYWORDS,
    _infer_task_type,
    analyze_node,
    build_execution_team,
    compile_report_node,
    route_agents,
    run_controls_node,
    run_productivity_node,
)

# =========================================================================
# _TASK_KEYWORDS
# =========================================================================


def test_task_keywords_canonical_buckets():
    assert set(_TASK_KEYWORDS) == {"controls", "productivity"}


def test_controls_keywords_pinned():
    assert "evm" in _TASK_KEYWORDS["controls"]
    assert "earned value" in _TASK_KEYWORDS["controls"]
    assert "s-curve" in _TASK_KEYWORDS["controls"]
    assert "variance" in _TASK_KEYWORDS["controls"]


def test_productivity_keywords_pinned():
    assert "productivity" in _TASK_KEYWORDS["productivity"]
    assert "crew" in _TASK_KEYWORDS["productivity"]
    assert "telemetry" in _TASK_KEYWORDS["productivity"]
    assert "utilization" in _TASK_KEYWORDS["productivity"]


def test_forecast_keyword_overlaps_both_buckets():
    """[invariant] 'forecast' deliberately appears in BOTH buckets so a
    bare 'forecast' query routes to 'full' (run both agents). Pin so a
    refactor doesn't accidentally remove it from one bucket."""
    assert "forecast" in _TASK_KEYWORDS["controls"]
    assert "forecast" in _TASK_KEYWORDS["productivity"]


# =========================================================================
# _infer_task_type
# =========================================================================


def test_infer_pure_controls():
    assert _infer_task_type("Compute the EVM variance") == "controls"


def test_infer_pure_productivity():
    assert _infer_task_type("Analyze crew utilization data") == "productivity"


def test_infer_no_keywords_returns_full():
    assert _infer_task_type("Hello") == "full"


def test_infer_only_forecast_returns_full():
    """[edge case] Only 'forecast' word present -> 1 in both buckets ->
    tie -> 'full'. This is the practical consequence of the
    overlap design."""
    assert _infer_task_type("forecast for the next month") == "full"


def test_infer_tied_scores_returns_full():
    assert _infer_task_type("EVM crew") == "full"


def test_infer_higher_score_wins():
    """3 controls keywords vs 1 productivity -> controls."""
    out = _infer_task_type("EVM earned value variance crew")
    # 3 controls (evm, earned value, variance), 1 productivity (crew):
    assert out == "controls"


def test_infer_case_insensitive():
    assert _infer_task_type("EVM VARIANCE") == "controls"


# =========================================================================
# analyze_node
# =========================================================================


@pytest.mark.asyncio
async def test_analyze_explicit_passthrough():
    out = await analyze_node({"request": "x", "task_type": "controls"})
    assert out["task_type"] == "controls"
    assert out["status"] == "analyzed"


@pytest.mark.asyncio
async def test_analyze_auto_infers():
    out = await analyze_node({"request": "EVM forecast variance", "task_type": "auto"})
    assert out["task_type"] == "controls"


# =========================================================================
# route_agents
# =========================================================================


def test_route_full_runs_both():
    assert set(route_agents({"task_type": "full"})) == {"run_controls", "run_productivity"}


def test_route_controls_only():
    assert route_agents({"task_type": "controls"}) == ["run_controls"]


def test_route_productivity_only():
    assert route_agents({"task_type": "productivity"}) == ["run_productivity"]


def test_route_default_full():
    assert set(route_agents({})) == {"run_controls", "run_productivity"}


# =========================================================================
# Per-node success + failure isolation
# =========================================================================


@pytest.mark.asyncio
async def test_controls_node_success():
    async def fake(*_args, **_kwargs):
        return {"cpi": 0.95, "status": "completed"}

    with patch("app.services.agents.execution_team.run_controls_agent", fake):
        out = await run_controls_node({"project_id": "p-1"})
    assert out["status"] == "controls_complete"
    assert out["controls_results"]["cpi"] == 0.95


@pytest.mark.asyncio
async def test_controls_node_failure_isolated():
    async def fake(*_args, **_kwargs):
        raise RuntimeError("controls timeout")

    with patch("app.services.agents.execution_team.run_controls_agent", fake):
        out = await run_controls_node({"project_id": "p-1"})
    assert out["status"] == "controls_failed"
    assert "controls timeout" in out["error"]
    assert out["controls_results"]["status"] == "failed"


@pytest.mark.asyncio
async def test_productivity_node_success():
    async def fake(*_args, **_kwargs):
        return {"utilization_pct": 73.5, "status": "completed"}

    with patch("app.services.agents.execution_team.run_productivity_agent", fake):
        out = await run_productivity_node({"project_id": "p-1"})
    assert out["status"] == "productivity_complete"


@pytest.mark.asyncio
async def test_productivity_node_failure_isolated():
    async def fake(*_args, **_kwargs):
        raise ValueError("data missing")

    with patch("app.services.agents.execution_team.run_productivity_agent", fake):
        out = await run_productivity_node({"project_id": "p-1"})
    assert out["status"] == "productivity_failed"
    assert "data missing" in out["error"]


# =========================================================================
# compile_report_node
# =========================================================================


@pytest.mark.asyncio
async def test_compile_report_assembles_state():
    state = {
        "project_id": "p-1",
        "task_type": "full",
        "controls_results": {"cpi": 0.95},
        "productivity_results": {"utilization": 73.5},
        "human_feedback": "approved",
    }
    out = await compile_report_node(state)
    assert out["status"] == "report_compiled"
    report = out["final_report"]
    assert report["project_id"] == "p-1"
    assert report["controls"] == {"cpi": 0.95}
    assert report["productivity"] == {"utilization": 73.5}
    assert report["human_feedback"] == "approved"


@pytest.mark.asyncio
async def test_compile_report_handles_missing_optionals():
    state = {
        "project_id": "p-1",
        "task_type": "controls",
        "controls_results": {"cpi": 0.9},
        "productivity_results": None,
        "human_feedback": None,
    }
    out = await compile_report_node(state)
    assert out["final_report"]["productivity"] is None
    assert out["status"] == "report_compiled"


# =========================================================================
# Graph build
# =========================================================================


def test_build_execution_team_returns_compiled_graph():
    graph = build_execution_team()
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    assert {
        "analyze",
        "run_controls",
        "run_productivity",
        "human_review",
        "compile_report",
    } <= nodes
