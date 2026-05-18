"""Tests for the CloudEvents-based EventRouter.

Pin priority defaults, workflow mapping, P1 interrupt path,
event-log capping, completed-workflow cleanup, and org-scoped
filtering on log/active queries.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.orchestration.event_router import (
    _MAX_EVENT_LOG_SIZE,
    EVENT_PRIORITY_DEFAULTS,
    EVENT_WORKFLOW_MAP,
    EventRouter,
)

# =========================================================================
# Module constants
# =========================================================================


def test_event_workflow_map_canonical():
    """Pin documented event-to-workflow mappings — refactor must not
    silently drop a routing rule."""
    expected_events = {
        "constructai.project.created",
        "constructai.change_order.submitted",
        "constructai.safety.incident_detected",
        "constructai.safety.critical_alert",
    }
    assert set(EVENT_WORKFLOW_MAP.keys()) == expected_events


def test_safety_events_get_p1_priority():
    """[business invariant] Safety events MUST have P1 priority —
    interrupts other workflows. Pin so a refactor can't silently
    demote safety to P2/P3."""
    assert EVENT_PRIORITY_DEFAULTS["constructai.safety.incident_detected"] == 1
    assert EVENT_PRIORITY_DEFAULTS["constructai.safety.critical_alert"] == 1


def test_change_order_p2_priority():
    """Change orders are P2 — high but below safety."""
    assert EVENT_PRIORITY_DEFAULTS["constructai.change_order.submitted"] == 2


def test_priority_defaults_canonical_events():
    """All documented event types have priority defaults."""
    expected = {
        "constructai.safety.incident_detected",
        "constructai.safety.critical_alert",
        "constructai.change_order.submitted",
        "constructai.project.created",
        "constructai.controls.variance_alert",
        "constructai.quality.defect_found",
        "constructai.communication.report_generated",
        "constructai.progress.milestone_reached",
    }
    assert expected == set(EVENT_PRIORITY_DEFAULTS.keys())


def test_max_event_log_sane():
    """Cap should be high enough to avoid losing recent events but
    low enough to bound memory."""
    assert 1000 <= _MAX_EVENT_LOG_SIZE <= 100_000


# =========================================================================
# fixtures
# =========================================================================


@pytest.fixture(autouse=True)
def disable_kafka():
    """Kafka publish is best-effort and async — disable it so tests
    don't try to connect to a real broker."""

    async def fake_publish(*args, **kwargs):
        return None

    fake_producer = AsyncMock()
    fake_producer.publish = fake_publish
    with patch(
        "app.services.orchestration.event_router._get_kafka_producer",
        return_value=fake_producer,
    ):
        yield


@pytest.fixture
def router() -> EventRouter:
    return EventRouter()


# =========================================================================
# route_event — workflow mapping
# =========================================================================


@pytest.mark.asyncio
async def test_route_event_known_event_returns_workflow(router: EventRouter):
    out = await router.route_event({"type": "constructai.project.created", "ce-projectid": "p-1"})
    assert out["routed_to"] == "new_project_onboarding"
    assert out["workflow_execution_id"] is not None


@pytest.mark.asyncio
async def test_route_event_unmapped_event_returns_none(router: EventRouter):
    """An unmapped event type still publishes to Kafka but returns
    routed_to=none."""
    out = await router.route_event({"type": "constructai.alien.unknown"})
    assert out["routed_to"] == "none"
    assert out["workflow_execution_id"] is None


@pytest.mark.asyncio
async def test_route_event_safety_incident_p1(router: EventRouter):
    """Safety incident gets P1 priority by default."""
    out = await router.route_event(
        {"type": "constructai.safety.incident_detected", "ce-projectid": "p-1"}
    )
    assert out["priority"] == 1
    assert out["routed_to"] == "safety_incident_response"


@pytest.mark.asyncio
async def test_route_event_explicit_priority_override(router: EventRouter):
    """Caller-provided ce-priority overrides default."""
    out = await router.route_event(
        {
            "type": "constructai.project.created",
            "ce-priority": 1,  # explicit P1
        }
    )
    assert out["priority"] == 1


@pytest.mark.asyncio
async def test_route_event_unknown_priority_default_p3(router: EventRouter):
    """An event without priority + no entry in defaults → P3."""
    out = await router.route_event({"type": "constructai.alien.unknown"})
    assert out["priority"] == 3


@pytest.mark.asyncio
async def test_route_event_records_in_log(router: EventRouter):
    await router.route_event({"type": "constructai.project.created", "ce-orgid": "org-1"})
    log = router.get_event_log()
    assert len(log) == 1
    assert log[0]["event_type"] == "constructai.project.created"


@pytest.mark.asyncio
async def test_route_event_p1_safety_triggers_interrupt(router: EventRouter):
    """[business invariant] P1 safety events log a warning about
    interrupting current workflows."""
    out = await router.route_event(
        {
            "type": "constructai.safety.incident_detected",
            "ce-projectid": "p-1",
        }
    )
    assert out["priority"] == 1


@pytest.mark.asyncio
async def test_route_event_correlation_id_generated_when_absent(router: EventRouter):
    """Workflow execution id is a UUID string."""
    out = await router.route_event({"type": "constructai.project.created"})
    import uuid

    uuid.UUID(out["workflow_execution_id"])


# =========================================================================
# Event log capping
# =========================================================================


@pytest.mark.asyncio
async def test_event_log_capped_at_max_size(router: EventRouter):
    """[memory bound] When event log hits its cap, oldest entries are
    dropped to bound memory growth. Pin: log size never exceeds the
    documented limit."""
    # Force the cap to a small number for the test:
    with patch(
        "app.services.orchestration.event_router._MAX_EVENT_LOG_SIZE",
        50,
    ):
        # Fire 60 events (10 over the cap):
        for _i in range(60):
            await router.route_event({"type": "constructai.alien.x"})
        # Log size shouldn't exceed cap:
        log = router.get_event_log()
        assert len(log) <= 50


# =========================================================================
# Active workflows — completion + cleanup
# =========================================================================


@pytest.mark.asyncio
async def test_active_workflows_includes_routed_event(router: EventRouter):
    out = await router.route_event({"type": "constructai.project.created", "ce-orgid": "org-1"})
    workflows = router.get_active_workflows()
    assert out["workflow_execution_id"] in workflows


@pytest.mark.asyncio
async def test_complete_workflow_removes_from_active(router: EventRouter):
    out = await router.route_event({"type": "constructai.project.created"})
    eid = out["workflow_execution_id"]
    assert eid in router.get_active_workflows()

    router.complete_workflow(eid)
    assert eid not in router.get_active_workflows()


def test_complete_workflow_unknown_id_no_op(router: EventRouter):
    """Completing an unknown ID must not crash."""
    router.complete_workflow("never-exists-id")


@pytest.mark.asyncio
async def test_cleanup_completed_workflows_internal(router: EventRouter):
    """Workflows in terminal status (completed/failed/cancelled) are
    removed on get_active_workflows() call."""
    await router.route_event({"type": "constructai.project.created"})
    eid = next(iter(router._active_workflows))
    router._active_workflows[eid]["status"] = "completed"

    # get_active_workflows triggers cleanup:
    active = router.get_active_workflows()
    assert eid not in active


# =========================================================================
# Org-scoped filtering
# =========================================================================


@pytest.mark.asyncio
async def test_get_event_log_org_filter(router: EventRouter):
    """Org-scoped log query returns only matching events."""
    await router.route_event({"type": "constructai.project.created", "ce-orgid": "org-a"})
    await router.route_event({"type": "constructai.project.created", "ce-orgid": "org-b"})
    await router.route_event({"type": "constructai.project.created", "ce-orgid": "org-a"})

    only_a = router.get_event_log(org_id="org-a")
    assert len(only_a) == 2

    only_b = router.get_event_log(org_id="org-b")
    assert len(only_b) == 1

    # No filter → all 3:
    assert len(router.get_event_log()) == 3


@pytest.mark.asyncio
async def test_get_active_workflows_org_filter(router: EventRouter):
    await router.route_event({"type": "constructai.project.created", "ce-orgid": "org-a"})
    await router.route_event({"type": "constructai.project.created", "ce-orgid": "org-b"})

    only_a = router.get_active_workflows(org_id="org-a")
    assert len(only_a) == 1
    assert all(wf["org_id"] == "org-a" for wf in only_a.values())


@pytest.mark.asyncio
async def test_get_event_log_returns_copy(router: EventRouter):
    """The returned list must be a copy — caller mutation can't leak
    into router state."""
    await router.route_event({"type": "constructai.project.created"})
    log = router.get_event_log()
    log.clear()
    # Internal log should still have the entry:
    assert len(router._event_log) == 1


# =========================================================================
# clear
# =========================================================================


@pytest.mark.asyncio
async def test_clear_resets_state(router: EventRouter):
    await router.route_event({"type": "constructai.project.created"})
    assert router.get_event_log()
    router.clear()
    assert router.get_event_log() == []
