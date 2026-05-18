"""Tests for the Planning Team LangGraph supervisor.

Pin the documented plan-type keywords (estimating/scheduling/
logistics/procurement), per-node error isolation, the
overall-status aggregation (complete vs partial_failure vs
completed_with_gaps), and the executive summary format.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.agents.planning_team import (
    _PLAN_TYPE_KEYWORDS,
    _build_executive_summary,
    _infer_plan_type,
    analyze_request_node,
    build_planning_team,
    compile_plan_node,
    route_agents,
    run_estimating_node,
    run_logistics_node,
    run_procurement_node,
    run_scheduling_node,
)

# =========================================================================
# _PLAN_TYPE_KEYWORDS — pin canonical buckets
# =========================================================================


def test_plan_type_keywords_canonical_buckets():
    """Pin the 4 documented plan types — refactor must NOT silently
    add or rename a bucket."""
    assert set(_PLAN_TYPE_KEYWORDS) == {
        "estimating",
        "scheduling",
        "logistics",
        "procurement",
    }


def test_estimating_keywords_pinned():
    assert "cost" in _PLAN_TYPE_KEYWORDS["estimating"]
    assert "budget" in _PLAN_TYPE_KEYWORDS["estimating"]
    assert "bid" in _PLAN_TYPE_KEYWORDS["estimating"]


def test_scheduling_keywords_pinned():
    assert "schedule" in _PLAN_TYPE_KEYWORDS["scheduling"]
    assert "cpm" in _PLAN_TYPE_KEYWORDS["scheduling"]
    assert "critical path" in _PLAN_TYPE_KEYWORDS["scheduling"]


def test_procurement_keywords_pinned():
    assert "vendor" in _PLAN_TYPE_KEYWORDS["procurement"]
    assert "supplier" in _PLAN_TYPE_KEYWORDS["procurement"]


# =========================================================================
# _infer_plan_type
# =========================================================================


def test_infer_pure_estimating():
    assert _infer_plan_type("Generate a detailed cost estimate") == "estimating"


def test_infer_pure_scheduling():
    assert _infer_plan_type("Build the CPM schedule") == "scheduling"


def test_infer_pure_logistics():
    assert _infer_plan_type("Plan crane logistics for the site") == "logistics"


def test_infer_pure_procurement():
    assert _infer_plan_type("Set up vendor procurement") == "procurement"


def test_infer_no_keywords_returns_full():
    """[fallback] Unrelated text -> 'full' (run all agents)."""
    assert _infer_plan_type("Hello there") == "full"


def test_infer_empty_request_returns_full():
    assert _infer_plan_type("") == "full"


def test_infer_tied_scores_returns_full():
    """[edge case] 1 keyword each from estimating + scheduling -> 'full'.
    Pin: tiebreaker is ALWAYS 'full', never the alphabetically first
    bucket."""
    out = _infer_plan_type("cost schedule")
    assert out == "full"


def test_infer_higher_score_wins():
    """3 estimating keywords vs 1 scheduling -> estimating."""
    out = _infer_plan_type("cost budget bid plus a schedule note")
    assert out == "estimating"


def test_infer_case_insensitive():
    assert _infer_plan_type("BUDGET COST BID") == "estimating"


# =========================================================================
# analyze_request_node
# =========================================================================


@pytest.mark.asyncio
async def test_analyze_explicit_plan_type_passes_through():
    state = {"project_id": "p-1", "request": "x", "plan_type": "estimating"}
    out = await analyze_request_node(state)
    assert out["plan_type"] == "estimating"
    assert out["status"] == "request_analyzed"


@pytest.mark.asyncio
async def test_analyze_auto_triggers_inference():
    state = {"project_id": "p-1", "request": "build the CPM schedule", "plan_type": "auto"}
    out = await analyze_request_node(state)
    assert out["plan_type"] == "scheduling"


@pytest.mark.asyncio
async def test_analyze_empty_plan_type_triggers_inference():
    state = {"project_id": "p-1", "request": "vendor contracts", "plan_type": ""}
    out = await analyze_request_node(state)
    assert out["plan_type"] == "procurement"


# =========================================================================
# route_agents
# =========================================================================


def test_route_full_runs_all_4():
    out = route_agents({"plan_type": "full"})
    assert set(out) == {
        "run_estimating",
        "run_scheduling",
        "run_logistics",
        "run_procurement",
    }


def test_route_single_estimating():
    assert route_agents({"plan_type": "estimating"}) == ["run_estimating"]


def test_route_single_scheduling():
    assert route_agents({"plan_type": "scheduling"}) == ["run_scheduling"]


def test_route_single_logistics():
    assert route_agents({"plan_type": "logistics"}) == ["run_logistics"]


def test_route_single_procurement():
    assert route_agents({"plan_type": "procurement"}) == ["run_procurement"]


def test_route_default_full():
    """Missing plan_type -> 'full' default."""
    out = route_agents({})
    assert set(out) == {
        "run_estimating",
        "run_scheduling",
        "run_logistics",
        "run_procurement",
    }


# =========================================================================
# Per-node success + failure
# =========================================================================


@pytest.mark.asyncio
async def test_estimating_node_success():
    async def fake(*_args, **_kwargs):
        return {"final_estimate": {"recommended_total": 100_000}, "status": "completed"}

    with patch("app.services.agents.planning_team.run_estimating_agent", fake):
        out = await run_estimating_node({"project_id": "p-1"})

    assert out["status"] == "estimating_complete"
    assert out["estimating_results"]["status"] == "completed"


@pytest.mark.asyncio
async def test_estimating_node_failure_isolated():
    async def fake(*_args, **_kwargs):
        raise RuntimeError("estimator crashed")

    with patch("app.services.agents.planning_team.run_estimating_agent", fake):
        out = await run_estimating_node({"project_id": "p-1"})

    assert out["status"] == "estimating_failed"
    assert "estimator crashed" in out["error"]
    assert out["estimating_results"]["status"] == "failed"


@pytest.mark.asyncio
async def test_scheduling_node_passes_sample_activities():
    """[contract] Scheduling node provides 9 sample activities for the
    agent. Pin: refactor must not silently drop the sample list (some
    callers depend on it for deterministic dev-mode runs)."""
    captured = {}

    async def fake(*, activities, **_kwargs):
        captured["activities"] = activities
        return {"status": "completed"}

    with patch("app.services.agents.planning_team.run_scheduling_agent", fake):
        await run_scheduling_node({"project_id": "p-1"})

    assert len(captured["activities"]) == 9
    activity_names = [a["name"] for a in captured["activities"]]
    assert "Mobilization" in activity_names
    assert "Punch List" in activity_names


@pytest.mark.asyncio
async def test_scheduling_node_failure_isolated():
    async def fake(*_args, **_kwargs):
        raise ValueError("schedule failed")

    with patch("app.services.agents.planning_team.run_scheduling_agent", fake):
        out = await run_scheduling_node({"project_id": "p-1"})

    assert out["status"] == "scheduling_failed"
    assert "schedule failed" in out["error"]


@pytest.mark.asyncio
async def test_logistics_node_success():
    async def fake(*_args, **_kwargs):
        return {"status": "completed"}

    with patch("app.services.agents.planning_team.run_logistics_agent", fake):
        out = await run_logistics_node({"project_id": "p-1"})

    assert out["status"] == "logistics_complete"


@pytest.mark.asyncio
async def test_logistics_node_failure_isolated():
    async def fake(*_args, **_kwargs):
        raise ConnectionError("logistics service")

    with patch("app.services.agents.planning_team.run_logistics_agent", fake):
        out = await run_logistics_node({"project_id": "p-1"})

    assert out["status"] == "logistics_failed"


@pytest.mark.asyncio
async def test_procurement_node_success():
    async def fake(*_args, **_kwargs):
        return {"status": "completed"}

    with patch("app.services.agents.planning_team.run_procurement_agent", fake):
        out = await run_procurement_node({"project_id": "p-1"})

    assert out["status"] == "procurement_complete"


@pytest.mark.asyncio
async def test_procurement_node_failure_isolated():
    async def fake(*_args, **_kwargs):
        raise RuntimeError("procurement failed")

    with patch("app.services.agents.planning_team.run_procurement_agent", fake):
        out = await run_procurement_node({"project_id": "p-1"})

    assert out["status"] == "procurement_failed"


# =========================================================================
# compile_plan_node — overall_status aggregation
# =========================================================================


@pytest.mark.asyncio
async def test_compile_plan_all_completed_returns_complete():
    state = {
        "project_id": "p-1",
        "plan_type": "full",
        "estimating_results": {
            "status": "completed",
            "final_estimate": {
                "recommended_total": 1_000_000,
                "confidence": "high",
                "summary": "ok",
            },
        },
        "scheduling_results": {
            "status": "completed",
            "optimized_schedule": {"optimized_duration": 365, "summary": "ok"},
            "dcma_results": {"overall_health": "good"},
        },
        "logistics_results": {
            "status": "completed",
            "simulation_results": {
                "delay_analysis": {"total_productive_time_pct": 75.5},
                "summary": "ok",
            },
        },
        "procurement_results": {
            "status": "completed",
            "recommendations": [{"priority": "critical"}, {"priority": "low"}],
        },
        "human_feedback": "approved",
    }
    out = await compile_plan_node(state)
    assert out["status"] == "plan_compiled"
    assert out["final_plan"]["overall_status"] == "complete"


@pytest.mark.asyncio
async def test_compile_plan_one_failed_returns_partial_failure():
    state = {
        "project_id": "p-1",
        "plan_type": "full",
        "estimating_results": {"status": "failed", "error": "x"},
        "scheduling_results": {"status": "completed", "optimized_schedule": {}},
        "logistics_results": None,
        "procurement_results": None,
        "human_feedback": None,
    }
    out = await compile_plan_node(state)
    assert out["final_plan"]["overall_status"] == "partial_failure"


@pytest.mark.asyncio
async def test_compile_plan_unknown_status_returns_completed_with_gaps():
    """[default] Status that's neither all-completed nor any-failed
    -> 'completed_with_gaps' (e.g., 'unknown' status from agent)."""
    state = {
        "project_id": "p-1",
        "plan_type": "full",
        "estimating_results": {"status": "in_progress", "final_estimate": {}},
        "scheduling_results": None,
        "logistics_results": None,
        "procurement_results": None,
        "human_feedback": None,
    }
    out = await compile_plan_node(state)
    assert out["final_plan"]["overall_status"] == "completed_with_gaps"


@pytest.mark.asyncio
async def test_compile_plan_pins_5_next_steps():
    """[contract] The 5 documented next-step actions appear in every
    plan. UI rendering depends on this fixed list — refactor must
    NOT silently shorten or reorder."""
    state = {
        "project_id": "p-1",
        "plan_type": "full",
        "estimating_results": {"status": "completed", "final_estimate": {}},
        "scheduling_results": None,
        "logistics_results": None,
        "procurement_results": None,
        "human_feedback": None,
    }
    out = await compile_plan_node(state)
    next_steps = out["final_plan"]["next_steps"]
    assert len(next_steps) == 5
    # First step is canonical:
    assert "approve cost estimate" in next_steps[0]
    # Long-lead procurement step is canonical:
    assert any("long-lead" in s for s in next_steps)


@pytest.mark.asyncio
async def test_compile_plan_critical_items_filtered():
    """Critical items list filters by priority='critical'."""
    state = {
        "project_id": "p-1",
        "plan_type": "procurement",
        "estimating_results": None,
        "scheduling_results": None,
        "logistics_results": None,
        "procurement_results": {
            "status": "completed",
            "recommendations": [
                {"priority": "critical", "name": "rebar"},
                {"priority": "low", "name": "paint"},
                {"priority": "critical", "name": "steel"},
            ],
        },
        "human_feedback": None,
    }
    out = await compile_plan_node(state)
    crit = out["final_plan"]["sections"]["procurement"]["critical_items"]
    assert len(crit) == 2


# =========================================================================
# _build_executive_summary — per-section formatting
# =========================================================================


def test_summary_includes_cost_with_dollar_format():
    sections = {
        "estimating": {
            "recommended_total": 1_500_000,
            "confidence": "high",
        }
    }
    out = _build_executive_summary(sections, None)
    assert "$1,500,000.00" in out
    assert "high confidence" in out


def test_summary_includes_schedule_dur_and_dcma():
    sections = {
        "scheduling": {
            "project_duration": 365,
            "dcma_health": "good",
        }
    }
    out = _build_executive_summary(sections, None)
    assert "365 days" in out
    assert "DCMA health: good" in out


def test_summary_includes_logistics_pct():
    sections = {"logistics": {"productive_time_pct": 78.4}}
    out = _build_executive_summary(sections, None)
    assert "78.4%" in out


def test_summary_includes_procurement_counts():
    sections = {
        "procurement": {
            "recommendation_count": 12,
            "critical_items": [1, 2, 3],
        }
    }
    out = _build_executive_summary(sections, None)
    assert "12 recommendations" in out
    assert "3 critical" in out


def test_summary_appends_human_feedback_when_provided():
    out = _build_executive_summary({}, "approved with notes")
    assert "Human Review: approved with notes" in out


def test_summary_omits_human_feedback_when_none():
    out = _build_executive_summary({}, None)
    assert "Human Review" not in out


def test_summary_starts_with_canonical_header():
    """Pin: header line is 'Pre-Construction Plan Summary:'."""
    out = _build_executive_summary({}, None)
    assert out.startswith("Pre-Construction Plan Summary:")


# =========================================================================
# build_planning_team — graph topology
# =========================================================================


def test_build_planning_team_returns_compiled_graph():
    graph = build_planning_team()
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    assert {
        "analyze_request",
        "run_estimating",
        "run_scheduling",
        "run_logistics",
        "run_procurement",
        "human_review",
        "compile_plan",
    } <= nodes
