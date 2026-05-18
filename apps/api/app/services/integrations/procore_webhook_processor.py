"""Procore webhook event processor.

Consumes events from the "procore.webhooks" Kafka topic and routes
them to the appropriate sync + downstream trigger functions.

Event routing by resource_name:
  - Documents create/update → sync document + trigger RAG re-indexing
  - RFIs create → sync RFI + trigger RFI Resolution Agent
  - Budget Line Items update → sync budget + trigger EVM recalculation
  - Change Orders create/update → sync change order + trigger CO Analyzer
  - Daily Logs create/update → sync daily log
  - Submittals / Observations → logged, no sync target yet

Dead letter queue: "procore.webhooks.dlq" after 3 failed retries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds: 2, 4, 8

WEBHOOK_TOPIC = "procore.webhooks"
DLQ_TOPIC = "procore.webhooks.dlq"

# Consumer group for webhook processing
CONSUMER_GROUP = "constructai-procore-webhooks"


# ---------------------------------------------------------------------------
# Event handler: routes webhook events to sync functions
# ---------------------------------------------------------------------------


async def handle_webhook_event(event: dict[str, Any]) -> None:
    """Route a Procore webhook CloudEvent to the appropriate handler.

    The event is a CloudEvents envelope with the Procore webhook
    payload in event["data"]["payload"].
    """
    data = event.get("data", {})
    resource_name = data.get("resource_name", "unknown")
    event_type = data.get("event_type", "unknown")
    resource_id = data.get("resource_id")
    project_id = data.get("project_id")
    company_id = data.get("company_id")

    logger.info(
        "Processing webhook: %s.%s (resource=%s, project=%s)",
        resource_name,
        event_type,
        resource_id,
        project_id,
    )

    handler = _RESOURCE_HANDLERS.get(resource_name)
    if handler is None:
        logger.info(
            "No handler for resource %s; event acknowledged",
            resource_name,
        )
        return

    await handler(
        event_type=event_type,
        resource_id=resource_id,
        project_id=project_id,
        company_id=company_id,
        payload=data.get("payload", {}),
    )


# ---------------------------------------------------------------------------
# Per-resource handlers
# ---------------------------------------------------------------------------


async def _handle_document_event(
    event_type: str,
    resource_id: int | None,
    project_id: int | None,
    company_id: int | None,
    payload: dict,
) -> None:
    """Handle document create/update events.

    Syncs the document from Procore and triggers RAG re-indexing
    via Kafka event.
    """
    if event_type not in ("create", "update"):
        logger.debug("Ignoring document event_type=%s", event_type)
        return

    if not project_id or not company_id:
        logger.warning("Document event missing project_id or company_id")
        return

    db_project = await _get_project_by_procore_id(project_id, procore_company_id=company_id)
    if not db_project:
        logger.warning("No local project for procore_project_id=%s", project_id)
        return

    from app.services.integrations.procore_sync import sync_documents

    async with _get_db_session() as db:
        from app.services.integrations.procore_api import ProcoreAPI

        api = ProcoreAPI(org_id=db_project["org_id"], db=db)
        result = await sync_documents(
            api,
            db,
            db_project["id"],
            project_id,
            company_id,
        )
        await db.commit()

    logger.info(
        "Document webhook sync: %d synced, %d errors",
        result["synced"],
        len(result["errors"]),
    )

    # Trigger RAG re-indexing via Kafka
    await _publish_downstream(
        event_type="constructai.document.reindex_requested",
        data={
            "project_id": str(db_project["id"]),
            "procore_project_id": project_id,
            "trigger": "procore_webhook",
            "resource_id": resource_id,
        },
    )


async def _handle_rfi_event(
    event_type: str,
    resource_id: int | None,
    project_id: int | None,
    company_id: int | None,
    payload: dict,
) -> None:
    """Handle RFI create events.

    Syncs RFIs and triggers the RFI Resolution Agent (Phase 5).
    """
    if event_type not in ("create", "update"):
        logger.debug("Ignoring RFI event_type=%s", event_type)
        return

    if not project_id or not company_id:
        logger.warning("RFI event missing project_id or company_id")
        return

    db_project = await _get_project_by_procore_id(project_id, procore_company_id=company_id)
    if not db_project:
        logger.warning("No local project for procore_project_id=%s", project_id)
        return

    from app.services.integrations.procore_sync import sync_rfis

    async with _get_db_session() as db:
        from app.services.integrations.procore_api import ProcoreAPI

        api = ProcoreAPI(org_id=db_project["org_id"], db=db)
        result = await sync_rfis(
            api,
            db,
            db_project["id"],
            project_id,
            company_id,
        )
        await db.commit()

    logger.info(
        "RFI webhook sync: %d synced, %d errors",
        result["synced"],
        len(result["errors"]),
    )

    # Trigger RFI Resolution Agent (Phase 5)
    if event_type == "create":
        await _publish_downstream(
            event_type="constructai.procore.rfi.resolution_requested",
            data={
                "project_id": str(db_project["id"]),
                "procore_project_id": project_id,
                "resource_id": resource_id,
                "trigger": "procore_webhook",
            },
        )


async def _handle_budget_event(
    event_type: str,
    resource_id: int | None,
    project_id: int | None,
    company_id: int | None,
    payload: dict,
) -> None:
    """Handle budget line item update events.

    Syncs budget and triggers EVM recalculation.
    """
    if not project_id or not company_id:
        logger.warning("Budget event missing project_id or company_id")
        return

    db_project = await _get_project_by_procore_id(project_id, procore_company_id=company_id)
    if not db_project:
        logger.warning("No local project for procore_project_id=%s", project_id)
        return

    from app.services.integrations.procore_sync import sync_budget

    async with _get_db_session() as db:
        from app.services.integrations.procore_api import ProcoreAPI

        api = ProcoreAPI(org_id=db_project["org_id"], db=db)
        result = await sync_budget(
            api,
            db,
            db_project["id"],
            project_id,
            company_id,
        )
        await db.commit()

    logger.info("Budget webhook sync: %d items synced", result["synced"])

    # Trigger EVM recalculation
    await _publish_downstream(
        event_type="constructai.controls.evm_recalculation_requested",
        data={
            "project_id": str(db_project["id"]),
            "trigger": "procore_webhook",
        },
    )


async def _handle_change_order_event(
    event_type: str,
    resource_id: int | None,
    project_id: int | None,
    company_id: int | None,
    payload: dict,
) -> None:
    """Handle change order create/update events.

    Syncs change orders and triggers the Change Order Analyzer.
    """
    if event_type not in ("create", "update"):
        logger.debug("Ignoring change order event_type=%s", event_type)
        return

    if not project_id or not company_id:
        logger.warning("Change order event missing project_id or company_id")
        return

    db_project = await _get_project_by_procore_id(project_id, procore_company_id=company_id)
    if not db_project:
        logger.warning("No local project for procore_project_id=%s", project_id)
        return

    from app.services.integrations.procore_sync import sync_change_orders

    async with _get_db_session() as db:
        from app.services.integrations.procore_api import ProcoreAPI

        api = ProcoreAPI(org_id=db_project["org_id"], db=db)
        result = await sync_change_orders(
            api,
            db,
            db_project["id"],
            project_id,
            company_id,
        )
        await db.commit()

    logger.info(
        "Change order webhook sync: %d synced, %d errors",
        result["synced"],
        len(result["errors"]),
    )

    # Trigger Change Order Analyzer
    await _publish_downstream(
        event_type="constructai.procore.change_order.analysis_requested",
        data={
            "project_id": str(db_project["id"]),
            "resource_id": resource_id,
            "trigger": "procore_webhook",
        },
    )


async def _handle_daily_log_event(
    event_type: str,
    resource_id: int | None,
    project_id: int | None,
    company_id: int | None,
    payload: dict,
) -> None:
    """Handle daily log create/update events.

    Syncs daily logs (no downstream trigger needed).
    """
    if event_type not in ("create", "update"):
        logger.debug("Ignoring daily log event_type=%s", event_type)
        return

    if not project_id or not company_id:
        logger.warning("Daily log event missing project_id or company_id")
        return

    db_project = await _get_project_by_procore_id(project_id, procore_company_id=company_id)
    if not db_project:
        logger.warning("No local project for procore_project_id=%s", project_id)
        return

    from app.services.integrations.procore_sync import sync_daily_logs

    async with _get_db_session() as db:
        from app.services.integrations.procore_api import ProcoreAPI

        api = ProcoreAPI(org_id=db_project["org_id"], db=db)
        result = await sync_daily_logs(
            api,
            db,
            db_project["id"],
            project_id,
            company_id,
        )
        await db.commit()

    logger.info(
        "Daily log webhook sync: %d synced, %d errors",
        result["synced"],
        len(result["errors"]),
    )


async def _handle_submittal_event(
    event_type: str,
    resource_id: int | None,
    project_id: int | None,
    company_id: int | None,
    payload: dict,
) -> None:
    """Handle submittal events (logged only — no sync target yet)."""
    logger.info(
        "Submittal event received: %s (resource=%s, project=%s) — no sync target",
        event_type,
        resource_id,
        project_id,
    )


async def _handle_observation_event(
    event_type: str,
    resource_id: int | None,
    project_id: int | None,
    company_id: int | None,
    payload: dict,
) -> None:
    """Handle observation events (logged only — no sync target yet)."""
    logger.info(
        "Observation event received: %s (resource=%s, project=%s) — no sync target",
        event_type,
        resource_id,
        project_id,
    )


# Resource name → handler mapping
_RESOURCE_HANDLERS: dict[str, Any] = {
    "Documents": _handle_document_event,
    "RFIs": _handle_rfi_event,
    "Budget Line Items": _handle_budget_event,
    "Change Orders": _handle_change_order_event,
    "Daily Logs": _handle_daily_log_event,
    "Submittals": _handle_submittal_event,
    "Observations": _handle_observation_event,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_project_by_procore_id(
    procore_project_id: int,
    procore_company_id: int | None = None,
) -> dict | None:
    """Look up the local project by its Procore project ID.

    When *procore_company_id* is provided, the lookup is scoped to the
    organization that owns that Procore company connection, providing
    tenant isolation.  If no company_id is available, the query falls
    back to matching only on procore_id (less restrictive).

    Returns a dict with id and org_id, or None if not found.
    """
    from sqlalchemy import select

    from app.models.procore_connection import ProcoreConnection
    from app.models.project import Project

    async with _get_db_session() as db:
        if procore_company_id is not None:
            # Tenant-isolated lookup: join through ProcoreConnection to
            # ensure the project belongs to the org that owns this company.
            result = await db.execute(
                select(Project.id, Project.org_id).where(
                    Project.procore_id == procore_project_id,
                    Project.org_id.in_(
                        select(ProcoreConnection.organization_id).where(
                            ProcoreConnection.procore_company_id == str(procore_company_id),
                        )
                    ),
                )
            )
        else:
            # NOTE: Without company_id we cannot enforce tenant isolation.
            # This path should be rare — webhook events normally include
            # company_id.  If this becomes an issue, consider rejecting
            # events that lack a company_id.
            result = await db.execute(
                select(Project.id, Project.org_id).where(
                    Project.procore_id == procore_project_id,
                )
            )
        row = result.first()
        if row is None:
            return None
        return {"id": row[0], "org_id": row[1]}


def _get_db_session():
    """Get an async database session context manager."""
    from app.database import async_session

    return async_session()


async def _publish_downstream(event_type: str, data: dict) -> None:
    """Publish a downstream event to Kafka."""
    try:
        from app.api.v1.procore_webhooks import _get_kafka_producer

        producer = _get_kafka_producer()
        if producer:
            await producer.publish(
                event_type=event_type,
                data=data,
                source="/procore-webhook-processor",
            )
    except Exception as exc:
        logger.error("Failed to publish downstream event %s: %s", event_type, exc)


# ---------------------------------------------------------------------------
# Downstream event handlers
# ---------------------------------------------------------------------------

_DOWNSTREAM_HANDLERS: dict[str, Any] = {}


async def handle_downstream_event(event_type: str, data: dict) -> None:
    """Route downstream internal events to their handlers."""
    handler = _DOWNSTREAM_HANDLERS.get(event_type)
    if handler is None:
        logger.debug("No downstream handler for %s", event_type)
        return
    await handler(data)


async def _handle_rfi_resolution_requested(data: dict) -> None:
    """Trigger RFI Resolution Agent Stage 1 on new RFI from Procore.

    Looks up the newly synced RFI by procore resource_id and runs
    the unnecessary check.
    """
    project_id_str = data.get("project_id")
    resource_id = data.get("resource_id")

    if not project_id_str or not resource_id:
        logger.warning("RFI resolution trigger missing project_id or resource_id")
        return

    try:
        from sqlalchemy import select

        from app.models.communication import RFI

        async with _get_db_session() as db:
            # Find the RFI that was just synced from Procore
            result = await db.execute(
                select(RFI).where(
                    RFI.project_id == uuid.UUID(project_id_str),
                    RFI.procore_id == int(resource_id),
                )
            )
            rfi = result.scalar_one_or_none()
            if not rfi:
                logger.info(
                    "No RFI found for procore_id=%s in project %s",
                    resource_id,
                    project_id_str,
                )
                return

            from app.services.agents.rfi_resolution_agent import (
                run_rfi_unnecessary_check,
            )

            check_result = await run_rfi_unnecessary_check(
                rfi_id=rfi.id,
                project_id=rfi.project_id,
                subject=rfi.subject,
                question=rfi.question,
            )

            # Log the check result
            from app.models.communication import RfiResolutionLog

            log = RfiResolutionLog(
                rfi_id=rfi.id,
                project_id=rfi.project_id,
                stage_reached=1,
                was_unnecessary=check_result.get("is_unnecessary", False),
                unnecessary_source=check_result.get("unnecessary_source"),
                unnecessary_reason=check_result.get("unnecessary_reason"),
                similar_rfi_count=len(check_result.get("similar_rfis", [])),
            )
            db.add(log)
            await db.commit()

            logger.info(
                "RFI resolution check for %s: unnecessary=%s",
                rfi.rfi_number,
                check_result.get("is_unnecessary", False),
            )

    except Exception as exc:
        logger.error("RFI resolution trigger failed: %s", exc)


_DOWNSTREAM_HANDLERS["constructai.procore.rfi.resolution_requested"] = (
    _handle_rfi_resolution_requested
)


# ---------------------------------------------------------------------------
# Kafka consumer with retry + DLQ
# ---------------------------------------------------------------------------


class ProcoreWebhookConsumer:
    """Kafka consumer for procore.webhooks topic with retry and DLQ.

    Processes webhook events with up to MAX_RETRIES attempts per message.
    Failed messages are forwarded to the dead letter queue topic after
    all retries are exhausted.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:29092",
        group_id: str = CONSUMER_GROUP,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._group_id = group_id
        self._consumer: Any = None
        self._dlq_producer: Any = None
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Initialize consumer and begin processing."""
        try:
            from confluent_kafka import Consumer, Producer
        except ImportError:
            logger.warning("confluent-kafka not installed; webhook consumer disabled")
            return

        try:
            self._consumer = Consumer(
                {
                    "bootstrap.servers": self._bootstrap_servers,
                    "group.id": self._group_id,
                    "auto.offset.reset": "earliest",
                    "enable.auto.commit": False,
                    "max.poll.interval.ms": 300_000,
                    "session.timeout.ms": 45_000,
                }
            )
            self._consumer.subscribe([WEBHOOK_TOPIC])

            self._dlq_producer = Producer(
                {
                    "bootstrap.servers": self._bootstrap_servers,
                    "client.id": "constructai-procore-dlq-producer",
                }
            )

            self._running = True
            self._task = asyncio.create_task(self._poll_loop())
            logger.info(
                "ProcoreWebhookConsumer started (group=%s, topic=%s)",
                self._group_id,
                WEBHOOK_TOPIC,
            )
        except Exception:
            logger.warning(
                "Failed to start Procore webhook consumer",
                exc_info=True,
            )

    async def stop(self) -> None:
        """Stop the consumer gracefully."""
        self._running = False
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except TimeoutError:
                self._task.cancel()
            self._task = None

        if self._consumer:
            try:
                self._consumer.close()
            except Exception:
                logger.warning("Downstream event handler cleanup failed", exc_info=True)
            self._consumer = None

        if self._dlq_producer:
            try:
                self._dlq_producer.flush(timeout=5.0)
            except Exception:
                logger.warning("Downstream event handler cleanup failed", exc_info=True)
            self._dlq_producer = None

        logger.info("ProcoreWebhookConsumer stopped.")

    async def _poll_loop(self) -> None:
        """Poll and process messages with retry logic."""
        while self._running:
            msg = self._consumer.poll(timeout=0.0)
            if msg is None:
                await asyncio.sleep(0.1)
                continue

            error = msg.error()
            if error:
                logger.error("Consumer poll error: %s", error)
                await asyncio.sleep(0.1)
                continue

            await self._process_with_retry(msg)

    async def _process_with_retry(self, msg: Any) -> None:
        """Process a message with up to MAX_RETRIES attempts."""
        raw = msg.value()
        attempt = 0

        while attempt < MAX_RETRIES:
            try:
                event = json.loads(raw)
                if event.get("specversion") != "1.0":
                    raise ValueError("Invalid CloudEvent")
                await handle_webhook_event(event)
                self._commit(msg)
                return
            except Exception as exc:
                attempt += 1
                if attempt >= MAX_RETRIES:
                    logger.error(
                        "Webhook processing failed after %d retries: %s",
                        MAX_RETRIES,
                        exc,
                    )
                    self._forward_to_dlq(raw, str(exc))
                    self._commit(msg)
                    return

                # M-27: add up to 1s jitter on top of the exponential base.
                # Without jitter, if many consumers hit the same Procore
                # outage at once they all retry at t=2s, t=4s, t=8s in
                # lockstep and hammer Procore when it comes back —
                # classic thundering herd. Random offset decorrelates.
                import random

                backoff = RETRY_BACKOFF_BASE**attempt + random.uniform(0, 1)
                logger.warning(
                    "Webhook processing failed (attempt %d/%d): %s; retrying in %.2fs",
                    attempt,
                    MAX_RETRIES,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)

    def _commit(self, msg: Any) -> None:
        """Commit offset for a processed message."""
        try:
            self._consumer.commit(message=msg, asynchronous=True)
        except Exception:
            logger.warning("Offset commit failed", exc_info=True)

    def _forward_to_dlq(self, raw: bytes, error_reason: str) -> None:
        """Forward a failed message to the dead letter queue."""
        if not self._dlq_producer:
            logger.error("DLQ producer unavailable; message dropped")
            return

        headers = [
            ("dlq.original.topic", WEBHOOK_TOPIC.encode("utf-8")),
            ("dlq.error.reason", error_reason.encode("utf-8")),
            ("dlq.timestamp", str(time.time()).encode("utf-8")),
        ]
        try:
            self._dlq_producer.produce(
                topic=DLQ_TOPIC,
                value=raw,
                headers=headers,
            )
            self._dlq_producer.poll(0)
            logger.warning(
                "Message forwarded to DLQ %s — reason: %s",
                DLQ_TOPIC,
                error_reason,
            )
        except Exception:
            logger.error("Failed to forward message to DLQ", exc_info=True)
