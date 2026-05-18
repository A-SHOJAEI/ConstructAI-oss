"""Tests for document_worker security helpers and the DLQ base task.

The Celery tasks themselves (process_document_task, etc.) need a live
broker + DB and aren't tested here. Their non-task helpers are pure
functions or mockable, and they're the parts that contain the real
security logic (UUID validation, dead-letter persistence, beat-lock).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.workers.document_worker import (
    _ALLOWED_TASKS,
    DLQTask,
    _acquire_beat_lock,
    _validate_uuid,
)

# ---- _validate_uuid -----------------------------------------------------


def test_validate_uuid_accepts_well_formed_uuid():
    val = "12345678-1234-1234-1234-123456789abc"
    assert _validate_uuid(val) == val


def test_validate_uuid_accepts_uppercase_hex():
    val = "12345678-1234-1234-1234-123456789ABC"
    assert _validate_uuid(val) == val


def test_validate_uuid_rejects_garbage():
    with pytest.raises(ValueError, match="not a valid UUID"):
        _validate_uuid("not-a-uuid")


def test_validate_uuid_rejects_sql_injection_payload():
    """Confirms H-20: an attacker can't smuggle a payload through the
    Celery task arg by crafting a string that looks vaguely UUID-ish."""
    with pytest.raises(ValueError):
        _validate_uuid("'; DROP TABLE documents; --")


def test_validate_uuid_rejects_empty_string():
    with pytest.raises(ValueError):
        _validate_uuid("")


def test_validate_uuid_includes_field_name_in_error():
    """When validation fails, the error mentions which arg was bad —
    helps ops triage a bad task payload faster."""
    with pytest.raises(ValueError, match="org_id"):
        _validate_uuid("nope", name="org_id")


def test_validate_uuid_handles_non_string_input():
    """Passing a non-string raises ValueError (re-wrapped from
    AttributeError/TypeError). Callers shouldn't have to special-case
    the bad-type path themselves."""
    with pytest.raises(ValueError):
        _validate_uuid(12345)  # type: ignore[arg-type]


# ---- _ALLOWED_TASKS whitelist ------------------------------------------


def test_allowed_tasks_includes_process_document():
    assert "process_document" in _ALLOWED_TASKS


def test_allowed_tasks_includes_all_scheduled_jobs():
    """Each beat-scheduled task name must also appear in the
    whitelist — otherwise a scheduled run gets dead-lettered."""
    expected_scheduled = {
        "refresh_fred_price_data",
        "refresh_bls_ppi_data",
        "generate_weekly_briefs",
        "compute_daily_evm_snapshots",
    }
    assert expected_scheduled.issubset(_ALLOWED_TASKS)


# ---- _acquire_beat_lock ------------------------------------------------


def test_acquire_beat_lock_succeeds_when_redis_set_returns_truthy():
    fake_redis = MagicMock()
    fake_redis.set = MagicMock(return_value=True)  # NX succeeded
    with patch("redis.Redis.from_url", return_value=fake_redis):
        assert _acquire_beat_lock("task-x", 60) is True
    fake_redis.set.assert_called_once()
    args, kwargs = fake_redis.set.call_args
    assert args[0] == "cai:beat_lock:task-x"
    assert kwargs == {"nx": True, "ex": 60}


def test_acquire_beat_lock_returns_false_when_lock_already_held():
    fake_redis = MagicMock()
    fake_redis.set = MagicMock(return_value=None)  # NX failed → another worker has it
    with patch("redis.Redis.from_url", return_value=fake_redis):
        assert _acquire_beat_lock("task-x", 60) is False


def test_acquire_beat_lock_returns_true_on_redis_error():
    """M-30: silent skip is more dangerous than the occasional
    double-run; the helper takes the lock optimistically when Redis
    fails."""
    with patch("redis.Redis.from_url", side_effect=ConnectionError("redis down")):
        assert _acquire_beat_lock("task-x", 60) is True


# ---- DLQTask.on_failure ------------------------------------------------


def test_dlq_task_on_failure_records_to_dead_letter():
    """The DLQ base records the exhausted-retry failure via
    record_dead_letter so operators can audit it. The task's *own*
    Celery state machine (retries already exhausted) brought us here.

    NB: ``DLQTask.name`` is normally set by Celery when the decorator
    runs; we set it directly via ``__dict__`` to bypass the descriptor
    that pyright complains about.
    """
    task = DLQTask()
    task.__dict__["name"] = "process_document"
    recorded: list[dict] = []

    async def _record(**kwargs):
        recorded.append(kwargs)
        return {"status": "dead_letter"}

    with patch(
        "app.services.orchestration.dead_letter_queue.record_dead_letter",
        new=_record,
    ):
        task.on_failure(
            exc=RuntimeError("permanent failure"),
            task_id="task-uuid",
            args=("doc-id",),
            kwargs={"org_id": "org-id"},
            einfo="traceback object",
        )

    assert len(recorded) == 1
    rec = recorded[0]
    assert rec["task_name"] == "process_document"
    assert rec["task_id"] == "task-uuid"
    assert rec["args"] == ("doc-id",)
    assert rec["kwargs"] == {"org_id": "org-id"}
    assert rec["exception"] == "permanent failure"


def test_dlq_task_on_failure_swallows_dlq_persist_errors():
    """If recording to the DLQ itself fails, we must not mask the
    original task failure with a new exception — log and return."""
    task = DLQTask()
    task.__dict__["name"] = "process_document"

    async def _broken(**kwargs):
        raise RuntimeError("DLQ table unreachable")

    with patch(
        "app.services.orchestration.dead_letter_queue.record_dead_letter",
        new=_broken,
    ):
        # Must not raise:
        task.on_failure(
            exc=RuntimeError("original"),
            task_id="t1",
            args=(),
            kwargs={},
            einfo="trace",
        )
