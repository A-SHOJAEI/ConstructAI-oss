"""LangGraph Safety Agent for context-enriched alert generation."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TypedDict

from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)


class SafetyAgentState(TypedDict, total=False):
    project_id: str
    detection_events: list[dict]
    bim_zone_context: dict | None
    schedule_phase: str | None
    weather_data: dict | None
    alerts_generated: list[dict]
    daily_summary: str | None
    status: str
    errors: list[str]
    # H-6: When any node sets ``passed=False`` the conditional edges route
    # straight to END instead of silently carrying the failure through
    # every downstream node. ``total=False`` so legacy callers that don't
    # set this still type-check.
    passed: bool


def _continue_or_abort(state: SafetyAgentState) -> str:
    """Conditional-edge router: short-circuit the graph when a node failed."""
    return "abort" if not state.get("passed", True) else "continue"


async def enrich_context_node(state: SafetyAgentState) -> dict:
    """Enrich detection events with BIM and schedule context."""
    try:
        bim_context = state.get("bim_zone_context") or {}
        schedule_phase = state.get("schedule_phase") or "unknown"
        weather = state.get("weather_data") or {}

        enriched_events = []
        for event in state.get("detection_events", []):
            enriched = {**event}
            enriched["context"] = {
                "construction_phase": schedule_phase,
                "bim_zone": bim_context.get(event.get("camera_id", ""), {}),
                "weather_conditions": weather,
            }
            enriched_events.append(enriched)

        return {
            "detection_events": enriched_events,
            "status": "enriched",
        }
    except Exception as exc:
        logger.error("Context enrichment failed: %s", exc)
        return {"errors": [str(exc)], "status": "enrichment_failed", "passed": False}


async def classify_severity_node(state: SafetyAgentState) -> dict:
    """Classify severity of each detection event."""
    if not state.get("passed", True):
        return {"status": "skipped_due_to_upstream_error"}
    try:
        from app.services.safety.severity_classifier import classify_severity

        classified = []
        for event in state.get("detection_events", []):
            violation = event.get("violation", {})
            detection = event.get("detection", {})
            severity = classify_severity(
                zone_type=violation.get("zone_type", "general"),
                violation_type=violation.get("violation", "other"),
                confidence=detection.get("confidence", 0.5),
                severity_override=violation.get("severity_override"),
            )
            classified.append({**event, "severity": severity})

        return {"detection_events": classified, "status": "classified"}
    except Exception as exc:
        logger.error("Severity classification failed: %s", exc)
        return {"errors": [str(exc)], "passed": False}


async def generate_alerts_node(state: SafetyAgentState) -> dict:
    """Generate human-readable alert descriptions."""
    if not state.get("passed", True):
        return {"status": "skipped_due_to_upstream_error"}
    try:
        alerts = []
        for event in state.get("detection_events", []):
            violation = event.get("violation", {})
            detection = event.get("detection", {})
            severity = event.get("severity", "P5_info")
            context = event.get("context", {})

            description = (
                f"{detection.get('class_name', 'Object')} detected - "
                f"{violation.get('violation', 'unknown violation')} "
                f"in {violation.get('zone_type', 'unknown')} zone"
            )
            if context.get("construction_phase"):
                description += f" during {context['construction_phase']} phase"

            alerts.append(
                {
                    "camera_id": event.get("camera_id"),
                    "priority": severity,
                    "alert_type": (
                        "ppe_violation"
                        if "missing_" in violation.get("violation", "")
                        else "zone_breach"
                    ),
                    "description": description,
                    "detection": detection,
                    "violation": violation,
                    "context": context,
                }
            )

        return {"alerts_generated": alerts, "status": "alerts_generated"}
    except Exception as exc:
        logger.error("Alert generation failed: %s", exc)
        return {"errors": [str(exc)], "passed": False}


async def route_notifications_node(state: SafetyAgentState) -> dict:
    """Route alerts to appropriate notification channels."""
    if not state.get("passed", True):
        return {"status": "skipped_due_to_upstream_error"}
    try:
        from app.services.safety.notification_router import route_notification

        for alert in state.get("alerts_generated", []):
            await route_notification(alert)

        return {"status": "completed"}
    except Exception as exc:
        logger.error("Notification routing failed: %s", exc)
        return {"errors": [str(exc)], "status": "routing_failed", "passed": False}


def build_safety_agent(checkpointer=None):
    """Build the LangGraph safety agent.

    H-6: Uses conditional edges between every stage so a failed node routes
    directly to END; downstream nodes no longer need to carry defensive
    ``if not passed`` guards that could be forgotten as the graph grows.
    M-19: ``recursion_limit`` caps runaway loops.
    """
    workflow = StateGraph(SafetyAgentState)
    workflow.add_node("enrich_context", enrich_context_node)
    workflow.add_node("classify_severity", classify_severity_node)
    workflow.add_node("generate_alerts", generate_alerts_node)
    workflow.add_node("route_notifications", route_notifications_node)
    workflow.set_entry_point("enrich_context")
    for src, dst in (
        ("enrich_context", "classify_severity"),
        ("classify_severity", "generate_alerts"),
        ("generate_alerts", "route_notifications"),
    ):
        workflow.add_conditional_edges(
            src,
            _continue_or_abort,
            {"continue": dst, "abort": END},
        )
    workflow.add_edge("route_notifications", END)
    return workflow.compile(checkpointer=checkpointer)


async def run_safety_agent(
    project_id: str,
    detection_events: list[dict],
    bim_zone_context: dict | None = None,
    schedule_phase: str | None = None,
    weather_data: dict | None = None,
    *,
    correlation_id: str | None = None,
) -> dict:
    """Run the safety agent pipeline."""
    from app.services.agents._config import make_agent_config
    from app.services.agents.checkpointer import get_checkpointer

    checkpointer = get_checkpointer()
    graph = build_safety_agent(checkpointer=checkpointer)
    thread_id = f"safety_{uuid.uuid4().hex}"
    config = make_agent_config(thread_id, correlation_id=correlation_id)
    logger.info(
        "safety_agent_start",
        extra={
            "thread_id": thread_id,
            "correlation_id": correlation_id,
            "project_id": project_id,
        },
    )
    initial_state: SafetyAgentState = {
        "project_id": project_id,
        "detection_events": detection_events,
        "bim_zone_context": bim_zone_context,
        "schedule_phase": schedule_phase,
        "weather_data": weather_data,
        "alerts_generated": [],
        "daily_summary": None,
        "status": "processing",
        "errors": [],
    }
    try:
        result = await asyncio.wait_for(
            graph.ainvoke(initial_state, config=config),
            timeout=300.0,  # 5 minute timeout
        )
        return result
    except TimeoutError:
        logger.error("Agent timed out after 300s", extra={"agent": "safety"})
        return {**initial_state, "status": "timeout", "errors": ["Agent execution timed out"]}
    except Exception as exc:
        logger.error("Safety agent failed: %s", exc)
        return {**initial_state, "status": "failed", "errors": [str(exc)]}
