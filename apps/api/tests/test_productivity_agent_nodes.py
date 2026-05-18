"""Tests for the productivity agent LangGraph nodes.

Pin per-node short-circuit behavior + the equipment telemetry
aggregation math (utilization% = (engine - idle) / engine * 100,
with /0 guard) + per-node error isolation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.agents.productivity_agent import (
    analyze_equipment_node,
    build_productivity_agent,
    forecast_node,
    recognize_activity_node,
)

# =========================================================================
# recognize_activity_node
# =========================================================================


@pytest.mark.asyncio
async def test_recognize_no_frames_short_circuits():
    """[edge case] No frames -> activity_type='unknown',
    confidence=0.0, status='no_frames'. NOT a failure."""
    out = await recognize_activity_node({"project_id": "p-1", "frames": []})
    assert out["activity_results"]["activity_type"] == "unknown"
    assert out["activity_results"]["confidence"] == 0.0
    assert out["status"] == "no_frames"


@pytest.mark.asyncio
async def test_recognize_with_frames_calls_recognizer():
    fake = AsyncMock(return_value={"activity_type": "rebar_tying", "confidence": 0.87})
    with patch("app.services.agents.productivity_agent._recognizer.recognize", fake):
        out = await recognize_activity_node({"project_id": "p-1", "frames": [b"f1", b"f2", b"f3"]})
    fake.assert_called_once()
    assert out["status"] == "activity_recognized"
    assert out["activity_results"]["activity_type"] == "rebar_tying"


@pytest.mark.asyncio
async def test_recognize_failure_isolated():
    fake = AsyncMock(side_effect=RuntimeError("model crash"))
    with patch("app.services.agents.productivity_agent._recognizer.recognize", fake):
        out = await recognize_activity_node({"project_id": "p-1", "frames": [b"f1"]})
    assert out["activity_results"] is None
    assert out["status"] == "recognition_failed"
    assert "model crash" in out["error"]


# =========================================================================
# forecast_node
# =========================================================================


@pytest.mark.asyncio
async def test_forecast_passes_14_day_horizon():
    """[contract] Forecast horizon is 14 days. Pin so a refactor
    doesn't silently shorten or lengthen the horizon — downstream
    UI rendering depends on the documented value."""
    captured = {}

    async def fake_forecast(*, historical_data, trade, forecast_days):
        captured.update({"historical": historical_data, "trade": trade, "days": forecast_days})
        return {"forecast": [1, 2, 3]}

    state = {
        "historical_data": [{"date": "2026-04-20", "productivity": 0.85}],
        "trade": "concrete",
    }
    with patch(
        "app.services.agents.productivity_agent.forecast_productivity",
        fake_forecast,
    ):
        out = await forecast_node(state)

    assert captured["days"] == 14
    assert captured["trade"] == "concrete"
    assert captured["historical"] == [{"date": "2026-04-20", "productivity": 0.85}]
    assert out["status"] == "forecast_complete"


@pytest.mark.asyncio
async def test_forecast_default_trade_general():
    """Missing trade -> defaults to 'general'."""
    captured = {}

    async def fake_forecast(*, trade, **_kwargs):
        captured["trade"] = trade
        return {}

    with patch(
        "app.services.agents.productivity_agent.forecast_productivity",
        fake_forecast,
    ):
        await forecast_node({"historical_data": []})

    assert captured["trade"] == "general"


@pytest.mark.asyncio
async def test_forecast_failure_isolated():
    async def boom(**_kwargs):
        raise RuntimeError("forecast model down")

    with patch(
        "app.services.agents.productivity_agent.forecast_productivity",
        boom,
    ):
        out = await forecast_node({"historical_data": [], "trade": "x"})

    assert out["forecast_results"] is None
    assert out["status"] == "forecast_failed"
    assert "forecast model down" in out["error"]


# =========================================================================
# analyze_equipment_node — aggregation math
# =========================================================================


@pytest.mark.asyncio
async def test_analyze_equipment_no_telemetry_short_circuits():
    """[edge case] No telemetry -> 'no_telemetry' status. NOT a
    failure."""
    out = await analyze_equipment_node({"telemetry_data": []})
    assert out["status"] == "no_telemetry"
    assert out["equipment_analysis"]["summary"] == "No telemetry data"


@pytest.mark.asyncio
async def test_analyze_equipment_aggregates_correctly():
    """Pin the aggregation formula:
    - total_engine_hours = sum of engine_hours
    - total_idle_hours = sum of idle_time_hours
    - utilization_pct = (engine - idle) / engine * 100
    - equipment_count = unique equipment_id count
    """
    telemetry = [
        {"equipment_id": "EX-1", "engine_hours": 8, "idle_time_hours": 2, "fuel_consumption": 50},
        {"equipment_id": "EX-2", "engine_hours": 10, "idle_time_hours": 3, "fuel_consumption": 60},
        {"equipment_id": "EX-1", "engine_hours": 6, "idle_time_hours": 1, "fuel_consumption": 40},
    ]
    out = await analyze_equipment_node({"telemetry_data": telemetry})

    a = out["equipment_analysis"]
    # 8 + 10 + 6 = 24
    assert a["total_engine_hours"] == 24.0
    # 2 + 3 + 1 = 6
    assert a["total_idle_hours"] == 6.0
    # 50 + 60 + 40 = 150
    assert a["total_fuel_consumption"] == 150.0
    # (24 - 6) / 24 * 100 = 75.0
    assert a["utilization_pct"] == 75.0
    # Unique equipment_ids: EX-1, EX-2
    assert a["equipment_count"] == 2
    # Summary format pinned:
    assert "75.0%" in a["summary"]
    assert "24 engine hours" in a["summary"]


@pytest.mark.asyncio
async def test_analyze_equipment_zero_engine_hours_no_div_by_zero():
    """[edge case] All engine_hours=0 -> utilization_pct=0 (NOT
    crash, NOT NaN)."""
    telemetry = [
        {"equipment_id": "EX-1", "engine_hours": 0, "idle_time_hours": 0, "fuel_consumption": 0},
    ]
    out = await analyze_equipment_node({"telemetry_data": telemetry})
    assert out["equipment_analysis"]["utilization_pct"] == 0
    assert out["status"] == "equipment_analyzed"


@pytest.mark.asyncio
async def test_analyze_equipment_handles_none_fields():
    """Telemetry with None numeric fields -> coerced to 0 (don't
    crash on TypeError)."""
    telemetry = [
        {
            "equipment_id": "EX-1",
            "engine_hours": None,
            "idle_time_hours": None,
            "fuel_consumption": None,
        },
        {
            "equipment_id": "EX-2",
            "engine_hours": 10,
            "idle_time_hours": 2,
            "fuel_consumption": 30,
        },
    ]
    out = await analyze_equipment_node({"telemetry_data": telemetry})
    assert out["equipment_analysis"]["total_engine_hours"] == 10.0
    assert out["equipment_analysis"]["total_idle_hours"] == 2.0
    assert out["equipment_analysis"]["total_fuel_consumption"] == 30.0


@pytest.mark.asyncio
async def test_analyze_equipment_full_idle_zero_utilization():
    """[boundary] Engine hours = idle hours -> 0% utilization."""
    telemetry = [
        {"equipment_id": "EX-1", "engine_hours": 8, "idle_time_hours": 8, "fuel_consumption": 5},
    ]
    out = await analyze_equipment_node({"telemetry_data": telemetry})
    assert out["equipment_analysis"]["utilization_pct"] == 0.0


# =========================================================================
# Graph build
# =========================================================================


def test_build_productivity_agent_returns_compiled_graph():
    graph = build_productivity_agent()
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    assert {"recognize_activity", "forecast", "analyze_equipment"} <= nodes


def test_build_productivity_agent_sequential_flow():
    """[contract] recognize_activity -> forecast -> analyze_equipment
    sequential — order is load-bearing because each node's status
    is consumed by downstream consumers."""
    graph = build_productivity_agent()
    g = graph.get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    assert ("recognize_activity", "forecast") in edges
    assert ("forecast", "analyze_equipment") in edges
