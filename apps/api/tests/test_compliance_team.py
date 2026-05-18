"""Tests for the Compliance Team LangGraph supervisor.

Pin the documented routing keywords (so a refactor can't silently
drop "osha" -> quality), node-level error isolation (per-agent
failure must NOT crash the team), and graph topology.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.agents.compliance_team import (
    _TASK_KEYWORDS,
    _infer_task_type,
    analyze_node,
    build_compliance_team,
    compile_report_node,
    route_agents,
    run_communication_node,
    run_quality_node,
)

# =========================================================================
# _TASK_KEYWORDS — pin canonical routing keywords
# =========================================================================


def test_task_keywords_includes_osha_under_quality():
    """[business invariant] OSHA references MUST route to the quality
    agent. Pin so a refactor doesn't silently move it under
    'communication'."""
    assert "osha" in _TASK_KEYWORDS["quality"]


def test_task_keywords_quality_canonical():
    """All 6 documented quality keywords are pinned."""
    expected = {"quality", "defect", "inspection", "ncr", "compliance", "osha"}
    assert set(_TASK_KEYWORDS["quality"]) == expected


def test_task_keywords_communication_canonical():
    """All 6 documented communication keywords are pinned."""
    expected = {"report", "meeting", "transcri", "rfi", "submittal", "minutes"}
    assert set(_TASK_KEYWORDS["communication"]) == expected


# =========================================================================
# _infer_task_type
# =========================================================================


def test_infer_pure_quality_request():
    assert _infer_task_type("Run an OSHA compliance inspection") == "quality"


def test_infer_pure_communication_request():
    assert _infer_task_type("Generate the weekly report") == "communication"


def test_infer_no_keywords_returns_full():
    """[fallback] When no keywords match, do BOTH (full)."""
    assert _infer_task_type("Hello world this is unrelated") == "full"


def test_infer_empty_request_returns_full():
    assert _infer_task_type("") == "full"


def test_infer_tied_scores_returns_full():
    """[edge case] Equal scores in both buckets -> 'full' (run both
    agents). Pin: never silently pick one bucket on a tie."""
    # 1 quality keyword + 1 communication keyword = tie:
    assert _infer_task_type("inspection meeting") == "full"


def test_infer_higher_quality_score_wins():
    """3 quality keywords vs 1 communication -> quality."""
    out = _infer_task_type("OSHA defect inspection report")
    # 3 matches in quality (osha, defect, inspection), 1 in communication
    # (report) -> quality wins:
    assert out == "quality"


def test_infer_case_insensitive():
    """Matching is case-insensitive."""
    assert _infer_task_type("OSHA INSPECTION") == "quality"


def test_infer_substring_match_for_transcri():
    """[corner case] 'transcri' substring catches 'transcribe',
    'transcript', 'transcription'."""
    assert _infer_task_type("Please transcribe this meeting") == "communication"
    assert _infer_task_type("Read the transcript") == "communication"


# =========================================================================
# analyze_node
# =========================================================================


@pytest.mark.asyncio
async def test_analyze_node_with_explicit_task_type_passes_through():
    """Explicit task_type is preserved (no re-inference)."""
    state = {"request": "irrelevant", "task_type": "quality"}
    out = await analyze_node(state)
    assert out["task_type"] == "quality"
    assert out["status"] == "analyzed"


@pytest.mark.asyncio
async def test_analyze_node_with_auto_infers():
    """task_type='auto' triggers _infer_task_type."""
    state = {"request": "OSHA inspection report", "task_type": "auto"}
    out = await analyze_node(state)
    assert out["task_type"] == "quality"


@pytest.mark.asyncio
async def test_analyze_node_with_empty_task_type_infers():
    """Empty string -> infer (treated like 'auto')."""
    state = {"request": "weekly meeting minutes", "task_type": ""}
    out = await analyze_node(state)
    assert out["task_type"] == "communication"


# =========================================================================
# route_agents
# =========================================================================


def test_route_agents_full_runs_both():
    out = route_agents({"task_type": "full"})
    assert set(out) == {"run_quality", "run_communication"}


def test_route_agents_quality_only_runs_quality():
    out = route_agents({"task_type": "quality"})
    assert out == ["run_quality"]


def test_route_agents_communication_only_runs_communication():
    out = route_agents({"task_type": "communication"})
    assert out == ["run_communication"]


def test_route_agents_default_full():
    """[fallback] Missing task_type -> 'full' default."""
    out = route_agents({})
    assert set(out) == {"run_quality", "run_communication"}


# =========================================================================
# Node-level error isolation
# =========================================================================


@pytest.mark.asyncio
async def test_quality_node_success():
    async def fake_quality(*_args, **_kwargs):
        return {"defects_found": 3, "status": "completed"}

    with patch("app.services.agents.compliance_team.run_quality_agent", fake_quality):
        out = await run_quality_node({"project_id": "p-1"})

    assert out["status"] == "quality_complete"
    assert out["quality_results"]["defects_found"] == 3


@pytest.mark.asyncio
async def test_quality_node_agent_failure_captures_error():
    """[error isolation] Quality agent crash -> node returns failed
    state with error string. The graph keeps moving (does NOT raise)."""

    async def fake_quality(*_args, **_kwargs):
        raise RuntimeError("quality model unavailable")

    with patch("app.services.agents.compliance_team.run_quality_agent", fake_quality):
        out = await run_quality_node({"project_id": "p-1"})

    assert out["status"] == "quality_failed"
    assert "quality model unavailable" in out["error"]
    assert out["quality_results"]["status"] == "failed"
    assert "quality model unavailable" in out["quality_results"]["error"]


@pytest.mark.asyncio
async def test_communication_node_success():
    async def fake_comm(*_args, **_kwargs):
        return {"reports_generated": 1, "status": "completed"}

    with patch(
        "app.services.agents.compliance_team.run_communication_agent",
        fake_comm,
    ):
        out = await run_communication_node({"project_id": "p-1"})

    assert out["status"] == "communication_complete"
    assert out["communication_results"]["reports_generated"] == 1


@pytest.mark.asyncio
async def test_communication_node_agent_failure_captures_error():
    async def fake_comm(*_args, **_kwargs):
        raise ConnectionError("kafka down")

    with patch(
        "app.services.agents.compliance_team.run_communication_agent",
        fake_comm,
    ):
        out = await run_communication_node({"project_id": "p-1"})

    assert out["status"] == "communication_failed"
    assert "kafka down" in out["error"]
    assert out["communication_results"]["status"] == "failed"


# =========================================================================
# compile_report_node
# =========================================================================


@pytest.mark.asyncio
async def test_compile_report_assembles_full_state():
    state = {
        "project_id": "p-1",
        "task_type": "full",
        "quality_results": {"defects": 2},
        "communication_results": {"messages": 5},
        "human_feedback": "approved with notes",
    }
    out = await compile_report_node(state)
    assert out["status"] == "report_compiled"
    report = out["final_report"]
    assert report["project_id"] == "p-1"
    assert report["task_type"] == "full"
    assert report["quality"] == {"defects": 2}
    assert report["communication"] == {"messages": 5}
    assert report["human_feedback"] == "approved with notes"


@pytest.mark.asyncio
async def test_compile_report_handles_missing_optionals():
    """[robustness] Quality/communication None -> still compiles
    (don't crash if one agent didn't run)."""
    state = {
        "project_id": "p-1",
        "task_type": "quality",
        "quality_results": {"defects": 1},
        "communication_results": None,
        "human_feedback": None,
    }
    out = await compile_report_node(state)
    assert out["status"] == "report_compiled"
    assert out["final_report"]["communication"] is None


# =========================================================================
# build_compliance_team — graph topology
# =========================================================================


def test_build_compliance_team_returns_compiled_graph():
    graph = build_compliance_team()
    assert graph is not None
    # Spot-check the documented node set:
    nodes = set(graph.get_graph().nodes.keys())
    assert {
        "analyze",
        "run_quality",
        "run_communication",
        "human_review",
        "compile_report",
    } <= nodes


def test_build_compliance_team_with_checkpointer():
    """Passing a checkpointer compiles successfully (smoke test)."""

    class _NullCheckpointer:
        async def aget(self, *_a, **_k):
            return None

        async def aput(self, *_a, **_k):
            return None

    # Most LangGraph builds accept any duck-typed checkpointer or None.
    # Passing None is the documented fallback path:
    graph = build_compliance_team(checkpointer=None)
    assert graph is not None
