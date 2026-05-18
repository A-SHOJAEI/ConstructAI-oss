"""Tests for the project controls LangGraph agent nodes.

Pin per-node behavior: Decimal coercion of EVM inputs, the EAC
3-method fan-out (cpi / spi_cpi / remaining_work), the
no-activities skip path, the S-Curve snapshot composition, and
per-node error isolation. The integration agent is exercised via
the workflow tests; here we exercise the node functions directly.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from app.services.agents.controls_agent import (
    build_controls_agent,
    compute_evm_node,
    forecast_eac_node,
    risk_simulation_node,
    scurve_node,
)

# =========================================================================
# compute_evm_node
# =========================================================================


@pytest.mark.asyncio
async def test_compute_evm_passes_decimals_to_engine():
    """[contract] BAC/PV/EV/AC are stored as strings in state but
    MUST be coerced to Decimal before hitting the EVM engine
    (avoid float drift in earned-value math)."""
    captured = {}

    async def fake_compute(*, bac, pv, ev, ac):
        captured.update({"bac": bac, "pv": pv, "ev": ev, "ac": ac})
        return {"cpi": 0.94, "spi": 0.98}

    state = {
        "project_id": "p-1",
        "bac": "1000000",
        "pv": "500000",
        "ev": "450000",
        "ac": "480000",
    }
    with patch("app.services.agents.controls_agent.compute_evm_snapshot", fake_compute):
        out = await compute_evm_node(state)

    assert isinstance(captured["bac"], Decimal)
    assert isinstance(captured["pv"], Decimal)
    assert isinstance(captured["ev"], Decimal)
    assert isinstance(captured["ac"], Decimal)
    assert captured["bac"] == Decimal("1000000")
    assert out["status"] == "evm_computed"
    assert out["evm_results"]["cpi"] == 0.94


@pytest.mark.asyncio
async def test_compute_evm_failure_isolated():
    async def boom(**_kwargs):
        raise ValueError("invalid PV")

    state = {"bac": "1", "pv": "1", "ev": "1", "ac": "1"}
    with patch("app.services.agents.controls_agent.compute_evm_snapshot", boom):
        out = await compute_evm_node(state)

    assert out["status"] == "evm_failed"
    assert out["evm_results"] is None
    assert "invalid PV" in out["error"]


# =========================================================================
# forecast_eac_node
# =========================================================================


@pytest.mark.asyncio
async def test_forecast_eac_runs_3_canonical_methods():
    """[business invariant] EAC forecast runs 3 documented methods
    (cpi, spi_cpi, remaining_work). Pin so a refactor doesn't drop
    one — each represents a different forecasting assumption."""
    methods_called = []

    async def fake_forecast(*, method, **_kwargs):
        methods_called.append(method)
        return {"eac": 1_100_000, "method": method}

    state = {
        "bac": "1000000",
        "ev": "450000",
        "ac": "480000",
        "evm_results": {"cpi": 0.94, "spi": 0.98},
    }
    with patch("app.services.agents.controls_agent.forecast_eac", fake_forecast):
        out = await forecast_eac_node(state)

    assert set(methods_called) == {"cpi", "spi_cpi", "remaining_work"}
    assert set(out["eac_results"]) == {"cpi", "spi_cpi", "remaining_work"}
    assert out["status"] == "eac_computed"


@pytest.mark.asyncio
async def test_forecast_eac_skipped_when_no_evm():
    """[edge case] No prior EVM result -> skip EAC (don't compute on
    bogus defaults). Pin: status='eac_skipped', not 'eac_failed'."""
    state = {
        "bac": "1000000",
        "ev": "0",
        "ac": "0",
        "evm_results": None,
    }
    out = await forecast_eac_node(state)
    assert out["status"] == "eac_skipped"
    assert out["eac_results"] is None


@pytest.mark.asyncio
async def test_forecast_eac_uses_evm_spi_cpi():
    """SPI/CPI from prior EVM result MUST be coerced to Decimal
    (forecaster contract)."""
    captured = []

    async def fake_forecast(*, spi, cpi, **_kwargs):
        captured.append({"spi": spi, "cpi": cpi})
        return {}

    state = {
        "bac": "1",
        "ev": "1",
        "ac": "1",
        "evm_results": {"cpi": "0.85", "spi": "0.95"},
    }
    with patch("app.services.agents.controls_agent.forecast_eac", fake_forecast):
        await forecast_eac_node(state)

    assert all(isinstance(c["spi"], Decimal) for c in captured)
    assert all(isinstance(c["cpi"], Decimal) for c in captured)
    assert captured[0]["spi"] == Decimal("0.95")
    assert captured[0]["cpi"] == Decimal("0.85")


@pytest.mark.asyncio
async def test_forecast_eac_failure_isolated():
    async def boom(**_kwargs):
        raise RuntimeError("forecaster crashed")

    state = {
        "bac": "1",
        "ev": "1",
        "ac": "1",
        "evm_results": {"cpi": 1, "spi": 1},
    }
    with patch("app.services.agents.controls_agent.forecast_eac", boom):
        out = await forecast_eac_node(state)

    assert out["status"] == "eac_failed"
    assert "forecaster crashed" in out["error"]


# =========================================================================
# risk_simulation_node
# =========================================================================


@pytest.mark.asyncio
async def test_risk_simulation_runs_documented_iterations():
    """[contract] Monte Carlo runs 1000 iterations with seed=42
    (deterministic across runs). Pin so a refactor doesn't drop
    iteration count below the documented threshold."""
    captured = {}

    async def fake_run(*, activities, num_iterations, seed):
        captured.update(
            {"activities": activities, "iterations": num_iterations, "seed": seed},
        )
        return {"p50_duration": 130, "p90_duration": 145}

    state = {"activities": [{"id": "1", "duration_days": 30, "predecessors": []}]}
    with patch("app.services.agents.controls_agent.run_schedule_risk_simulation", fake_run):
        out = await risk_simulation_node(state)

    assert captured["iterations"] == 1000
    assert captured["seed"] == 42
    assert out["status"] == "risk_computed"


@pytest.mark.asyncio
async def test_risk_simulation_skipped_with_no_activities():
    """[edge case] Empty activities list -> skip simulation. Pin:
    status='risk_skipped', not 'risk_failed' — no data is not a
    failure."""
    state = {"activities": []}
    out = await risk_simulation_node(state)
    assert out["status"] == "risk_skipped"
    assert out["risk_results"] is None


@pytest.mark.asyncio
async def test_risk_simulation_failure_isolated():
    async def boom(**_kwargs):
        raise RuntimeError("monte carlo fails")

    state = {"activities": [{"id": "1"}]}
    with patch("app.services.agents.controls_agent.run_schedule_risk_simulation", boom):
        out = await risk_simulation_node(state)

    assert out["status"] == "risk_failed"
    assert "monte carlo fails" in out["error"]


# =========================================================================
# scurve_node
# =========================================================================


@pytest.mark.asyncio
async def test_scurve_node_composes_snapshot_from_state():
    """S-Curve generator receives a single snapshot built from
    PV/EV/AC + SPI from prior EVM result."""
    captured = {}

    async def fake_generate(*, snapshots, bac, start_date):
        captured.update(
            {"snapshots": snapshots, "bac": bac, "start_date": start_date},
        )
        return {"data_points": [1, 2, 3], "forecast_completion": "2026-12-31"}

    state = {
        "bac": "1000000",
        "pv": "500000",
        "ev": "450000",
        "ac": "480000",
        "evm_results": {"spi": "0.98"},
    }
    with patch("app.services.agents.controls_agent.generate_scurve_data", fake_generate):
        out = await scurve_node(state)

    assert isinstance(captured["bac"], Decimal)
    assert captured["bac"] == Decimal("1000000")
    assert len(captured["snapshots"]) == 1
    snap = captured["snapshots"][0]
    assert snap["pv"] == "500000"
    assert snap["ev"] == "450000"
    assert snap["ac"] == "480000"
    assert snap["spi"] == "0.98"
    # Result is condensed (just count + forecast date):
    assert out["scurve_results"]["data_points_count"] == 3
    assert out["scurve_results"]["forecast_completion"] == "2026-12-31"
    assert out["status"] == "scurve_generated"


@pytest.mark.asyncio
async def test_scurve_node_no_evm_uses_default_spi_one():
    """[fallback] Missing evm_results -> SPI defaults to 1 in
    snapshot. Don't crash on missing prior result."""
    captured = {}

    async def fake_generate(*, snapshots, **_kwargs):
        captured["snapshots"] = snapshots
        return {"data_points": [], "forecast_completion": "2026-01-01"}

    state = {
        "bac": "1",
        "pv": "1",
        "ev": "1",
        "ac": "1",
        "evm_results": None,
    }
    with patch("app.services.agents.controls_agent.generate_scurve_data", fake_generate):
        out = await scurve_node(state)

    # SPI default '1' (str of int 1):
    assert captured["snapshots"][0]["spi"] == "1"
    assert out["status"] == "scurve_generated"


@pytest.mark.asyncio
async def test_scurve_node_failure_isolated():
    async def boom(**_kwargs):
        raise RuntimeError("scurve fails")

    state = {
        "bac": "1",
        "pv": "1",
        "ev": "1",
        "ac": "1",
        "evm_results": {},
    }
    with patch("app.services.agents.controls_agent.generate_scurve_data", boom):
        out = await scurve_node(state)

    assert out["status"] == "scurve_failed"
    assert "scurve fails" in out["error"]


# =========================================================================
# Graph build
# =========================================================================


def test_build_controls_agent_returns_compiled_graph():
    graph = build_controls_agent()
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    assert {"compute_evm", "forecast_eac", "risk_simulation", "scurve"} <= nodes
