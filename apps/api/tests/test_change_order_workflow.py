"""Tests for the change-order processing workflow.

Pin the partial-failure semantics, the 3-way agent fan-out, the
cost-percentage and risk-score formulas, and the approval-required
contract — change orders go through human review (per L-12), so the
workflow MUST always return a complete package even when individual
agent calls fail.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.orchestration.workflows.change_order_processing import (
    run_change_order_processing,
)

# =========================================================================
# Helpers — ALL 5 agents must be patched, otherwise tests hit the
# real LLM/DB layer.
# =========================================================================


async def _ok_doc(*_args, **_kwargs):
    return {"sections": [{"title": "Scope"}], "status": "ok"}


async def _ok_estimate(*_args, **_kwargs):
    return {"total_cost": 50_000, "status": "completed"}


async def _ok_schedule(*_args, **_kwargs):
    return {"impacted_activities": 3, "status": "completed"}


async def _ok_controls(*_args, **_kwargs):
    return {"cpi": 0.94, "spi": 0.98, "status": "completed"}


async def _ok_procurement(*_args, **_kwargs):
    return {"materials_affected": 2, "status": "completed"}


def _patch_all_agents(*, doc=None, est=None, sch=None, ctl=None, proc=None):
    """Build the 5 patches needed for every test. Default to happy
    path; pass replacements for ones we want to fail."""
    return [
        patch(
            "app.services.orchestration.workflows.change_order_processing.run_document_agent",
            doc or _ok_doc,
        ),
        patch(
            "app.services.orchestration.workflows.change_order_processing.run_estimating_agent",
            est or _ok_estimate,
        ),
        patch(
            "app.services.orchestration.workflows.change_order_processing.run_scheduling_agent",
            sch or _ok_schedule,
        ),
        patch(
            "app.services.orchestration.workflows.change_order_processing.run_controls_agent",
            ctl or _ok_controls,
        ),
        patch(
            "app.services.orchestration.workflows.change_order_processing.run_procurement_agent",
            proc or _ok_procurement,
        ),
    ]


# =========================================================================
# Happy path — all agents succeed
# =========================================================================


@pytest.mark.asyncio
async def test_all_agents_succeed_returns_waiting_human():
    """[business invariant] Even when all agents succeed, the CO is
    NOT auto-approved — overall status stays ``waiting_human`` because
    PM approval is mandatory (L-12)."""
    co_data = {
        "description": "Add 50ft of cast-in-place concrete wall",
        "type": "scope_change",
        "cost_impact": 50_000,
        "original_contract": 1_000_000,
        "schedule_impact_days": 5,
    }
    patches = _patch_all_agents()
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", co_data)
    finally:
        for p in patches:
            p.stop()

    assert out["status"] == "waiting_human"
    assert out["approval_required"] is True
    assert out["project_id"] == "p-1"
    # All 4 steps appear in the trail (parse_scope, impact_analysis,
    # material_impact — that's 3 entries; the impact_analysis row
    # rolls up the 3 parallel sub-steps):
    step_names = {s["step"] for s in out["steps_completed"]}
    assert step_names == {"parse_scope", "impact_analysis", "material_impact"}
    # All steps completed:
    for step in out["steps_completed"]:
        assert step["status"] == "completed"


# =========================================================================
# Cost percentage formula
# =========================================================================


@pytest.mark.asyncio
async def test_cost_percentage_50k_of_1m_is_5_percent():
    """50_000 / 1_000_000 * 100 = 5.00."""
    co_data = {"cost_impact": 50_000, "original_contract": 1_000_000}
    patches = _patch_all_agents()
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", co_data)
    finally:
        for p in patches:
            p.stop()
    assert out["cost_impact"]["percentage"] == 5.0
    assert out["cost_impact"]["amount"] == 50_000
    assert out["cost_impact"]["original_contract"] == 1_000_000


@pytest.mark.asyncio
async def test_cost_percentage_zero_contract_returns_zero():
    """[edge case] Division by zero -> 0.0, not crash."""
    co_data = {"cost_impact": 50_000, "original_contract": 0}
    patches = _patch_all_agents()
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", co_data)
    finally:
        for p in patches:
            p.stop()
    assert out["cost_impact"]["percentage"] == 0.0


@pytest.mark.asyncio
async def test_cost_percentage_zero_cost_returns_zero():
    co_data = {"cost_impact": 0, "original_contract": 1_000_000}
    patches = _patch_all_agents()
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", co_data)
    finally:
        for p in patches:
            p.stop()
    assert out["cost_impact"]["percentage"] == 0.0


@pytest.mark.asyncio
async def test_cost_percentage_rounds_to_two_decimals():
    """100 / 30_000 * 100 = 0.333... -> 0.33."""
    co_data = {"cost_impact": 100, "original_contract": 30_000}
    patches = _patch_all_agents()
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", co_data)
    finally:
        for p in patches:
            p.stop()
    assert out["cost_impact"]["percentage"] == 0.33


# =========================================================================
# Risk-level tiers — pin documented thresholds
# =========================================================================


@pytest.mark.asyncio
async def test_risk_level_low_under_10():
    """cost% 5 + days 0 = 5 -> low."""
    co_data = {"cost_impact": 50_000, "original_contract": 1_000_000, "schedule_impact_days": 0}
    patches = _patch_all_agents()
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", co_data)
    finally:
        for p in patches:
            p.stop()
    assert out["risk_exposure"]["risk_level"] == "low"
    assert out["risk_exposure"]["risk_score"] == 5.0


@pytest.mark.asyncio
async def test_risk_level_medium_at_10():
    """[boundary] Exactly 10 -> medium (>= comparison)."""
    co_data = {
        "cost_impact": 50_000,
        "original_contract": 1_000_000,
        "schedule_impact_days": 10,
    }
    patches = _patch_all_agents()
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", co_data)
    finally:
        for p in patches:
            p.stop()
    # 5 + 10*0.5 = 10.0 -> medium
    assert out["risk_exposure"]["risk_score"] == 10.0
    assert out["risk_exposure"]["risk_level"] == "medium"


@pytest.mark.asyncio
async def test_risk_level_medium_under_30():
    co_data = {
        "cost_impact": 200_000,
        "original_contract": 1_000_000,
        "schedule_impact_days": 10,
    }
    patches = _patch_all_agents()
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", co_data)
    finally:
        for p in patches:
            p.stop()
    # 20 + 10*0.5 = 25.0 -> medium
    assert out["risk_exposure"]["risk_score"] == 25.0
    assert out["risk_exposure"]["risk_level"] == "medium"


@pytest.mark.asyncio
async def test_risk_level_high_at_30():
    """[boundary] Exactly 30 -> high (>= comparison)."""
    co_data = {
        "cost_impact": 300_000,
        "original_contract": 1_000_000,
        "schedule_impact_days": 0,
    }
    patches = _patch_all_agents()
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", co_data)
    finally:
        for p in patches:
            p.stop()
    # 30 + 0 = 30.0 -> high
    assert out["risk_exposure"]["risk_score"] == 30.0
    assert out["risk_exposure"]["risk_level"] == "high"


@pytest.mark.asyncio
async def test_risk_level_high_combined():
    """High cost AND schedule slip -> definitively high."""
    co_data = {
        "cost_impact": 250_000,
        "original_contract": 1_000_000,  # 25%
        "schedule_impact_days": 30,  # +15
    }
    patches = _patch_all_agents()
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", co_data)
    finally:
        for p in patches:
            p.stop()
    assert out["risk_exposure"]["risk_score"] == 40.0
    assert out["risk_exposure"]["risk_level"] == "high"


# =========================================================================
# Partial-failure semantics — every step has its own try/except,
# steps_completed tracks each, overall_status flips to "partial".
# =========================================================================


@pytest.mark.asyncio
async def test_doc_agent_failure_yields_partial_status():
    async def doc_boom(*_args, **_kwargs):
        raise RuntimeError("LLM rate limit")

    patches = _patch_all_agents(doc=doc_boom)
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", {"cost_impact": 1, "original_contract": 100})
    finally:
        for p in patches:
            p.stop()

    assert out["status"] == "partial"
    # Scope still carries description/type so downstream consumers
    # can still render something:
    assert out["scope"]["status"] == "failed"
    assert out["scope"]["error"] == "RuntimeError"
    parse_step = next(s for s in out["steps_completed"] if s["step"] == "parse_scope")
    assert parse_step["status"] == "failed"


@pytest.mark.asyncio
async def test_estimating_agent_failure_yields_partial():
    async def est_boom(*_args, **_kwargs):
        raise ValueError("missing project")

    patches = _patch_all_agents(est=est_boom)
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing(
            "p-1", {"cost_impact": 50_000, "original_contract": 1_000_000}
        )
    finally:
        for p in patches:
            p.stop()

    assert out["status"] == "partial"
    impact_step = next(s for s in out["steps_completed"] if s["step"] == "impact_analysis")
    assert impact_step["status"] == "partial"
    assert impact_step["sub_steps"]["cost_impact"] == "failed"
    # Schedule + risk still completed:
    assert impact_step["sub_steps"]["schedule_impact"] == "completed"
    assert impact_step["sub_steps"]["risk_exposure"] == "completed"


@pytest.mark.asyncio
async def test_all_three_parallel_failures_marked_partial():
    """[fan-in robustness] All 3 parallel agents fail -> impact_analysis
    sub_steps all 'failed', overall partial. Workflow does NOT crash."""

    async def boom(*_args, **_kwargs):
        raise RuntimeError("upstream down")

    patches = _patch_all_agents(est=boom, sch=boom, ctl=boom)
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing(
            "p-1", {"cost_impact": 50_000, "original_contract": 1_000_000}
        )
    finally:
        for p in patches:
            p.stop()

    assert out["status"] == "partial"
    impact_step = next(s for s in out["steps_completed"] if s["step"] == "impact_analysis")
    assert impact_step["sub_steps"]["cost_impact"] == "failed"
    assert impact_step["sub_steps"]["schedule_impact"] == "failed"
    assert impact_step["sub_steps"]["risk_exposure"] == "failed"


@pytest.mark.asyncio
async def test_procurement_failure_yields_partial():
    async def proc_boom(*_args, **_kwargs):
        raise ConnectionError("supply DB down")

    patches = _patch_all_agents(proc=proc_boom)
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", {"cost_impact": 1, "original_contract": 100})
    finally:
        for p in patches:
            p.stop()

    assert out["status"] == "partial"
    mat_step = next(s for s in out["steps_completed"] if s["step"] == "material_impact")
    assert mat_step["status"] == "failed"
    assert mat_step["error"] == "ConnectionError"
    # Material impact dict has the error:
    assert out["material_impact"]["error"] == "ConnectionError"


@pytest.mark.asyncio
async def test_every_step_failure_still_returns_complete_package():
    """[robustness] Every single agent fails -> we still get a package
    with all top-level keys (so the API can render an error UI)."""

    async def boom(*_args, **_kwargs):
        raise RuntimeError("everything is broken")

    patches = _patch_all_agents(doc=boom, est=boom, sch=boom, ctl=boom, proc=boom)
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", {"cost_impact": 1, "original_contract": 100})
    finally:
        for p in patches:
            p.stop()

    # Pin: top-level package contract is intact even with everything
    # failed:
    assert {
        "project_id",
        "scope",
        "cost_impact",
        "schedule_impact",
        "risk_exposure",
        "material_impact",
        "steps_completed",
        "approval_required",
        "status",
    } <= set(out)
    assert out["status"] == "partial"
    assert out["approval_required"] is True


# =========================================================================
# Default fallbacks for missing input fields
# =========================================================================


@pytest.mark.asyncio
async def test_minimal_input_uses_documented_defaults():
    """[edge case] Sparse change_order_data -> documented defaults
    apply (bac=1M, pv=500k, ev=450k, ac=480k, type='scope_change',
    filename='change_order.pdf')."""
    captured = {}

    async def captured_doc(*_args, **kwargs):
        captured["doc"] = kwargs
        return {"status": "ok"}

    async def captured_controls(*_args, **kwargs):
        captured["controls"] = kwargs
        return {"status": "ok"}

    patches = _patch_all_agents(doc=captured_doc, ctl=captured_controls)
    for p in patches:
        p.start()
    try:
        out = await run_change_order_processing("p-1", {})
    finally:
        for p in patches:
            p.stop()

    # Document agent fell back to "co-p-1" doc id and default filename:
    assert captured["doc"]["document_id"] == "co-p-1"
    assert captured["doc"]["filename"] == "change_order.pdf"

    # Controls agent received the documented EVM defaults:
    assert captured["controls"]["bac"] == 1_000_000
    assert captured["controls"]["pv"] == 500_000
    assert captured["controls"]["ev"] == 450_000
    assert captured["controls"]["ac"] == 480_000

    # Scope type defaults to scope_change:
    assert out["scope"]["type"] == "scope_change"
