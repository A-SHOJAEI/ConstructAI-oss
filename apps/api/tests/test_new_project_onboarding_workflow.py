"""Tests for the new-project onboarding workflow.

Pin the documented step sequence (1 sequential -> 3 parallel ->
1 sequential), the per-step partial-failure isolation, and the
brief contract that downstream consumers depend on.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.orchestration.workflows.new_project_onboarding import run_onboarding

# =========================================================================
# Patch harness — 5 agents
# =========================================================================


async def _ok_doc(*_args, **_kwargs):
    return {"classified_as": "spec", "status": "ok"}


async def _ok_estimate(*_args, **_kwargs):
    return {"total_cost": 1_500_000, "status": "completed"}


async def _ok_schedule(*_args, **_kwargs):
    return {"duration_days": 365, "status": "completed"}


async def _ok_logistics(*_args, **_kwargs):
    return {"laydown_zones": 3, "status": "completed"}


async def _ok_procurement(*_args, **_kwargs):
    return {"long_lead_items": 5, "status": "completed"}


def _patch_all(*, doc=None, est=None, sch=None, log=None, proc=None):
    return [
        patch(
            "app.services.orchestration.workflows.new_project_onboarding.run_document_agent",
            doc or _ok_doc,
        ),
        patch(
            "app.services.orchestration.workflows.new_project_onboarding.run_estimating_agent",
            est or _ok_estimate,
        ),
        patch(
            "app.services.orchestration.workflows.new_project_onboarding.run_scheduling_agent",
            sch or _ok_schedule,
        ),
        patch(
            "app.services.orchestration.workflows.new_project_onboarding.run_logistics_agent",
            log or _ok_logistics,
        ),
        patch(
            "app.services.orchestration.workflows.new_project_onboarding.run_procurement_agent",
            proc or _ok_procurement,
        ),
    ]


def _start(patches):
    for p in patches:
        p.start()


def _stop(patches):
    for p in patches:
        p.stop()


# =========================================================================
# Happy path
# =========================================================================


@pytest.mark.asyncio
async def test_no_documents_completes_successfully():
    """[edge case] document_ids=None -> Step 1 still completes
    (zero classified docs), parallel + procurement still run."""
    patches = _patch_all()
    _start(patches)
    try:
        out = await run_onboarding("p-1")
    finally:
        _stop(patches)

    assert out["status"] == "completed"
    assert out["project_id"] == "p-1"
    assert out["results"]["documents"]["classified_documents"] == 0


@pytest.mark.asyncio
async def test_with_documents_classifies_each():
    """Step 1 invokes document_agent once per id."""
    captured_ids = []

    async def captured_doc(*, document_id, **_kwargs):
        captured_ids.append(document_id)
        return {"id": document_id}

    patches = _patch_all(doc=captured_doc)
    _start(patches)
    try:
        out = await run_onboarding("p-1", document_ids=["doc-a", "doc-b", "doc-c"])
    finally:
        _stop(patches)

    assert captured_ids == ["doc-a", "doc-b", "doc-c"]
    assert out["results"]["documents"]["classified_documents"] == 3


@pytest.mark.asyncio
async def test_full_happy_path_all_5_steps_recorded():
    """[contract] steps_completed should track all 5 step records,
    in their canonical names — UI rendering depends on this."""
    patches = _patch_all()
    _start(patches)
    try:
        out = await run_onboarding("p-1", document_ids=["doc-a"])
    finally:
        _stop(patches)

    step_names = [s["step"] for s in out["steps_completed"]]
    assert step_names == [
        "document_classification",
        "cost_estimation",
        "schedule_analysis",
        "site_layout",
        "procurement_setup",
    ]
    for step in out["steps_completed"]:
        assert step["status"] == "completed"


@pytest.mark.asyncio
async def test_results_dict_keys():
    """[contract] All 5 results keys present in the brief dict."""
    patches = _patch_all()
    _start(patches)
    try:
        out = await run_onboarding("p-1")
    finally:
        _stop(patches)

    expected = {"documents", "estimate", "schedule", "site_layout", "procurement"}
    assert expected <= set(out["results"])


# =========================================================================
# Default fallbacks
# =========================================================================


@pytest.mark.asyncio
async def test_estimate_type_defaults_to_conceptual():
    """[default] estimate_type fallback is 'conceptual' (not 'detailed') —
    onboarding starts BEFORE we have a detailed scope."""
    captured = {}

    async def captured_est(*, estimate_type, **_kwargs):
        captured["estimate_type"] = estimate_type
        return {"status": "ok"}

    patches = _patch_all(est=captured_est)
    _start(patches)
    try:
        await run_onboarding("p-1")
    finally:
        _stop(patches)

    assert captured["estimate_type"] == "conceptual"


@pytest.mark.asyncio
async def test_filename_default_uses_doc_id():
    """[edge case] No filename in input_data -> 'document_{doc_id}'."""
    captured = {}

    async def captured_doc(*, document_id, filename, **_kwargs):
        captured["filename"] = filename
        captured["document_id"] = document_id
        return {}

    patches = _patch_all(doc=captured_doc)
    _start(patches)
    try:
        await run_onboarding("p-1", document_ids=["abc-123"])
    finally:
        _stop(patches)

    assert captured["filename"] == "document_abc-123"


# =========================================================================
# Per-step partial failures
# =========================================================================


@pytest.mark.asyncio
async def test_doc_step_failure_yields_partial():
    async def boom(*_args, **_kwargs):
        raise RuntimeError("LLM down")

    patches = _patch_all(doc=boom)
    _start(patches)
    try:
        out = await run_onboarding("p-1", document_ids=["doc-a"])
    finally:
        _stop(patches)

    assert out["status"] == "partial"
    assert out["results"]["documents"]["status"] == "failed"
    assert out["results"]["documents"]["error"] == "RuntimeError"
    # Step 1 failure does NOT block parallel agents:
    assert out["results"]["estimate"]["status"] == "completed"


@pytest.mark.asyncio
async def test_estimating_failure_does_not_block_other_parallel_agents():
    """[fan-in robustness] One failed parallel agent doesn't kill the
    other two."""

    async def boom(*_args, **_kwargs):
        raise ValueError("rate model missing")

    patches = _patch_all(est=boom)
    _start(patches)
    try:
        out = await run_onboarding("p-1")
    finally:
        _stop(patches)

    assert out["status"] == "partial"
    assert out["results"]["estimate"]["status"] == "failed"
    # Other parallel ones still completed:
    assert out["results"]["schedule"]["status"] == "completed"
    assert out["results"]["site_layout"]["status"] == "completed"
    # And procurement still ran:
    assert out["results"]["procurement"]["status"] == "completed"


@pytest.mark.asyncio
async def test_all_3_parallel_failures_still_runs_procurement():
    """[robustness] All 3 parallel agents fail -> step 5 still runs
    (it doesn't depend on parallel results, it's a separate setup)."""

    async def boom(*_args, **_kwargs):
        raise RuntimeError("everything broken")

    patches = _patch_all(est=boom, sch=boom, log=boom)
    _start(patches)
    try:
        out = await run_onboarding("p-1")
    finally:
        _stop(patches)

    assert out["status"] == "partial"
    # Procurement still ran:
    assert out["results"]["procurement"]["status"] == "completed"
    # But all 3 parallel agents failed:
    assert out["results"]["estimate"]["status"] == "failed"
    assert out["results"]["schedule"]["status"] == "failed"
    assert out["results"]["site_layout"]["status"] == "failed"


@pytest.mark.asyncio
async def test_procurement_failure_yields_partial():
    async def boom(*_args, **_kwargs):
        raise ConnectionError("supply DB down")

    patches = _patch_all(proc=boom)
    _start(patches)
    try:
        out = await run_onboarding("p-1")
    finally:
        _stop(patches)

    assert out["status"] == "partial"
    assert out["results"]["procurement"]["status"] == "failed"
    assert out["results"]["procurement"]["error"] == "ConnectionError"


@pytest.mark.asyncio
async def test_every_agent_fails_still_returns_complete_brief():
    """[robustness] Every single agent fails -> brief contract is
    still intact so the API can render the failure UI."""

    async def boom(*_args, **_kwargs):
        raise RuntimeError("apocalypse")

    patches = _patch_all(doc=boom, est=boom, sch=boom, log=boom, proc=boom)
    _start(patches)
    try:
        out = await run_onboarding("p-1", document_ids=["doc-a"])
    finally:
        _stop(patches)

    # Top-level brief contract:
    assert {"project_id", "steps_completed", "results", "status"} <= set(out)
    assert out["status"] == "partial"
    # All 5 step records present (each as failed):
    assert len(out["steps_completed"]) == 5
    for step in out["steps_completed"]:
        assert step["status"] == "failed"


# =========================================================================
# input_data threading
# =========================================================================


@pytest.mark.asyncio
async def test_input_data_threads_to_correct_agents():
    """activities -> scheduling, site_data -> logistics, materials ->
    procurement."""
    captured = {}

    async def captured_sch(*, activities, **_kwargs):
        captured["activities"] = activities
        return {}

    async def captured_log(*, site_data, **_kwargs):
        captured["site_data"] = site_data
        return {}

    async def captured_proc(*, materials, **_kwargs):
        captured["materials"] = materials
        return {}

    patches = _patch_all(sch=captured_sch, log=captured_log, proc=captured_proc)
    _start(patches)
    try:
        await run_onboarding(
            "p-1",
            input_data={
                "activities": [{"id": "act-1"}],
                "site_data": {"area_sqft": 50000},
                "materials": ["concrete", "rebar"],
            },
        )
    finally:
        _stop(patches)

    assert captured["activities"] == [{"id": "act-1"}]
    assert captured["site_data"] == {"area_sqft": 50000}
    assert captured["materials"] == ["concrete", "rebar"]


@pytest.mark.asyncio
async def test_no_input_data_uses_none_defaults():
    """input_data=None -> agents receive None for optional fields."""
    captured = {}

    async def captured_sch(*, activities, **_kwargs):
        captured["activities"] = activities
        return {}

    patches = _patch_all(sch=captured_sch)
    _start(patches)
    try:
        await run_onboarding("p-1")
    finally:
        _stop(patches)

    assert captured["activities"] is None
