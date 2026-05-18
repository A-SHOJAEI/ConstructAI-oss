"""Tests for the workflow orchestrator dispatch logic.

Three workflow types are supported. The orchestrator wraps DB writes
in a savepoint so partial failures don't taint the outer transaction.
Pin every branch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.orchestration.orchestrator_agent import (
    WORKFLOW_RUNNERS,
    execute_workflow,
)

# ---- WORKFLOW_RUNNERS map ----------------------------------------------


def test_workflow_runners_includes_all_documented_types():
    """The orchestrator's runner dict must include the three supported
    workflow types — adding a new workflow without registering it here
    is a silent regression."""
    assert "new_project_onboarding" in WORKFLOW_RUNNERS
    assert "change_order_processing" in WORKFLOW_RUNNERS
    assert "safety_incident_response" in WORKFLOW_RUNNERS


# ---- execute_workflow happy paths --------------------------------------


async def test_execute_unknown_workflow_returns_failure():
    out = await execute_workflow("ghost_workflow", "proj-1", {})
    assert out["status"] == "failed"
    assert "Unknown workflow type" in out["error"]


async def test_execute_onboarding_dispatches_to_run_onboarding():
    """Each workflow type forwards to its specific runner with the
    documented argument shape — onboarding takes ``document_ids``."""
    fake = AsyncMock(return_value={"status": "success"})
    with patch.dict(WORKFLOW_RUNNERS, {"new_project_onboarding": fake}):
        out = await execute_workflow(
            "new_project_onboarding",
            "proj-1",
            {"document_ids": ["doc-1", "doc-2"]},
        )
    assert out == {"status": "success"}
    fake.assert_awaited_once()
    kwargs = fake.call_args.kwargs
    assert kwargs["project_id"] == "proj-1"
    assert kwargs["document_ids"] == ["doc-1", "doc-2"]


async def test_execute_change_order_dispatches_with_change_order_data():
    """Change-order workflow forwards the entire input_data as
    ``change_order_data`` — verify the keyword name is right (a
    refactor that renames it would silently break the runner)."""
    fake = AsyncMock(return_value={"status": "approved"})
    with patch.dict(WORKFLOW_RUNNERS, {"change_order_processing": fake}):
        out = await execute_workflow(
            "change_order_processing",
            "proj-1",
            {"co_id": "CO-001", "amount": 50000},
        )
    assert out == {"status": "approved"}
    kwargs = fake.call_args.kwargs
    assert kwargs["change_order_data"] == {"co_id": "CO-001", "amount": 50000}


async def test_execute_safety_incident_dispatches_with_incident_data():
    fake = AsyncMock(return_value={"status": "logged"})
    with patch.dict(WORKFLOW_RUNNERS, {"safety_incident_response": fake}):
        out = await execute_workflow(
            "safety_incident_response",
            "proj-1",
            {"severity": "P1_critical"},
        )
    assert out == {"status": "logged"}
    kwargs = fake.call_args.kwargs
    assert kwargs["incident_data"] == {"severity": "P1_critical"}


# ---- DB savepoint behaviour --------------------------------------------


async def test_execute_with_db_uses_savepoint():
    """When a db session is provided, the workflow runs inside
    db.begin_nested() so partial failures don't taint the outer
    transaction."""
    fake_runner = AsyncMock(return_value={"status": "success"})

    db = AsyncMock()
    # begin_nested returns an async context manager
    nested_cm = AsyncMock()
    nested_cm.__aenter__ = AsyncMock()
    nested_cm.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested_cm)
    db.flush = AsyncMock()
    db.rollback = AsyncMock()

    with patch.dict(WORKFLOW_RUNNERS, {"new_project_onboarding": fake_runner}):
        out = await execute_workflow(
            "new_project_onboarding",
            "proj-1",
            {"document_ids": []},
            db=db,
        )
    assert out["status"] == "success"
    db.begin_nested.assert_called_once()
    db.flush.assert_awaited_once()


async def test_execute_runner_exception_returns_failure_and_rolls_back_db():
    """A runner failure must NOT propagate — the orchestrator catches,
    returns a generic failure dict, and rolls back the session.

    The error message returned to the caller is intentionally generic
    to avoid leaking internal details to the API layer."""
    fake_runner = AsyncMock(side_effect=RuntimeError("internal boom"))

    db = AsyncMock()
    nested_cm = AsyncMock()
    nested_cm.__aenter__ = AsyncMock()
    nested_cm.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested_cm)
    db.flush = AsyncMock()
    db.rollback = AsyncMock()

    with patch.dict(WORKFLOW_RUNNERS, {"new_project_onboarding": fake_runner}):
        out = await execute_workflow(
            "new_project_onboarding",
            "proj-1",
            {},
            db=db,
        )
    assert out["status"] == "failed"
    # Generic error — internal exception text not leaked:
    assert "internal boom" not in out["error"]
    db.rollback.assert_awaited_once()


async def test_execute_runner_exception_without_db_does_not_call_rollback():
    """No db session = no rollback to perform — but still return a
    generic failure response."""
    fake_runner = AsyncMock(side_effect=RuntimeError("boom"))
    with patch.dict(WORKFLOW_RUNNERS, {"new_project_onboarding": fake_runner}):
        out = await execute_workflow("new_project_onboarding", "proj-1", {})
    assert out["status"] == "failed"


async def test_execute_without_db_skips_savepoint():
    """No db session = no begin_nested / flush. Just runs the workflow
    directly."""
    fake_runner = AsyncMock(return_value={"status": "success"})
    with patch.dict(WORKFLOW_RUNNERS, {"new_project_onboarding": fake_runner}):
        out = await execute_workflow(
            "new_project_onboarding",
            "proj-1",
            {"document_ids": []},
        )
    assert out == {"status": "success"}


# ---- input_data defaults -----------------------------------------------


async def test_execute_onboarding_with_missing_document_ids_uses_empty_list():
    """If document_ids isn't in input_data, the runner must receive
    an empty list rather than KeyError."""
    fake = AsyncMock(return_value={"status": "success"})
    with patch.dict(WORKFLOW_RUNNERS, {"new_project_onboarding": fake}):
        await execute_workflow("new_project_onboarding", "proj-1", {})
    assert fake.call_args.kwargs["document_ids"] == []


@pytest.mark.parametrize(
    "workflow_type",
    ["new_project_onboarding", "change_order_processing", "safety_incident_response"],
)
async def test_each_documented_workflow_dispatches_without_error(workflow_type):
    """Smoke test: every documented workflow type dispatches to its
    runner (with whatever payload shape it expects) without raising."""
    fake = AsyncMock(return_value={"status": "success"})
    with patch.dict(WORKFLOW_RUNNERS, {workflow_type: fake}):
        out = await execute_workflow(workflow_type, "proj-1", {})
    assert out["status"] == "success"
