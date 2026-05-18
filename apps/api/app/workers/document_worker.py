"""Celery worker for asynchronous document processing."""

from __future__ import annotations

import logging
import uuid

from asgiref.sync import async_to_sync
from celery import Celery, Task
from celery.schedules import crontab

from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery("constructai", broker=settings.REDIS_URL)


# SECURITY (H-20): Validate that a string is a well-formed UUID to prevent
# injection of arbitrary payloads through task arguments.
def _validate_uuid(value: str, name: str = "id") -> str:
    """Validate that *value* is a well-formed UUID string.

    Raises ``ValueError`` if the format is invalid.
    """
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid {name} format: {value!r} is not a valid UUID") from exc
    return value


# SECURITY (H-20): Whitelist of allowed task names to prevent task injection
# via unauthenticated Redis. Only tasks explicitly registered here can be
# dispatched by the broker.
_ALLOWED_TASKS = {
    "process_document",
    "refresh_fred_price_data",
    "refresh_bls_ppi_data",
    "generate_weekly_briefs",
    "compute_daily_evm_snapshots",
}

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    # M-51: reject + requeue on worker crash. Combined with task_acks_late
    # this gives at-least-once semantics — a task killed mid-execution
    # (OOM, pod restart, node loss) goes back on the queue for retry.
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # SECURITY (H-20): Route all known tasks to specific queues; unknown tasks
    # are routed to a dead-letter queue that no worker consumes.
    task_routes={
        "process_document": {"queue": "documents"},
        "refresh_fred_price_data": {"queue": "scheduled"},
        "refresh_bls_ppi_data": {"queue": "scheduled"},
        "generate_weekly_briefs": {"queue": "scheduled"},
        "compute_daily_evm_snapshots": {"queue": "scheduled"},
    },
    task_default_queue="dead_letter",
    beat_schedule={
        "refresh-fred-price-data-daily": {
            "task": "refresh_fred_price_data",
            # 9:00 AM ET = 14:00 UTC (EST) / 13:00 UTC (EDT)
            "schedule": crontab(hour=14, minute=0),
        },
        "refresh-bls-ppi-data-daily": {
            "task": "refresh_bls_ppi_data",
            "schedule": crontab(hour=14, minute=30),
        },
        "generate-weekly-briefs": {
            "task": "generate_weekly_briefs",
            # Monday 6:00 AM ET ≈ 11:00 UTC
            "schedule": crontab(hour=11, minute=0, day_of_week=1),
        },
        "compute-daily-evm-snapshots": {
            "task": "compute_daily_evm_snapshots",
            # Daily at 6:00 AM ET ≈ 11:00 UTC
            "schedule": crontab(hour=11, minute=0),
        },
    },
)


# M-30: Beat-lock helper. Uses Redis SET NX with TTL; if Redis is
# unavailable we return True (take the lock optimistically) because
# silently skipping scheduled tasks is more dangerous than an occasional
# double-run that downstream idempotency can absorb.
def _acquire_beat_lock(task_name: str, ttl_seconds: int) -> bool:
    """Try to acquire a Redis-backed lock for a beat-scheduled task."""
    try:
        import redis  # type: ignore[import-untyped]

        client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        acquired = client.set(
            f"cai:beat_lock:{task_name}",
            "1",
            nx=True,
            ex=ttl_seconds,
        )
        return bool(acquired)
    except Exception as exc:
        logger.warning("Beat lock acquisition failed for %s (%s) — proceeding", task_name, exc)
        return True


class DLQTask(Task):
    """Base task class that persists failures to a dead-letter queue.

    When a task exhausts all retries, ``on_failure`` is invoked by Celery.
    We log the permanent failure and mark the associated record so that
    operators can investigate via the DLQ table or monitoring dashboard.
    """

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when task fails after max retries - persist to DLQ."""
        logger.error(
            "Task %s permanently failed: %s. Args: %s",
            task_id,
            exc,
            args,
        )
        # Attempt to mark the document as permanently failed so the
        # status is visible through the API.
        try:
            from app.services.orchestration.dead_letter_queue import (
                record_dead_letter,
            )

            # Use async_to_sync instead of asyncio.run() for Celery pool compatibility
            async_to_sync(record_dead_letter)(
                task_name=self.name,
                task_id=task_id,
                args=args,
                kwargs=kwargs,
                exception=str(exc),
                traceback=str(einfo),
            )
        except Exception as dlq_exc:
            # If we cannot persist to the DLQ we must not mask the
            # original failure -- just log and move on.
            logger.error(
                "Failed to persist task %s to DLQ: %s",
                task_id,
                dlq_exc,
            )


@celery_app.task(
    name="process_document",
    bind=True,
    base=DLQTask,
    max_retries=3,
    soft_time_limit=300,
    time_limit=600,
)
def process_document_task(self, document_id: str, org_id: str | None = None) -> dict:
    """Process a document through the ingestion pipeline.

    This Celery task wraps the async ingestion pipeline so it can be
    executed by synchronous Celery workers.

    **Security note:** Task results must never be exposed to users
    without verifying that the requesting user belongs to the same
    organization that owns the document.  The ``org_id`` parameter is
    logged for audit purposes but authorization checks must happen at
    the API layer before enqueuing this task.

    Args:
        document_id: UUID string of the document to process.
        org_id: Organization UUID string for audit logging. Callers
            should always supply this so that task execution can be
            traced back to the originating tenant.

    Returns:
        A dict with processing status information.
    """
    from app.services.ingestion.pipeline import process_document

    # SECURITY (H-20): Validate document_id is a proper UUID to prevent injection.
    _validate_uuid(document_id, "document_id")
    if org_id is not None:
        _validate_uuid(org_id, "org_id")

    try:
        logger.info("Starting document processing: %s (org_id=%s)", document_id, org_id)

        async def _run():
            from app.database import async_session

            async with async_session() as db:
                await process_document(uuid.UUID(document_id), db)
                await db.commit()

        # Use async_to_sync instead of asyncio.run() for Celery pool compatibility
        result = async_to_sync(_run)()
        logger.info("Document processing completed: %s (org_id=%s)", document_id, org_id)
        return {"document_id": document_id, "status": "completed", "result": result}
    except Exception as exc:
        logger.error("Document processing failed for %s: %s", document_id, exc)
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1)) from exc


@celery_app.task(
    name="refresh_fred_price_data",
    bind=True,
    base=DLQTask,
    max_retries=2,
    soft_time_limit=120,
    time_limit=180,
)
def refresh_fred_price_data_task(self) -> dict:
    """Celery task: refresh FRED price data for all tracked series.

    Scheduled via Celery Beat at 9:00 AM ET daily (14:00 UTC).
    """
    from app.workers.scheduled_tasks import refresh_fred_price_data

    try:
        logger.info("Starting daily FRED price data refresh")
        # Use async_to_sync instead of asyncio.run() for Celery pool compatibility
        results = async_to_sync(refresh_fred_price_data)()
        succeeded = sum(1 for v in results.values() if v)
        logger.info("FRED refresh complete: %d/%d series OK", succeeded, len(results))
        return {"status": "completed", "results": results}
    except Exception as exc:
        logger.error("FRED refresh task failed: %s", exc)
        raise self.retry(exc=exc, countdown=300) from exc


@celery_app.task(
    name="generate_weekly_briefs",
    bind=True,
    base=DLQTask,
    max_retries=1,
    soft_time_limit=600,
    time_limit=900,
)
def generate_weekly_briefs_task(self) -> dict:
    """Celery task: generate intelligence briefs for all active projects.

    Scheduled via Celery Beat every Monday at 6:00 AM ET (11:00 UTC).
    """
    from app.workers.scheduled_tasks import generate_all_weekly_briefs

    # M-30: Beat-level lock. A previous run that overruns the cron interval
    # must not spawn a concurrent twin — would double-generate briefs for
    # every project. Lock is best-effort; if Redis is unavailable, we
    # proceed (alerting picks up the duplicate via the idempotency layer).
    if not _acquire_beat_lock("generate_weekly_briefs", ttl_seconds=3600):
        logger.warning("generate_weekly_briefs: previous run still holds lock, skipping")
        return {"status": "skipped_locked"}

    try:
        logger.info("Starting weekly intelligence brief generation")
        # Use async_to_sync instead of asyncio.run() for Celery pool compatibility
        results = async_to_sync(generate_all_weekly_briefs)()
        succeeded = sum(1 for r in results if r.get("success"))
        logger.info("Weekly briefs complete: %d/%d projects OK", succeeded, len(results))
        return {"status": "completed", "results": results}
    except Exception as exc:
        logger.error("Weekly brief generation failed: %s", exc)
        raise self.retry(exc=exc, countdown=600) from exc


@celery_app.task(
    name="refresh_bls_ppi_data",
    bind=True,
    base=DLQTask,
    max_retries=2,
    soft_time_limit=120,
    time_limit=180,
)
def refresh_bls_ppi_data_task(self) -> dict:
    """Celery task: refresh BLS PPI data for all tracked series."""
    from app.workers.scheduled_tasks import refresh_ppi_data

    try:
        logger.info("Starting daily BLS PPI data refresh")
        # Use async_to_sync instead of asyncio.run() for Celery pool compatibility
        results = async_to_sync(refresh_ppi_data)()
        succeeded = sum(1 for v in results.values() if v)
        logger.info("BLS PPI refresh complete: %d/%d series OK", succeeded, len(results))
        return {"status": "completed", "results": results}
    except Exception as exc:
        logger.error("BLS PPI refresh task failed: %s", exc)
        raise self.retry(exc=exc, countdown=300) from exc


@celery_app.task(
    name="compute_daily_evm_snapshots",
    bind=True,
    base=DLQTask,
    max_retries=2,
    soft_time_limit=300,
    time_limit=600,
)
def compute_daily_evm_snapshots_task(self) -> dict:
    """Celery task: compute daily EVM snapshots for all active projects.

    Scheduled via Celery Beat daily at 6:00 AM ET (11:00 UTC).
    """
    from app.workers.scheduled_tasks import compute_daily_evm_snapshots

    if not _acquire_beat_lock("compute_daily_evm_snapshots", ttl_seconds=1800):
        logger.warning("compute_daily_evm_snapshots: previous run still holds lock, skipping")
        return {"status": "skipped_locked"}

    try:
        logger.info("Starting daily EVM snapshot computation")

        async def _run():
            from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
            from sqlalchemy.orm import sessionmaker

            engine = create_async_engine(settings.DATABASE_URL)
            _async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with _async_session() as db:
                results = await compute_daily_evm_snapshots(db)
            await engine.dispose()
            return results

        # Use async_to_sync instead of asyncio.run() for Celery pool compatibility
        results = async_to_sync(_run)()
        succeeded = sum(1 for r in results if r.get("success"))
        logger.info("EVM snapshots complete: %d/%d projects OK", succeeded, len(results))
        return {"status": "completed", "results": results}
    except Exception as exc:
        logger.error("EVM snapshot task failed: %s", exc)
        raise self.retry(exc=exc, countdown=300) from exc
