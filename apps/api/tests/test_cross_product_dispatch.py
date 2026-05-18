"""Tests for the cross-product event dispatch infrastructure.

Pin the @handles decorator + dispatch_event behavior. The actual
domain handlers are DB-bound; these tests validate the dispatch
plumbing — handler registration, isolation between handlers, and
failure recovery.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.services.products import cross_product
from app.services.products.cross_product import (
    EVENT_HANDLERS,
    dispatch_event,
    handles,
)


@pytest.fixture(autouse=True)
def isolated_handlers():
    """Save and restore EVENT_HANDLERS so test pollution doesn't
    affect production handler registrations or cross-test state."""
    saved = {k: list(v) for k, v in EVENT_HANDLERS.items()}
    EVENT_HANDLERS.clear()
    yield
    EVENT_HANDLERS.clear()
    EVENT_HANDLERS.update(saved)


# =========================================================================
# @handles decorator
# =========================================================================


def test_handles_registers_function():
    @handles("test.event.x")
    async def my_handler(db, project_id, org_id, payload):
        return "ok"

    assert "test.event.x" in EVENT_HANDLERS
    assert my_handler in EVENT_HANDLERS["test.event.x"]


def test_handles_returns_function_unchanged():
    """The decorator must return the function so it remains callable
    directly — pin the no-wrap behavior."""

    async def my_handler(db, project_id, org_id, payload):
        return "ok"

    decorated = handles("test.event.y")(my_handler)
    assert decorated is my_handler


def test_handles_supports_multiple_handlers_per_event():
    """Multiple handlers can register for the same event."""

    @handles("test.fanout")
    async def handler_a(db, project_id, org_id, payload):
        return "a"

    @handles("test.fanout")
    async def handler_b(db, project_id, org_id, payload):
        return "b"

    assert len(EVENT_HANDLERS["test.fanout"]) == 2


# =========================================================================
# dispatch_event
# =========================================================================


@pytest.mark.asyncio
async def test_dispatch_no_handlers_returns_empty():
    """An event type with no registered handlers → empty result list."""
    db = AsyncMock()
    out = await dispatch_event(db, "test.unknown", uuid.uuid4(), uuid.uuid4(), {})
    assert out == []


@pytest.mark.asyncio
async def test_dispatch_calls_registered_handler():
    called_with = {}

    @handles("test.event")
    async def my_handler(db, project_id, org_id, payload):
        called_with["payload"] = payload
        return {"processed": True}

    db = AsyncMock()
    project_id = uuid.uuid4()
    org_id = uuid.uuid4()

    out = await dispatch_event(db, "test.event", project_id, org_id, {"key": "value"})
    assert called_with["payload"] == {"key": "value"}
    assert len(out) == 1
    assert out[0]["status"] == "ok"
    assert out[0]["result"] == {"processed": True}
    assert out[0]["handler"] == "my_handler"


@pytest.mark.asyncio
async def test_dispatch_isolates_handler_failures():
    """One handler raising must NOT prevent other handlers from
    running. Pin: failures are caught and logged per-handler."""

    @handles("test.fanout")
    async def good_handler(db, project_id, org_id, payload):
        return "ok"

    @handles("test.fanout")
    async def bad_handler(db, project_id, org_id, payload):
        raise RuntimeError("simulated failure")

    @handles("test.fanout")
    async def another_good(db, project_id, org_id, payload):
        return "also ok"

    db = AsyncMock()
    out = await dispatch_event(db, "test.fanout", uuid.uuid4(), uuid.uuid4(), {})

    # All 3 handlers ran and produced result entries:
    assert len(out) == 3
    statuses = [r["status"] for r in out]
    assert statuses.count("ok") == 2
    assert statuses.count("error") == 1


@pytest.mark.asyncio
async def test_dispatch_calls_handlers_in_registration_order():
    """Handlers fire in the order they were registered (helps with
    debugging cross-product chains)."""
    call_order = []

    @handles("test.ordered")
    async def first(db, project_id, org_id, payload):
        call_order.append("first")

    @handles("test.ordered")
    async def second(db, project_id, org_id, payload):
        call_order.append("second")

    @handles("test.ordered")
    async def third(db, project_id, org_id, payload):
        call_order.append("third")

    db = AsyncMock()
    await dispatch_event(db, "test.ordered", uuid.uuid4(), uuid.uuid4(), {})
    assert call_order == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_dispatch_passes_db_session():
    """The provided AsyncSession is forwarded to handlers — they need
    DB access to do meaningful work."""
    received_db = {}

    @handles("test.db_check")
    async def my_handler(db, project_id, org_id, payload):
        received_db["db"] = db

    fake_db = AsyncMock()
    await dispatch_event(fake_db, "test.db_check", uuid.uuid4(), uuid.uuid4(), {})
    assert received_db["db"] is fake_db


@pytest.mark.asyncio
async def test_dispatch_handler_result_in_output():
    """Whatever the handler returns is captured in the dispatch
    result entry."""

    @handles("test.result")
    async def returns_dict(db, project_id, org_id, payload):
        return {"status": "complete", "items": [1, 2, 3]}

    db = AsyncMock()
    out = await dispatch_event(db, "test.result", uuid.uuid4(), uuid.uuid4(), {})
    assert out[0]["result"] == {"status": "complete", "items": [1, 2, 3]}


# =========================================================================
# Production handler registration sanity check
# =========================================================================


def test_production_handlers_registered_on_import():
    """[regression] After importing cross_product, the documented
    canonical event types must have handlers registered. Pin so a
    refactor that drops the @handles decorator breaks the test."""
    # Save state, restore prod registrations, check, then teardown
    # restores to test state.
    EVENT_HANDLERS.clear()
    # Re-execute the decorators by reloading:
    import importlib

    importlib.reload(cross_product)

    # Documented integrations (canonical cross-product fan-out events):
    canonical_events = [
        "constructai.sitescribe.report_approved",
        "constructai.rfi.responded",
        "constructai.heat.incident_reported",
        "constructai.closeout.all_complete",
    ]
    for event_type in canonical_events:
        assert event_type in cross_product.EVENT_HANDLERS, (
            f"missing handler registration for {event_type}"
        )
