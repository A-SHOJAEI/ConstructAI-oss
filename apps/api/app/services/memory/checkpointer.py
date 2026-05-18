"""LangGraph PostgresSaver integration for workflow checkpointing."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CheckpointerFactory:
    """Create checkpointer instances for LangGraph workflows.

    In production, uses AsyncPostgresSaver from
    langgraph-checkpoint-postgres. Falls back to in-memory
    checkpointer for testing.
    """

    @staticmethod
    async def create(db_url: str | None = None) -> object:
        """Create a checkpointer instance.

        Args:
            db_url: PostgreSQL connection string.
                If None, returns in-memory checkpointer.
        """
        if db_url:
            try:
                from langgraph.checkpoint.postgres.aio import (
                    AsyncPostgresSaver,
                )

                async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
                    await checkpointer.setup()
                    logger.info("PostgresSaver checkpointer created")
                    return checkpointer
            except ImportError:
                logger.warning(
                    "langgraph-checkpoint-postgres not available, using in-memory checkpointer",
                )
            except Exception:
                logger.warning(
                    "PostgresSaver setup failed, using in-memory checkpointer",
                )

        return InMemoryCheckpointer()


class InMemoryCheckpointer:
    """Simple in-memory checkpointer for testing."""

    def __init__(self):
        self._storage: dict[str, dict] = {}

    async def aget(self, config: dict) -> dict | None:
        """Get checkpoint by thread_id."""
        thread_id = config.get("configurable", {}).get(
            "thread_id",
            "",
        )
        return self._storage.get(thread_id)

    async def aput(
        self,
        config: dict,
        checkpoint: dict,
        metadata: dict | None = None,
    ) -> dict:
        """Store checkpoint."""
        thread_id = config.get("configurable", {}).get(
            "thread_id",
            "",
        )
        self._storage[thread_id] = {
            "checkpoint": checkpoint,
            "metadata": metadata or {},
        }
        return config

    async def alist(
        self,
        config: dict | None = None,
    ) -> list[dict]:
        """List all checkpoints."""
        return list(self._storage.values())

    def clear(self):
        """Clear all checkpoints."""
        self._storage.clear()
