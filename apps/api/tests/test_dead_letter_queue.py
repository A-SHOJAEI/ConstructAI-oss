"""Tests for the DeadLetterQueue retry/dead-letter store.

Pure in-memory bookkeeping — no DB or Redis. Covers the retry-budget
exhaustion path, size caps, redaction, and the new ``record_dead_letter``
helper that the Celery worker depends on (the worker was importing a
function that didn't exist; tests pin the contract so this can't regress
silently again).
"""

from __future__ import annotations

import pytest

from app.services.orchestration.dead_letter_queue import (
    BACKOFF_BASE,
    DeadLetterQueue,
    get_dead_letter_queue,
    record_dead_letter,
)


@pytest.fixture
def dlq() -> DeadLetterQueue:
    """Fresh DLQ instance; clear() called for hermeticity."""
    q = DeadLetterQueue(max_retries=3)
    q.clear()
    return q


# ---- add_failed_event / retry budget ------------------------------------


async def test_add_failed_event_creates_first_retry_entry(dlq: DeadLetterQueue):
    result = await dlq.add_failed_event({"type": "x", "id": "1"}, "boom")
    assert result["status"] == "retry_queued"
    assert result["retry_count"] == 1
    assert len(dlq._retry_queue) == 1
    assert dlq._retry_queue[0]["last_error"] == "boom"


async def test_add_failed_event_increments_existing_entry(dlq: DeadLetterQueue):
    event = {"type": "x", "id": "1"}
    await dlq.add_failed_event(event, "first")
    result = await dlq.add_failed_event(event, "second")
    assert result["retry_count"] == 2
    assert dlq._retry_queue[0]["last_error"] == "second"
    # First-error must remain pinned to the original failure for triage.
    assert dlq._retry_queue[0]["first_error"] == "first"


async def test_retry_budget_exhausted_moves_to_dlq(dlq: DeadLetterQueue):
    """The Nth call that pushes retry_count to max_retries triggers the
    move to dead-letter — for max_retries=3 that's the third call."""
    event = {"type": "x", "id": "1"}
    # First two failures stay in the retry queue:
    await dlq.add_failed_event(event, "err1")
    await dlq.add_failed_event(event, "err2")
    assert dlq._retry_queue and dlq._dead_letters == []
    # Third failure exhausts the budget and moves to DLQ:
    final = await dlq.add_failed_event(event, "final")
    assert final["status"] == "dead_letter"
    assert dlq._retry_queue == []
    assert len(dlq._dead_letters) == 1


async def test_backoff_grows_exponentially(dlq: DeadLetterQueue):
    """Backoff schedule documented in the docstring: 1s, 5s, 25s.
    Translates to BACKOFF_BASE ** retry_count / BACKOFF_BASE."""
    assert dlq._get_backoff(1) == BACKOFF_BASE / BACKOFF_BASE  # 1.0
    assert dlq._get_backoff(2) == BACKOFF_BASE
    assert dlq._get_backoff(3) == BACKOFF_BASE * BACKOFF_BASE


# ---- caps --------------------------------------------------------------


async def test_retry_queue_cap_drops_oldest():
    from app.services.orchestration import dead_letter_queue as dlq_mod

    q = DeadLetterQueue(max_retries=3)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(dlq_mod, "_MAX_RETRY_QUEUE_SIZE", 3)
        for i in range(3):
            await q.add_failed_event({"id": i}, "err")
        # 4th distinct event triggers drop:
        await q.add_failed_event({"id": 99}, "err")
    # Oldest dropped, newest retained:
    ids = [e["event"]["id"] for e in q._retry_queue]
    assert 0 not in ids
    assert 99 in ids


async def test_dlq_cap_drops_oldest():
    from app.services.orchestration import dead_letter_queue as dlq_mod

    q = DeadLetterQueue(max_retries=1)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(dlq_mod, "_MAX_DEAD_LETTERS_SIZE", 2)
        for i in range(3):
            event = {"id": i}
            await q.add_failed_event(event, "err")
            await q.add_failed_event(event, "err")  # exceeds max_retries=1
    assert len(q._dead_letters) == 2


# ---- get_retry_queue / get_dead_letters / redaction --------------------


async def test_redacted_event_keeps_only_safe_fields(dlq: DeadLetterQueue):
    """Sensitive fields like passwords or tokens in the event must not
    appear in the redacted snapshot returned to operators."""
    event = {
        "event_type": "doc_uploaded",
        "project_id": "p1",
        "timestamp": "2026-04-26T01:00:00Z",
        "password": "should-not-appear",
        "auth_token": "secret-jwt",
        "internal_state": {"k": "v"},
    }
    await dlq.add_failed_event(event, "boom")
    snapshot = (await dlq.get_retry_queue())[0]
    redacted_event = snapshot["event"]
    assert redacted_event == {
        "event_type": "doc_uploaded",
        "project_id": "p1",
        "timestamp": "2026-04-26T01:00:00Z",
        "error_type": None,
    }
    assert "password" not in redacted_event
    assert "auth_token" not in redacted_event
    assert snapshot["first_error"] == "[redacted]"
    assert snapshot["last_error"] == "[redacted]"


async def test_redact_handles_alternate_event_type_keys(dlq: DeadLetterQueue):
    """Some upstream producers use ``type`` and ``ce-projectid`` (CloudEvents)
    instead of ``event_type``/``project_id``."""
    await dlq.add_failed_event(
        {"type": "alert", "ce-projectid": "p2", "timestamp": "t"},
        "boom",
    )
    snapshot = (await dlq.get_retry_queue())[0]
    assert snapshot["event"]["event_type"] == "alert"
    assert snapshot["event"]["project_id"] == "p2"


# ---- reprocess_dead_letter ---------------------------------------------


async def test_reprocess_dead_letter_moves_back_to_retry(dlq: DeadLetterQueue):
    event = {"id": "1"}
    for _ in range(dlq.max_retries):
        await dlq.add_failed_event(event, "err")
    assert len(dlq._dead_letters) == 1
    assert dlq._retry_queue == []

    out = await dlq.reprocess_dead_letter(0)
    assert out is not None
    assert out["retry_count"] == 0
    assert out["status"] == "retry_queued"
    assert dlq._dead_letters == []
    assert len(dlq._retry_queue) == 1


async def test_reprocess_dead_letter_invalid_index_returns_none(dlq: DeadLetterQueue):
    assert await dlq.reprocess_dead_letter(0) is None
    assert await dlq.reprocess_dead_letter(-1) is None


async def test_dlq_depth_reflects_dead_letters(dlq: DeadLetterQueue):
    assert await dlq.get_dlq_depth() == 0
    for i in range(2):
        event = {"id": i}
        for _ in range(dlq.max_retries):
            await dlq.add_failed_event(event, "err")
    assert await dlq.get_dlq_depth() == 2


# ---- record_dead_letter (Celery on_failure entry point) ----------------


async def test_record_dead_letter_persists_failed_celery_task():
    """This is the function the Celery DLQTask.on_failure imports —
    its absence used to silently swallow permanent task failures."""
    dlq = get_dead_letter_queue()
    dlq.clear()
    entry = await record_dead_letter(
        task_name="process_document",
        task_id="task-1",
        args=("doc-1",),
        kwargs={"org_id": "org-1"},
        exception="hard failure",
        traceback="...stack...",
    )
    assert entry["status"] == "dead_letter"
    assert entry["last_error"] == "hard failure"

    snapshot = await dlq.get_dead_letters()
    assert len(snapshot) == 1
    # Redaction strips the traceback / error fields when surfacing to ops:
    assert snapshot[0]["last_error"] == "[redacted]"
    # Event metadata still includes task identifiers via redaction map:
    assert snapshot[0]["event"]["event_type"] == "celery_task_failure"


async def test_record_dead_letter_returns_singleton_dlq():
    dlq_1 = get_dead_letter_queue()
    dlq_2 = get_dead_letter_queue()
    assert dlq_1 is dlq_2
