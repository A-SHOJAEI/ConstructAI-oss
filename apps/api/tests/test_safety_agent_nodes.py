"""Tests for the safety agent LangGraph nodes.

Pin per-node behavior: context enrichment merges BIM/schedule/
weather into events, severity classification calls the documented
classifier with override pass-through, alert text uses canonical
'ppe_violation' / 'zone_breach' types, and the H-6 short-circuit
on ``passed=False`` skips downstream work.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.agents.safety_agent import (
    _continue_or_abort,
    build_safety_agent,
    classify_severity_node,
    enrich_context_node,
    generate_alerts_node,
    route_notifications_node,
)

# =========================================================================
# _continue_or_abort — short-circuit logic
# =========================================================================


def test_continue_or_abort_default_continues():
    """Default state (no 'passed' key) -> 'continue'. Pin: legacy
    callers without 'passed' must NOT be silently routed to END."""
    assert _continue_or_abort({}) == "continue"


def test_continue_or_abort_passed_true_continues():
    assert _continue_or_abort({"passed": True}) == "continue"


def test_continue_or_abort_passed_false_aborts():
    """[H-6] passed=False -> 'abort' (graph routes to END instead of
    carrying failure through downstream nodes)."""
    assert _continue_or_abort({"passed": False}) == "abort"


# =========================================================================
# enrich_context_node
# =========================================================================


@pytest.mark.asyncio
async def test_enrich_context_merges_phase_bim_weather():
    """BIM zone is keyed by camera_id; phase + weather added to
    every event."""
    state = {
        "detection_events": [
            {"camera_id": "cam-1", "violation": {"zone_type": "fall"}},
            {"camera_id": "cam-2", "violation": {"zone_type": "trench"}},
        ],
        "bim_zone_context": {
            "cam-1": {"zone_label": "Roof Edge", "elevation_ft": 80},
            "cam-2": {"zone_label": "Foundation"},
        },
        "schedule_phase": "structural",
        "weather_data": {"wind_mph": 25, "rain": False},
    }
    out = await enrich_context_node(state)
    assert out["status"] == "enriched"
    events = out["detection_events"]
    assert len(events) == 2
    # Cam-1 enriched with its specific BIM zone:
    assert events[0]["context"]["bim_zone"]["zone_label"] == "Roof Edge"
    # Phase + weather applied to every event:
    assert events[0]["context"]["construction_phase"] == "structural"
    assert events[0]["context"]["weather_conditions"]["wind_mph"] == 25
    assert events[1]["context"]["construction_phase"] == "structural"


@pytest.mark.asyncio
async def test_enrich_context_unknown_camera_gets_empty_bim_zone():
    """[edge case] event camera not in bim_zone_context -> empty
    dict (don't crash, don't fabricate zone data)."""
    state = {
        "detection_events": [{"camera_id": "cam-X"}],
        "bim_zone_context": {"cam-1": {"zone_label": "Known"}},
    }
    out = await enrich_context_node(state)
    assert out["detection_events"][0]["context"]["bim_zone"] == {}


@pytest.mark.asyncio
async def test_enrich_context_missing_phase_defaults_unknown():
    """Missing schedule_phase -> 'unknown' (not None, not crash)."""
    state = {"detection_events": [{"camera_id": "cam-1"}]}
    out = await enrich_context_node(state)
    assert out["detection_events"][0]["context"]["construction_phase"] == "unknown"


@pytest.mark.asyncio
async def test_enrich_context_empty_events_returns_empty():
    state = {"detection_events": []}
    out = await enrich_context_node(state)
    assert out["detection_events"] == []
    assert out["status"] == "enriched"


# =========================================================================
# classify_severity_node — short-circuit + classifier call
# =========================================================================


@pytest.mark.asyncio
async def test_classify_severity_short_circuits_on_failed_upstream():
    """[H-6] passed=False -> skip classifier, return
    'skipped_due_to_upstream_error'. Pin so a refactor doesn't run
    the classifier on bad context (which would corrupt the alert
    queue)."""
    out = await classify_severity_node({"passed": False, "detection_events": []})
    assert out["status"] == "skipped_due_to_upstream_error"


@pytest.mark.asyncio
async def test_classify_severity_passes_through_override():
    """severity_override from violation dict is forwarded to the
    classifier (caller can force severity for known scenarios)."""
    captured = []

    def fake_classify(*, severity_override, **_kwargs):
        captured.append(severity_override)
        return "P1_critical"

    state = {
        "detection_events": [
            {
                "violation": {"severity_override": "critical"},
                "detection": {"confidence": 0.9},
            }
        ]
    }
    with patch(
        "app.services.safety.severity_classifier.classify_severity",
        fake_classify,
    ):
        out = await classify_severity_node(state)

    assert captured == ["critical"]
    assert out["detection_events"][0]["severity"] == "P1_critical"
    assert out["status"] == "classified"


@pytest.mark.asyncio
async def test_classify_severity_uses_default_zone_violation_confidence():
    """[edge case] Missing violation/detection fields -> classifier
    receives defaults: zone_type='general', violation='other',
    confidence=0.5. Pin: ensures the classifier is never called
    with None args."""
    captured = {}

    def fake_classify(*, zone_type, violation_type, confidence, **_kwargs):
        captured.update(
            {
                "zone_type": zone_type,
                "violation_type": violation_type,
                "confidence": confidence,
            }
        )
        return "P5_info"

    state = {"detection_events": [{}]}
    with patch(
        "app.services.safety.severity_classifier.classify_severity",
        fake_classify,
    ):
        await classify_severity_node(state)

    assert captured["zone_type"] == "general"
    assert captured["violation_type"] == "other"
    assert captured["confidence"] == 0.5


@pytest.mark.asyncio
async def test_classify_severity_failure_marks_passed_false():
    """[H-6] Classifier crash -> passed=False so downstream nodes
    short-circuit. Errors captured in errors list."""

    def boom(**_kwargs):
        raise RuntimeError("classifier broken")

    state = {"detection_events": [{"violation": {}, "detection": {}}]}
    with patch(
        "app.services.safety.severity_classifier.classify_severity",
        boom,
    ):
        out = await classify_severity_node(state)

    assert out["passed"] is False
    assert "classifier broken" in out["errors"][0]


# =========================================================================
# generate_alerts_node — alert text + type classification
# =========================================================================


@pytest.mark.asyncio
async def test_generate_alerts_short_circuits_on_failed_upstream():
    out = await generate_alerts_node({"passed": False, "detection_events": []})
    assert out["status"] == "skipped_due_to_upstream_error"


@pytest.mark.asyncio
async def test_generate_alerts_ppe_violation_type():
    """[business invariant] Violations with 'missing_' prefix -> type
    'ppe_violation'. Pin: this categorization drives downstream
    notification routing."""
    state = {
        "detection_events": [
            {
                "camera_id": "cam-1",
                "violation": {"violation": "missing_hard_hat", "zone_type": "general"},
                "detection": {"class_name": "Person", "confidence": 0.92},
                "severity": "P2_high",
                "context": {"construction_phase": "structural"},
            }
        ]
    }
    out = await generate_alerts_node(state)
    alerts = out["alerts_generated"]
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "ppe_violation"
    assert alerts[0]["priority"] == "P2_high"
    assert alerts[0]["camera_id"] == "cam-1"
    # Description contains class name + violation + zone:
    assert "Person" in alerts[0]["description"]
    assert "missing_hard_hat" in alerts[0]["description"]
    assert "general" in alerts[0]["description"]
    # Phase included when present:
    assert "structural" in alerts[0]["description"]


@pytest.mark.asyncio
async def test_generate_alerts_zone_breach_type():
    """Non-PPE violations -> type 'zone_breach'."""
    state = {
        "detection_events": [
            {
                "camera_id": "cam-1",
                "violation": {"violation": "unauthorized_entry", "zone_type": "trench"},
                "detection": {"class_name": "Person"},
                "severity": "P1_critical",
                "context": {},
            }
        ]
    }
    out = await generate_alerts_node(state)
    assert out["alerts_generated"][0]["alert_type"] == "zone_breach"


@pytest.mark.asyncio
async def test_generate_alerts_default_severity_p5_info():
    """Missing severity -> default 'P5_info' (lowest priority)."""
    state = {
        "detection_events": [
            {
                "camera_id": "cam-1",
                "violation": {"violation": "x", "zone_type": "y"},
                "detection": {"class_name": "Object"},
                "context": {},
            }
        ]
    }
    out = await generate_alerts_node(state)
    assert out["alerts_generated"][0]["priority"] == "P5_info"


@pytest.mark.asyncio
async def test_generate_alerts_omits_phase_when_absent():
    """No construction_phase -> description doesn't include 'during'
    fragment (don't write 'during  phase' or 'during None phase')."""
    state = {
        "detection_events": [
            {
                "camera_id": "cam-1",
                "violation": {"violation": "x", "zone_type": "y"},
                "detection": {"class_name": "Person"},
                "severity": "P3_medium",
                "context": {},  # no construction_phase
            }
        ]
    }
    out = await generate_alerts_node(state)
    assert "during" not in out["alerts_generated"][0]["description"]


# =========================================================================
# route_notifications_node
# =========================================================================


@pytest.mark.asyncio
async def test_route_notifications_short_circuits_on_failed_upstream():
    out = await route_notifications_node({"passed": False, "alerts_generated": []})
    assert out["status"] == "skipped_due_to_upstream_error"


@pytest.mark.asyncio
async def test_route_notifications_dispatches_per_alert():
    """Each alert is sent to the notification router individually."""
    fake_route = AsyncMock(return_value=None)
    state = {
        "alerts_generated": [
            {"priority": "P1_critical", "alert_type": "ppe_violation"},
            {"priority": "P3_medium", "alert_type": "zone_breach"},
        ]
    }
    with patch(
        "app.services.safety.notification_router.route_notification",
        fake_route,
    ):
        out = await route_notifications_node(state)
    assert fake_route.call_count == 2
    assert out["status"] == "completed"


@pytest.mark.asyncio
async def test_route_notifications_failure_marks_passed_false():
    fake_route = AsyncMock(side_effect=RuntimeError("kafka down"))
    state = {"alerts_generated": [{"priority": "P1_critical"}]}
    with patch(
        "app.services.safety.notification_router.route_notification",
        fake_route,
    ):
        out = await route_notifications_node(state)
    assert out["passed"] is False
    assert out["status"] == "routing_failed"
    assert "kafka down" in out["errors"][0]


# =========================================================================
# Graph build
# =========================================================================


def test_build_safety_agent_returns_compiled_graph():
    graph = build_safety_agent()
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    assert {
        "enrich_context",
        "classify_severity",
        "generate_alerts",
        "route_notifications",
    } <= nodes
