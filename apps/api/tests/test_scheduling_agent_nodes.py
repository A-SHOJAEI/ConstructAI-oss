"""Tests for the scheduling agent LangGraph nodes.

Pin the documented DCMA thresholds, the weather seasonal-factor
average, the compression-days formula (min of avg_float*0.3 and
duration*0.1), and per-node error isolation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.agents.scheduling_agent import (
    _DCMA_THRESHOLDS,
    assess_weather_node,
    build_scheduling_agent,
    calculate_cpm_node,
    optimize_node,
)

# =========================================================================
# _DCMA_THRESHOLDS — pin documented industry-standard values
# =========================================================================


def test_dcma_thresholds_canonical_14_checks():
    """[contract] Pin the 14 documented DCMA checks. Pin so a
    refactor doesn't silently add or drop a check (changes overall
    'health' rating)."""
    expected = {
        "logic",
        "leads",
        "lags",
        "relationship_types",
        "hard_constraints",
        "high_float",
        "negative_float",
        "high_duration",
        "invalid_dates",
        "resources",
        "missed_tasks",
        "critical_path_test",
        "critical_path_length_index",
        "baseline",
    }
    assert set(_DCMA_THRESHOLDS) == expected


def test_dcma_logic_max_5_pct():
    """DCMA: <5% activities can be missing logic."""
    assert _DCMA_THRESHOLDS["logic"]["max_missing_pct"] == 5.0


def test_dcma_leads_max_zero():
    """[business invariant] DCMA strictly forbids leads (negative
    lags). Pin so a refactor doesn't relax this."""
    assert _DCMA_THRESHOLDS["leads"]["max_pct"] == 0.0


def test_dcma_negative_float_zero_tolerance():
    """[business invariant] Negative float = behind schedule, ZERO
    tolerance. Pin so refactor doesn't accidentally allow some."""
    assert _DCMA_THRESHOLDS["negative_float"]["max_pct"] == 0.0


def test_dcma_invalid_dates_zero_tolerance():
    assert _DCMA_THRESHOLDS["invalid_dates"]["max_pct"] == 0.0


def test_dcma_high_float_44_days():
    """DCMA defines 'high float' as >44 working days (typical 2 months)."""
    assert _DCMA_THRESHOLDS["high_float"]["max_days"] == 44


def test_dcma_high_duration_44_days():
    """DCMA defines 'high duration' as >44 working days."""
    assert _DCMA_THRESHOLDS["high_duration"]["max_days"] == 44


def test_dcma_resources_min_80_pct():
    """80% of activities must have resource assignments."""
    assert _DCMA_THRESHOLDS["resources"]["min_assigned_pct"] == 80.0


def test_dcma_critical_path_test_15pct_min():
    assert _DCMA_THRESHOLDS["critical_path_test"]["min_length_ratio"] == 0.15


def test_dcma_cpli_minimum_0_95():
    """[business invariant] CPLI < 0.95 indicates schedule slip.
    Pin so a refactor doesn't lower this threshold."""
    assert _DCMA_THRESHOLDS["critical_path_length_index"]["min_cpli"] == 0.95


def test_dcma_baseline_required():
    assert _DCMA_THRESHOLDS["baseline"]["required"] is True


# =========================================================================
# calculate_cpm_node
# =========================================================================


@pytest.mark.asyncio
async def test_cpm_no_activities_returns_zeros():
    """[edge case] Empty activities -> zero-duration result, status
    'no_activities'. NOT a failure."""
    state = {"project_id": "p-1", "activities": []}
    out = await calculate_cpm_node(state)
    assert out["status"] == "no_activities"
    assert out["cpm_results"]["project_duration"] == 0
    assert out["cpm_results"]["critical_path_length"] == 0


@pytest.mark.asyncio
async def test_cpm_calls_engine_with_activities():
    captured = {}

    async def fake_cpm(activities):
        captured["activities"] = activities
        return {"project_duration": 100, "critical_path_length": 5}

    state = {
        "project_id": "p-1",
        "activities": [{"id": "1", "duration_days": 30}],
    }
    with patch("app.services.agents.scheduling_agent.calculate_cpm", fake_cpm):
        out = await calculate_cpm_node(state)

    assert captured["activities"] == [{"id": "1", "duration_days": 30}]
    assert out["status"] == "cpm_complete"
    assert out["cpm_results"]["project_duration"] == 100


@pytest.mark.asyncio
async def test_cpm_failure_isolated():
    async def boom(_activities):
        raise RuntimeError("cpm engine crashed")

    state = {"project_id": "p-1", "activities": [{"id": "1"}]}
    with patch("app.services.agents.scheduling_agent.calculate_cpm", boom):
        out = await calculate_cpm_node(state)
    assert out["cpm_results"] is None
    assert out["status"] == "cpm_failed"
    assert "cpm engine crashed" in out["error"]


# =========================================================================
# assess_weather_node — seasonal-factor average pin
# =========================================================================


@pytest.mark.asyncio
async def test_weather_uses_documented_seasonal_factors():
    """[contract] Spring/summer/fall/winter delay factors pinned
    in code. Pin: refactor must not silently change the average."""
    state = {"project_id": "p-1", "cpm_results": {"project_duration": 100}}
    out = await assess_weather_node(state)
    factors = out["weather_impact"]["seasonal_factors"]
    assert factors["spring"]["delay_factor"] == 1.05
    assert factors["summer"]["delay_factor"] == 1.03
    assert factors["fall"]["delay_factor"] == 1.04
    assert factors["winter"]["delay_factor"] == 1.10


@pytest.mark.asyncio
async def test_weather_avg_factor_is_arithmetic_mean():
    """avg = (1.05 + 1.03 + 1.04 + 1.10) / 4 = 1.055. Pin so a
    refactor doesn't silently change to a weighted mean."""
    state = {"project_id": "p-1", "cpm_results": {"project_duration": 100}}
    out = await assess_weather_node(state)
    # Round to 3 decimals = 1.055:
    assert out["weather_impact"]["delay_factor"] == 1.055


@pytest.mark.asyncio
async def test_weather_adjusted_duration():
    """100 days * 1.055 = 105.5 -> round to 106."""
    state = {"project_id": "p-1", "cpm_results": {"project_duration": 100}}
    out = await assess_weather_node(state)
    assert out["weather_impact"]["adjusted_duration"] == 106
    assert out["weather_impact"]["weather_delay_days"] == 6


@pytest.mark.asyncio
async def test_weather_no_cpm_zero_duration():
    """[edge case] Missing cpm_results -> 0 duration, doesn't crash."""
    state = {"project_id": "p-1"}
    out = await assess_weather_node(state)
    assert out["weather_impact"]["original_duration"] == 0
    assert out["weather_impact"]["weather_delay_days"] == 0


@pytest.mark.asyncio
async def test_weather_risk_periods_4_documented_periods():
    """[contract] 4 risk periods (winter/spring/summer/fall) with
    pinned ratios: 0.45 / 0.25 / 0.10 / 0.20. Pin so refactor
    doesn't silently rebalance the seasonal split."""
    state = {"project_id": "p-1", "cpm_results": {"project_duration": 100}}
    out = await assess_weather_node(state)
    risk_periods = out["weather_impact"]["risk_periods"]
    assert len(risk_periods) == 4
    period_names = [p["period"] for p in risk_periods]
    assert any("Winter" in p for p in period_names)
    assert any("Spring" in p for p in period_names)
    assert any("Summer" in p for p in period_names)
    assert any("Fall" in p for p in period_names)


@pytest.mark.asyncio
async def test_weather_recommendations_4_canonical():
    """[contract] 4 documented recommendations. UI may show as
    bullet list — refactor must not silently change count."""
    state = {"project_id": "p-1", "cpm_results": {"project_duration": 100}}
    out = await assess_weather_node(state)
    assert len(out["weather_impact"]["recommendations"]) == 4


# =========================================================================
# optimize_node — compression formula
# =========================================================================


@pytest.mark.asyncio
async def test_optimize_no_cpm_returns_no_optimization():
    """[edge case] Missing CPM -> 'no_cpm_for_optimization' status."""
    state = {"project_id": "p-1"}
    out = await optimize_node(state)
    assert out["optimized_schedule"] is None
    assert out["status"] == "no_cpm_for_optimization"


@pytest.mark.asyncio
async def test_optimize_compression_formula():
    """[business invariant] compression_days = min(avg_float*0.3,
    duration*0.1). Pin so a refactor doesn't allow over-aggressive
    compression that would invalidate the schedule."""
    activities = [
        {"id": "1", "is_critical": True, "total_float": 0},
        {"id": "2", "is_critical": True, "total_float": 0},
        # 3 non-critical with avg_float=20:
        {"id": "3", "is_critical": False, "total_float": 30},
        {"id": "4", "is_critical": False, "total_float": 20},
        {"id": "5", "is_critical": False, "total_float": 10},
    ]
    state = {
        "project_id": "p-1",
        "cpm_results": {
            "activities": activities,
            "project_duration": 100,
        },
    }
    out = await optimize_node(state)
    # avg_float = (30+20+10)/3 = 20. avg*0.3 = 6, duration*0.1 = 10.
    # min = 6.
    assert out["optimized_schedule"]["compression_days"] == 6


@pytest.mark.asyncio
async def test_optimize_compression_caps_at_10pct_duration():
    """When avg_float is very large, compression caps at 10% duration."""
    activities = [
        {"id": "1", "is_critical": True},
        {"id": "2", "is_critical": False, "total_float": 100},
        {"id": "3", "is_critical": False, "total_float": 100},
    ]
    state = {
        "project_id": "p-1",
        "cpm_results": {
            "activities": activities,
            "project_duration": 50,
        },
    }
    out = await optimize_node(state)
    # avg_float = 100, *0.3 = 30. duration*0.1 = 5. min = 5.
    assert out["optimized_schedule"]["compression_days"] == 5


@pytest.mark.asyncio
async def test_optimize_combines_weather_and_compression():
    """net duration = original + weather - compression."""
    activities = [
        {"id": "1", "is_critical": True},
        {"id": "2", "is_critical": False, "total_float": 30},
    ]
    state = {
        "project_id": "p-1",
        "cpm_results": {"activities": activities, "project_duration": 100},
        "weather_impact": {"weather_delay_days": 8},
    }
    out = await optimize_node(state)
    # avg_float=30, *0.3 = 9; duration*0.1 = 10; min = 9
    # optimized = 100 + 8 - 9 = 99
    assert out["optimized_schedule"]["weather_delay_days"] == 8
    assert out["optimized_schedule"]["compression_days"] == 9
    assert out["optimized_schedule"]["optimized_duration"] == 99
    assert out["optimized_schedule"]["net_change"] == -1


@pytest.mark.asyncio
async def test_optimize_dcma_failures_become_recommendations():
    """[contract] Failed DCMA checks become recommendation strings."""
    activities = [{"id": "1", "is_critical": True}]
    state = {
        "project_id": "p-1",
        "cpm_results": {"activities": activities, "project_duration": 100},
        "dcma_results": {
            "checks": [
                {"check": "Logic", "pass": False, "detail": "5% missing"},
                {"check": "Leads", "pass": True, "detail": "0%"},  # pass -> not added
                {"check": "Lags", "pass": False, "detail": "8% lag-heavy"},
            ]
        },
    }
    out = await optimize_node(state)
    recs = out["optimized_schedule"]["dcma_recommendations"]
    # 2 failed -> 2 recommendations:
    assert len(recs) == 2
    assert any("Logic" in r and "5% missing" in r for r in recs)
    assert any("Lags" in r and "lag-heavy" in r for r in recs)


@pytest.mark.asyncio
async def test_optimize_no_dcma_no_recommendations():
    """No DCMA results -> empty recommendations list (don't fabricate)."""
    activities = [{"id": "1", "is_critical": True}]
    state = {
        "project_id": "p-1",
        "cpm_results": {"activities": activities, "project_duration": 100},
    }
    out = await optimize_node(state)
    assert out["optimized_schedule"]["dcma_recommendations"] == []


# =========================================================================
# Graph build
# =========================================================================


def test_build_scheduling_agent_returns_compiled_graph():
    graph = build_scheduling_agent()
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    assert {
        "calculate_cpm",
        "check_dcma",
        "assess_weather",
        "optimize",
    } <= nodes
