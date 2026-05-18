"""Tests for the scheduler-callable maintenance tasks.

These functions are called by APScheduler in production. They aren't
endpoints, but they shape billing data, EVM snapshots, weekly briefs,
and audit-log retention — so any silent failure has real product
impact. Mock the DB / external imports so each test exercises the
function's own bookkeeping logic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.scheduled_tasks import (
    cleanup_reconnect_tokens,
    compute_daily_evm_snapshots,
    purge_old_audit_logs,
    refresh_ppi_data,
)

# ---- refresh_ppi_data ---------------------------------------------------


async def test_refresh_ppi_data_returns_per_series_status():
    """When fetch_bls_ppi succeeds for every configured series, the
    result maps each category → True."""
    series_map = {"concrete": "WPU13110205", "steel": "WPU101"}
    fetch = AsyncMock(return_value={"latest_value": 142.5})
    with (
        patch("app.services.estimating.cost_database._BLS_SERIES_MAP", series_map),
        patch("app.services.estimating.cost_database.fetch_bls_ppi", fetch),
    ):
        result = await refresh_ppi_data()
    assert result == {"concrete": True, "steel": True}
    assert fetch.await_count == 2


async def test_refresh_ppi_data_marks_empty_responses_as_failure():
    series_map = {"concrete": "WPU13110205"}
    # Empty response = no latest_value → False, but no exception.
    fetch = AsyncMock(return_value={})
    with (
        patch("app.services.estimating.cost_database._BLS_SERIES_MAP", series_map),
        patch("app.services.estimating.cost_database.fetch_bls_ppi", fetch),
    ):
        result = await refresh_ppi_data()
    assert result == {"concrete": False}


async def test_refresh_ppi_data_isolates_per_series_failures():
    """A network error for one series must not abort the whole batch —
    it just gets recorded as False."""
    series_map = {"concrete": "WPU13110205", "steel": "WPU101"}

    async def _fetch(series_id: str):
        if series_id == "WPU13110205":
            raise RuntimeError("BLS rate limit")
        return {"latest_value": 99.0}

    with (
        patch("app.services.estimating.cost_database._BLS_SERIES_MAP", series_map),
        patch("app.services.estimating.cost_database.fetch_bls_ppi", AsyncMock(side_effect=_fetch)),
    ):
        result = await refresh_ppi_data()
    assert result == {"concrete": False, "steel": True}


# ---- cleanup_reconnect_tokens ------------------------------------------


async def test_cleanup_reconnect_tokens_invokes_manager():
    fake_manager = MagicMock()
    fake_manager.cleanup_expired_tokens = MagicMock()
    with patch("app.services.realtime.websocket_server.ws_manager", fake_manager):
        await cleanup_reconnect_tokens()
    fake_manager.cleanup_expired_tokens.assert_called_once()


async def test_cleanup_reconnect_tokens_swallows_manager_errors():
    fake_manager = MagicMock()
    fake_manager.cleanup_expired_tokens.side_effect = RuntimeError("bug")
    with patch("app.services.realtime.websocket_server.ws_manager", fake_manager):
        # Must not raise — schedulers run on a single worker, an
        # unhandled exception in a maintenance task would silently kill
        # the entire periodic schedule.
        await cleanup_reconnect_tokens()


# ---- compute_daily_evm_snapshots ---------------------------------------


async def test_compute_daily_evm_snapshots_no_active_projects():
    """No active projects → empty result, no errors."""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result_mock)
    out = await compute_daily_evm_snapshots(db)
    assert out == []


async def test_compute_daily_evm_snapshots_skips_existing_snapshot_today():
    """If a snapshot for today already exists, the function records that
    fact and skips re-computation rather than duplicating the row."""
    db = AsyncMock()

    project = MagicMock()
    project.id = "proj-1"

    projects_result = MagicMock()
    projects_result.scalars.return_value.all.return_value = [project]
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = "existing-snapshot-id"

    db.execute = AsyncMock(side_effect=[projects_result, dup_result])
    out = await compute_daily_evm_snapshots(db)
    assert len(out) == 1
    assert out[0]["success"] is True
    assert out[0]["skipped"] == "snapshot_already_exists"


async def test_compute_daily_evm_snapshots_records_missing_baseline():
    """Project has no prior EVM data → recorded as failure with explicit
    error message, batch continues."""
    db = AsyncMock()

    project = MagicMock()
    project.id = "proj-1"

    projects_result = MagicMock()
    projects_result.scalars.return_value.all.return_value = [project]
    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None
    latest_result = MagicMock()
    latest_result.scalar_one_or_none.return_value = None

    db.execute = AsyncMock(side_effect=[projects_result, dup_result, latest_result])
    out = await compute_daily_evm_snapshots(db)
    assert out[0]["success"] is False
    assert "No baseline EVM data" in out[0]["error"]


# ---- purge_old_audit_logs ----------------------------------------------


async def test_purge_old_audit_logs_returns_total_rowcount():
    """Function reports the sum of safety + general rows deleted."""
    db = AsyncMock()

    safety_result = MagicMock()
    safety_result.rowcount = 3
    general_result = MagicMock()
    general_result.rowcount = 17
    db.execute = AsyncMock(side_effect=[safety_result, general_result])

    deleted = await purge_old_audit_logs(db)
    assert deleted == 20


async def test_purge_old_audit_logs_uses_two_separate_statements():
    """Safety and general deletes use different cutoffs — verify the
    DELETE is issued twice rather than collapsed into one statement."""
    db = AsyncMock()
    res = MagicMock()
    res.rowcount = 0
    db.execute = AsyncMock(return_value=res)
    await purge_old_audit_logs(db)
    assert db.execute.await_count == 2


async def test_purge_old_audit_logs_returns_zero_on_failure():
    """Any DB error during purge is logged and the function returns 0
    instead of raising — schedulers shouldn't crash on transient errors."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("connection refused"))
    deleted = await purge_old_audit_logs(db)
    assert deleted == 0


@pytest.mark.parametrize("session_arg", [None])
async def test_purge_old_audit_logs_handles_no_session(session_arg, monkeypatch):
    """When no session is passed, the function tries to make its own
    via settings.DATABASE_URL. If creation fails, it should return 0
    (logged) rather than blow up the scheduler.

    We don't have a real DB here, so set DATABASE_URL to a guaranteed-
    unreachable target and assert the function returns 0 cleanly.
    """
    monkeypatch.setattr(
        "app.config.settings.DATABASE_URL",
        "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/nope",
    )

    # Even if the engine creation succeeds (it might lazy-connect), the
    # DELETE will fail. Either way the function must return 0.
    deleted = await purge_old_audit_logs(session_arg)
    assert deleted == 0
