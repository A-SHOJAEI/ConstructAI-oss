"""LangGraph checkpointer factory with PostgreSQL persistence.

Provides a ``get_checkpointer()`` factory that returns a PostgreSQL-backed
checkpointer for production and a lightweight in-memory saver for tests.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)


def get_checkpointer():
    """Return a LangGraph checkpointer instance.

    In test mode (``settings.TESTING is True``), an in-memory
    ``MemorySaver`` is returned so that tests do not require a live
    database connection.

    In production, a ``PostgresSaver`` backed by the synchronous
    ``DATABASE_URL_SYNC`` connection string is returned.  If the
    ``langgraph-checkpoint-postgres`` package is unavailable or the
    connection cannot be established, the function falls back to
    ``MemorySaver`` and logs a warning.
    """
    from app.config import Settings

    settings = Settings()

    if settings.TESTING:
        logger.debug("Using MemorySaver checkpointer (TESTING=True)")
        return MemorySaver()

    try:
        from langgraph.checkpoint.postgres import PostgresSaver

        checkpointer = PostgresSaver.from_conn_string(settings.DATABASE_URL_SYNC)
        checkpointer.setup()
        logger.info("Using PostgresSaver checkpointer")
        return checkpointer
    except Exception as exc:
        logger.warning(
            "Failed to initialize PostgresSaver, falling back to MemorySaver: %s",
            exc,
        )
        return MemorySaver()
