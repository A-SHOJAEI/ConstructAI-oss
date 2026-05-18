"""Tests for the safety incident response workflow.

Pin the documented stoppage tiers, the OSHA-liaison escalation rule
for critical/high severity, the per-step partial-failure isolation,
and the report-title generation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.orchestration.workflows.safety_incident_response import (
    _estimate_stoppage,
    run_safety_incident_response,
)

# =========================================================================
# _estimate_stoppage — pin documented tiers
# =========================================================================


def test_stoppage_critical_24h():
    """[business invariant] Critical incidents always trigger a full
    24h stoppage in the schedule estimate."""
    assert _estimate_stoppage("critical") == 24


def test_stoppage_high_8h():
    assert _estimate_stoppage("high") == 8


def test_stoppage_medium_2h():
    assert _estimate_stoppage("medium") == 2


def test_stoppage_low_zero():
    assert _estimate_stoppage("low") == 0


def test_stoppage_unknown_severity_zero():
    """[edge case] Unknown severity -> 0 (don't fabricate stoppage)."""
    assert _estimate_stoppage("unknown") == 0
    assert _estimate_stoppage("") == 0


# =========================================================================
# Workflow harness — patch all 4 agents
# =========================================================================


async def _ok_safety(*_args, **_kwargs):
    return {"alerts": [], "status": "completed"}


async def _ok_communication(*_args, **_kwargs):
    return {"messages_sent": 1, "status": "completed"}


async def _ok_controls(*_args, **_kwargs):
    return {"cpi": 0.95, "status": "completed"}


async def _ok_doc(*_args, **_kwargs):
    return {"sections": ["Header"], "status": "ok"}


def _patch_all(*, safety=None, comm=None, ctl=None, doc=None):
    return [
        patch(
            "app.services.orchestration.workflows.safety_incident_response.run_safety_agent",
            safety or _ok_safety,
        ),
        patch(
            "app.services.orchestration.workflows.safety_incident_response.run_communication_agent",
            comm or _ok_communication,
        ),
        patch(
            "app.services.orchestration.workflows.safety_incident_response.run_controls_agent",
            ctl or _ok_controls,
        ),
        patch(
            "app.services.orchestration.workflows.safety_incident_response.run_document_agent",
            doc or _ok_doc,
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
async def test_low_severity_returns_completed():
    incident = {
        "severity": "low",
        "type": "near_miss",
        "location": "Zone A",
        "description": "Worker tripped over hose",
    }
    patches = _patch_all()
    _start(patches)
    try:
        out = await run_safety_incident_response("p-1abc2def", incident)
    finally:
        _stop(patches)

    assert out["status"] == "completed"
    assert out["incident"]["status"] == "reported"
    assert out["incident"]["type"] == "near_miss"
    assert out["incident"]["severity"] == "low"
    assert out["log_entry"]["incident_id"] == "INC-p-1abc2d"
    # 5 steps total (4 with agent + 1 notifications):
    assert len(out["steps_completed"]) == 5


# =========================================================================
# Notification escalation tiers
# =========================================================================


@pytest.mark.asyncio
async def test_low_severity_no_extra_recipients():
    """[business invariant] Low severity only notifies safety_manager
    + superintendent — no PM, no OSHA liaison."""
    patches = _patch_all()
    _start(patches)
    try:
        out = await run_safety_incident_response("p-1", {"severity": "low"})
    finally:
        _stop(patches)
    assert out["notifications"]["recipients"] == ["safety_manager", "superintendent"]


@pytest.mark.asyncio
async def test_medium_severity_no_extra_recipients():
    """Medium severity does NOT trigger PM/OSHA escalation."""
    patches = _patch_all()
    _start(patches)
    try:
        out = await run_safety_incident_response("p-1", {"severity": "medium"})
    finally:
        _stop(patches)
    assert "project_manager" not in out["notifications"]["recipients"]
    assert "osha_liaison" not in out["notifications"]["recipients"]


@pytest.mark.asyncio
async def test_high_severity_escalates_to_pm_and_osha():
    """[business invariant] High severity adds PM + OSHA liaison.
    OSHA notification is REGULATORY — refactor must NOT silently
    drop this from the recipient list."""
    patches = _patch_all()
    _start(patches)
    try:
        out = await run_safety_incident_response("p-1", {"severity": "high"})
    finally:
        _stop(patches)
    recipients = out["notifications"]["recipients"]
    assert "safety_manager" in recipients
    assert "superintendent" in recipients
    assert "project_manager" in recipients
    assert "osha_liaison" in recipients


@pytest.mark.asyncio
async def test_critical_severity_full_escalation():
    """Critical severity gets the full 4-recipient list AND triggers
    the documented 24h stoppage."""
    patches = _patch_all()
    _start(patches)
    try:
        out = await run_safety_incident_response("p-1", {"severity": "critical"})
    finally:
        _stop(patches)
    recipients = out["notifications"]["recipients"]
    assert set(recipients) == {
        "safety_manager",
        "superintendent",
        "project_manager",
        "osha_liaison",
    }
    assert out["schedule_impact"]["work_stoppage_hours"] == 24


# =========================================================================
# Default incident type
# =========================================================================


@pytest.mark.asyncio
async def test_no_severity_defaults_to_low():
    """Missing severity field -> 'low' default (no over-escalation)."""
    patches = _patch_all()
    _start(patches)
    try:
        out = await run_safety_incident_response("p-1", {})
    finally:
        _stop(patches)
    assert out["incident"]["severity"] == "low"
    assert out["schedule_impact"]["work_stoppage_hours"] == 0


@pytest.mark.asyncio
async def test_no_type_defaults_to_near_miss():
    patches = _patch_all()
    _start(patches)
    try:
        out = await run_safety_incident_response("p-1", {})
    finally:
        _stop(patches)
    assert out["incident"]["type"] == "near_miss"


# =========================================================================
# Report title formatting
# =========================================================================


@pytest.mark.asyncio
async def test_report_title_humanizes_incident_type():
    """fall_from_height -> 'Fall From Height' (underscores stripped,
    title-cased)."""
    patches = _patch_all()
    _start(patches)
    try:
        out = await run_safety_incident_response(
            "p-1", {"severity": "high", "type": "fall_from_height"}
        )
    finally:
        _stop(patches)
    assert "Fall From Height" in out["report"]["title"]


# =========================================================================
# Partial-failure isolation
# =========================================================================


@pytest.mark.asyncio
async def test_safety_agent_failure_yields_partial():
    async def boom(*_args, **_kwargs):
        raise RuntimeError("LLM down")

    patches = _patch_all(safety=boom)
    _start(patches)
    try:
        out = await run_safety_incident_response("p-1", {"severity": "high"})
    finally:
        _stop(patches)

    assert out["status"] == "partial"
    assert out["incident"]["status"] == "failed"
    assert out["incident"]["error"] == "RuntimeError"
    # Notifications STILL fire — safety alerts are too important
    # to skip on agent failure:
    assert out["notifications"]["status"] == "sent"
    assert "osha_liaison" in out["notifications"]["recipients"]


@pytest.mark.asyncio
async def test_communication_agent_failure_yields_partial():
    async def boom(*_args, **_kwargs):
        raise ValueError("kafka down")

    patches = _patch_all(comm=boom)
    _start(patches)
    try:
        out = await run_safety_incident_response("p-1", {"severity": "low"})
    finally:
        _stop(patches)

    assert out["status"] == "partial"
    assert out["log_entry"]["status"] == "failed"
    assert out["log_entry"]["error"] == "ValueError"


@pytest.mark.asyncio
async def test_controls_agent_failure_yields_partial():
    async def boom(*_args, **_kwargs):
        raise ConnectionError("controls service")

    patches = _patch_all(ctl=boom)
    _start(patches)
    try:
        out = await run_safety_incident_response("p-1", {"severity": "critical"})
    finally:
        _stop(patches)

    assert out["status"] == "partial"
    assert out["schedule_impact"]["status"] == "failed"
    # Stoppage estimate still populated — pure function, doesn't
    # depend on the agent:
    assert out["schedule_impact"]["work_stoppage_hours"] == 24


@pytest.mark.asyncio
async def test_document_agent_failure_yields_partial():
    async def boom(*_args, **_kwargs):
        raise RuntimeError("template missing")

    patches = _patch_all(doc=boom)
    _start(patches)
    try:
        out = await run_safety_incident_response("p-1", {"severity": "high"})
    finally:
        _stop(patches)

    assert out["status"] == "partial"
    assert out["report"]["status"] == "failed"
    assert out["report"]["error"] == "RuntimeError"
    # Title still generated — uses incident dict, not agent result:
    assert "Safety Incident Report" in out["report"]["title"]


@pytest.mark.asyncio
async def test_all_agents_fail_still_returns_complete_package():
    """[robustness] Every agent fails -> we still get all top-level
    keys + notifications still go out (regulatory requirement)."""

    async def boom(*_args, **_kwargs):
        raise RuntimeError("everything broken")

    patches = _patch_all(safety=boom, comm=boom, ctl=boom, doc=boom)
    _start(patches)
    try:
        out = await run_safety_incident_response("p-1", {"severity": "critical"})
    finally:
        _stop(patches)

    assert {
        "project_id",
        "incident",
        "log_entry",
        "schedule_impact",
        "report",
        "notifications",
        "steps_completed",
        "status",
    } <= set(out)
    assert out["status"] == "partial"
    # OSHA liaison STILL notified — invariant for critical incidents:
    assert "osha_liaison" in out["notifications"]["recipients"]


# =========================================================================
# Incident ID format
# =========================================================================


@pytest.mark.asyncio
async def test_incident_id_uses_first_8_chars_of_project_id():
    """[contract] INC-{project_id[:8]} so multiple workflows on the
    same project are distinguishable from prefix alone."""
    patches = _patch_all()
    _start(patches)
    try:
        out = await run_safety_incident_response("abcdef1234567890", {})
    finally:
        _stop(patches)
    assert out["log_entry"]["incident_id"] == "INC-abcdef12"
