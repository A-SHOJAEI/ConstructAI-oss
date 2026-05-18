"""CloudEvents routing with priority and optional Kafka publishing."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Kafka producer singleton
# ---------------------------------------------------------------------------
_kafka_producer = None  # type: ignore[assignment]
_kafka_init_attempted = False


def _get_kafka_producer():
    """Return the shared KafkaEventProducer, or *None* if unavailable."""
    global _kafka_producer, _kafka_init_attempted
    if _kafka_init_attempted:
        return _kafka_producer
    _kafka_init_attempted = True
    try:
        from app.config import settings
        from app.services.messaging.kafka_producer import (
            KafkaEventProducer,
        )

        _kafka_producer = KafkaEventProducer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        )
        if not _kafka_producer.available:
            _kafka_producer = None
    except Exception:
        logger.debug(
            "Kafka producer not available -- events will only be routed in-process.",
            exc_info=True,
        )
        _kafka_producer = None
    return _kafka_producer


# Event type to workflow mapping
EVENT_WORKFLOW_MAP = {
    "constructai.project.created": "new_project_onboarding",
    "constructai.change_order.submitted": ("change_order_processing"),
    "constructai.safety.incident_detected": ("safety_incident_response"),
    "constructai.safety.critical_alert": ("safety_incident_response"),
}

# Priority defaults by event type
EVENT_PRIORITY_DEFAULTS = {
    "constructai.safety.incident_detected": 1,
    "constructai.safety.critical_alert": 1,
    "constructai.change_order.submitted": 2,
    "constructai.project.created": 3,
    "constructai.controls.variance_alert": 3,
    "constructai.quality.defect_found": 3,
    "constructai.communication.report_generated": 4,
    "constructai.progress.milestone_reached": 4,
}


_MAX_EVENT_LOG_SIZE = 10_000


class EventRouter:
    """CloudEvents-based event routing.

    CloudEvents extensions:
    - ce-projectid: project UUID
    - ce-agentsource: originating agent name
    - ce-priority: P1-P5 (1=highest)
    - ce-correlationid: workflow correlation UUID

    Priority routing:
    - P1: interrupt current workflows (safety critical)
    - P2-P3: queue for next available slot
    - P4-P5: batch process
    """

    def __init__(self):
        self._event_log: list[dict] = []
        self._active_workflows: dict[str, dict] = {}

    async def route_event(self, event: dict) -> dict:
        """Route CloudEvent to appropriate workflow.

        Returns routing result with workflow_execution_id.
        """
        event_type = event.get("type", "")
        project_id = event.get(
            "ce-projectid",
            event.get("project_id", ""),
        )
        org_id = event.get(
            "ce-orgid",
            event.get("org_id", ""),
        )
        priority = event.get(
            "ce-priority",
            EVENT_PRIORITY_DEFAULTS.get(event_type, 3),
        )
        correlation_id = event.get(
            "ce-correlationid",
            str(uuid.uuid4()),
        )

        workflow_type = EVENT_WORKFLOW_MAP.get(event_type)

        # Cap event log to prevent unbounded memory growth
        if len(self._event_log) >= _MAX_EVENT_LOG_SIZE:
            self._event_log = self._event_log[-(_MAX_EVENT_LOG_SIZE - 1) :]

        self._event_log.append(
            {
                "event_type": event_type,
                "org_id": org_id,
                "project_id": project_id,
                "priority": priority,
                "correlation_id": correlation_id,
                "workflow_type": workflow_type,
            }
        )

        if not workflow_type:
            logger.info(
                "No workflow mapped for event: %s",
                event_type,
            )
            await self._publish_to_kafka(event, event_type)
            return {
                "routed_to": "none",
                "priority": priority,
                "workflow_execution_id": None,
            }

        # P1 events get priority handling
        if priority == 1:
            await self._handle_priority_interrupt(
                event,
                workflow_type,
            )

        execution_id = str(uuid.uuid4())
        self._active_workflows[execution_id] = {
            "workflow_type": workflow_type,
            "org_id": org_id,
            "project_id": project_id,
            "priority": priority,
            "correlation_id": correlation_id,
            "status": "queued",
        }

        logger.info(
            "Routed %s to %s (P%d) -> %s",
            event_type,
            workflow_type,
            priority,
            execution_id,
        )

        # Publish to Kafka when available (best-effort, never blocks
        # the routing result).
        await self._publish_to_kafka(event, event_type)

        return {
            "routed_to": workflow_type,
            "priority": priority,
            "workflow_execution_id": execution_id,
        }

    async def _handle_priority_interrupt(
        self,
        event: dict,
        workflow_type: str,
    ):
        """P1 events interrupt current workflows."""
        logger.warning(
            "P1 interrupt: %s -> %s",
            event.get("type"),
            workflow_type,
        )

    async def _publish_to_kafka(
        self,
        event: dict,
        event_type: str,
    ) -> None:
        """Best-effort publish of the event to Kafka.

        Failures are logged but never propagated -- Kafka is an
        optional enhancement, not a hard dependency.
        """
        producer = _get_kafka_producer()
        if producer is None:
            return
        try:
            source = event.get(
                "ce-agentsource",
                event.get("source", "/event-router"),
            )
            await producer.publish(
                event_type=event_type,
                data=event.get("data", event),
                source=source,
            )
        except Exception:
            logger.warning(
                "Failed to publish %s to Kafka (non-fatal).",
                event_type,
                exc_info=True,
            )

    def complete_workflow(self, execution_id: str) -> None:
        """Mark a workflow as completed and remove from active tracking."""
        self._active_workflows.pop(execution_id, None)

    def _cleanup_completed_workflows(self) -> None:
        """Remove workflows with terminal statuses from active tracking."""
        terminal = {
            eid
            for eid, wf in self._active_workflows.items()
            if wf.get("status") in ("completed", "failed", "cancelled")
        }
        for eid in terminal:
            del self._active_workflows[eid]
        if terminal:
            logger.debug("Cleaned up %d completed workflows", len(terminal))

    def get_event_log(self, org_id: str | None = None) -> list[dict]:
        """Get routed events, optionally scoped to an organization.

        Args:
            org_id: When provided, only events whose ``org_id``
                matches are returned. When ``None``, all events are
                returned (admin use only).
        """
        if org_id is None:
            return list(self._event_log)
        needle = str(org_id)
        return [e for e in self._event_log if str(e.get("org_id") or "") == needle]

    def get_active_workflows(self, org_id: str | None = None) -> dict[str, dict]:
        """Get active workflows, optionally scoped to an organization.

        Args:
            org_id: When provided, only workflows whose ``org_id``
                matches are returned. When ``None``, all workflows are
                returned (admin use only).
        """
        self._cleanup_completed_workflows()
        if org_id is None:
            return dict(self._active_workflows)
        needle = str(org_id)
        return {
            eid: wf
            for eid, wf in self._active_workflows.items()
            if str(wf.get("org_id") or "") == needle
        }

    def clear(self):
        """Clear state (for testing)."""
        self._event_log.clear()
        self._active_workflows.clear()
