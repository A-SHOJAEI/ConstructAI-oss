"""Dead letter queue for failed event processing."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE = 5  # seconds
_MAX_RETRY_QUEUE_SIZE = 5000
_MAX_DEAD_LETTERS_SIZE = 1000


class DeadLetterQueue:
    """Failed event processing with retry and alerting.

    - Max 3 retries with exponential backoff (1s, 5s, 25s)
    - After max retries, move to DLQ
    - Alert on DLQ depth > 10
    - Retry queue capped at 5000, dead letters capped at 1000
    """

    def __init__(self, max_retries: int = MAX_RETRIES):
        self.max_retries = max_retries
        self._retry_queue: list[dict] = []
        self._dead_letters: list[dict] = []
        self._alert_threshold = 10

    async def add_failed_event(
        self,
        event: dict,
        error: str,
    ) -> dict:
        """Add a failed event to the retry queue.

        Returns the retry entry with status.
        """
        # Find existing retry entry
        existing = self._find_retry(event)

        if existing:
            existing["retry_count"] += 1
            existing["last_error"] = error
            existing["last_attempt"] = time.time()

            if existing["retry_count"] >= self.max_retries:
                return await self._move_to_dlq(existing)

            return {
                "status": "retry_queued",
                "retry_count": existing["retry_count"],
                "next_retry_delay": self._get_backoff(
                    existing["retry_count"],
                ),
            }

        entry = {
            "event": event,
            "retry_count": 1,
            "first_error": error,
            "last_error": error,
            "first_attempt": time.time(),
            "last_attempt": time.time(),
        }
        # Cap retry queue size by dropping oldest entries
        if len(self._retry_queue) >= _MAX_RETRY_QUEUE_SIZE:
            dropped = len(self._retry_queue) - _MAX_RETRY_QUEUE_SIZE + 1
            self._retry_queue = self._retry_queue[dropped:]
            logger.warning(
                "Retry queue exceeded cap (%d), dropped %d oldest entries",
                _MAX_RETRY_QUEUE_SIZE,
                dropped,
            )
        self._retry_queue.append(entry)

        return {
            "status": "retry_queued",
            "retry_count": 1,
            "next_retry_delay": self._get_backoff(1),
        }

    async def _move_to_dlq(self, entry: dict) -> dict:
        """Move entry to dead letter queue."""
        self._retry_queue.remove(entry)
        entry["status"] = "dead_letter"
        entry["moved_to_dlq_at"] = time.time()
        # Cap dead letters size by dropping oldest entries
        if len(self._dead_letters) >= _MAX_DEAD_LETTERS_SIZE:
            dropped = len(self._dead_letters) - _MAX_DEAD_LETTERS_SIZE + 1
            self._dead_letters = self._dead_letters[dropped:]
            logger.warning(
                "Dead letter queue exceeded cap (%d), dropped %d oldest entries",
                _MAX_DEAD_LETTERS_SIZE,
                dropped,
            )
        self._dead_letters.append(entry)

        logger.error(
            "Event moved to DLQ after %d retries: %s",
            entry["retry_count"],
            entry["last_error"],
        )

        # Check alert threshold
        if len(self._dead_letters) > self._alert_threshold:
            logger.critical(
                "DLQ depth %d exceeds threshold %d",
                len(self._dead_letters),
                self._alert_threshold,
            )

        return {
            "status": "dead_letter",
            "retry_count": entry["retry_count"],
        }

    async def get_retry_queue(self) -> list[dict]:
        """Get all events pending retry, with sensitive fields redacted."""
        return [self._redact_entry(entry) for entry in self._retry_queue]

    async def get_dead_letters(self) -> list[dict]:
        """Get all dead letter entries, with sensitive fields redacted."""
        return [self._redact_entry(entry) for entry in self._dead_letters]

    @staticmethod
    def _redact_entry(entry: dict) -> dict:
        """Return a copy of a queue entry with sensitive event payload fields removed.

        Only safe operational fields are preserved: event_type, project_id,
        timestamp, and error_type.  All other payload data (which may contain
        PII, credentials, or internal state) is stripped.
        """
        redacted = dict(entry)
        event = entry.get("event")
        if isinstance(event, dict):
            redacted["event"] = {
                "event_type": event.get("type", event.get("event_type")),
                "project_id": event.get("ce-projectid", event.get("project_id")),
                "timestamp": event.get("timestamp"),
                "error_type": event.get("error_type"),
            }
        # Redact full traceback which may contain sensitive context
        if "first_error" in redacted:
            redacted["first_error"] = "[redacted]"
        if "last_error" in redacted:
            redacted["last_error"] = "[redacted]"
        return redacted

    async def get_dlq_depth(self) -> int:
        """Get current DLQ depth."""
        return len(self._dead_letters)

    async def reprocess_dead_letter(
        self,
        index: int,
    ) -> dict | None:
        """Move a dead letter back to retry queue."""
        if 0 <= index < len(self._dead_letters):
            entry = self._dead_letters.pop(index)
            entry["retry_count"] = 0
            entry["status"] = "retry_queued"
            self._retry_queue.append(entry)
            return entry
        return None

    def _find_retry(self, event: dict) -> dict | None:
        """Find existing retry entry for an event."""
        for entry in self._retry_queue:
            if entry["event"] == event:
                return entry
        return None

    def _get_backoff(self, retry_count: int) -> float:
        """Exponential backoff: 1s, 5s, 25s."""
        return BACKOFF_BASE**retry_count / BACKOFF_BASE

    def clear(self):
        """Clear all queues (for testing)."""
        self._retry_queue.clear()
        self._dead_letters.clear()


# ---------------------------------------------------------------------------
# Module-level singleton + Celery-task entry point
# ---------------------------------------------------------------------------
#
# ``DLQTask.on_failure`` in ``app/workers/document_worker.py`` imports
# ``record_dead_letter`` to persist a failed Celery task once retries are
# exhausted. Without a function at this name, the on_failure path silently
# logs and drops the failure — the dead-letter table never receives the
# entry and operators have no breadcrumb to investigate from. Provide a
# thin async helper that adapts the Celery args to the DLQ's storage
# format and delegates to the singleton.

_default_dlq: DeadLetterQueue | None = None


def get_dead_letter_queue() -> DeadLetterQueue:
    """Return the process-wide DLQ singleton."""
    global _default_dlq
    if _default_dlq is None:
        _default_dlq = DeadLetterQueue()
    return _default_dlq


async def record_dead_letter(
    *,
    task_name: str,
    task_id: str,
    args: tuple | list,
    kwargs: dict,
    exception: str,
    traceback: str = "",
) -> dict:
    """Persist a permanently-failed Celery task to the DLQ.

    Called by ``DLQTask.on_failure`` after all retries are exhausted.
    Returns the DLQ entry dict that was added.
    """
    dlq = get_dead_letter_queue()
    event = {
        "event_type": "celery_task_failure",
        "task_name": task_name,
        "task_id": task_id,
        "args": list(args),
        "kwargs": kwargs,
        "traceback": traceback,
    }
    # Skip the retry stage and go straight to dead-lettered: the
    # caller has already exhausted Celery's retry budget by the time
    # ``on_failure`` runs.
    entry = {
        "event": event,
        "retry_count": dlq.max_retries,
        "first_error": exception,
        "last_error": exception,
        "first_attempt": time.time(),
        "last_attempt": time.time(),
    }
    if len(dlq._dead_letters) >= _MAX_DEAD_LETTERS_SIZE:
        dropped = len(dlq._dead_letters) - _MAX_DEAD_LETTERS_SIZE + 1
        dlq._dead_letters = dlq._dead_letters[dropped:]
        logger.warning(
            "Dead letter queue exceeded cap (%d), dropped %d oldest entries",
            _MAX_DEAD_LETTERS_SIZE,
            dropped,
        )
    entry["status"] = "dead_letter"
    entry["moved_to_dlq_at"] = time.time()
    dlq._dead_letters.append(entry)
    logger.error(
        "Celery task %s permanently failed (id=%s): %s",
        task_name,
        task_id,
        exception,
    )
    if len(dlq._dead_letters) > dlq._alert_threshold:
        logger.critical(
            "DLQ depth %d exceeds threshold %d",
            len(dlq._dead_letters),
            dlq._alert_threshold,
        )
    return entry
