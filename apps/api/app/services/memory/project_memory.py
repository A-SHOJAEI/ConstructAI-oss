"""Long-term project fact store."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

# SECURITY [H-11]: Sanitize user-controlled text before storage/retrieval
# to prevent agent memory injection attacks.
from app.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)

# SECURITY [H-12]: Prevent unbounded memory growth per project.
_MAX_FACTS_PER_PROJECT = 1000
_MAX_FACT_TEXT_LENGTH = 10_000

# Valid fact types
FACT_TYPES = {
    "decision",
    "constraint",
    "requirement",
    "preference",
    "contact",
    "budget",
    "schedule",
    "risk",
    "lesson_learned",
}

# Valid source types
SOURCE_TYPES = {
    "conversation",
    "document",
    "agent_output",
    "user_input",
    "system_derived",
}


class ProjectMemory:
    """Long-term project fact store.

    Stores facts extracted from conversations and agent outputs.
    Retrieves relevant facts via semantic search.
    Tracks temporal validity (valid_from / valid_until).
    """

    def __init__(self):
        self._facts: dict[str, list[dict]] = {}

    async def store_fact(
        self,
        project_id: str,
        fact_type: str,
        fact_text: str,
        source_type: str,
        source_id: str | None = None,
        confidence: float = 1.0,
        metadata: dict | None = None,
    ) -> str:
        """Store a new project fact.

        Returns the fact ID.
        """
        if fact_type not in FACT_TYPES:
            msg = f"Invalid fact_type: {fact_type}"
            raise ValueError(msg)
        if source_type not in SOURCE_TYPES:
            msg = f"Invalid source_type: {source_type}"
            raise ValueError(msg)

        # SECURITY [H-11]: Sanitize fact_text to prevent prompt injection.
        fact_text = sanitize_for_prompt(fact_text, max_length=_MAX_FACT_TEXT_LENGTH)

        fact_id = str(uuid.uuid4())
        fact = {
            "id": fact_id,
            "project_id": project_id,
            "fact_type": fact_type,
            "fact_text": fact_text,
            "source_type": source_type,
            "source_id": source_id,
            "confidence": confidence,
            "valid_from": datetime.now(UTC).isoformat(),
            "valid_until": None,
            "metadata": metadata or {},
        }

        if project_id not in self._facts:
            self._facts[project_id] = []

        # SECURITY [H-12]: Enforce per-project fact limit to prevent unbounded
        # memory growth. Evict lowest-confidence facts when limit is reached.
        project_facts = self._facts[project_id]
        if len(project_facts) >= _MAX_FACTS_PER_PROJECT:
            # Find the active fact with the lowest confidence to evict
            active = [f for f in project_facts if f["valid_until"] is None]
            if active:
                lowest = min(active, key=lambda f: f["confidence"])
                lowest["valid_until"] = datetime.now(UTC).isoformat()
                logger.warning(
                    "Evicted fact %s (confidence=%.2f) for project %s — reached %d-fact limit",
                    lowest["id"],
                    lowest["confidence"],
                    project_id,
                    _MAX_FACTS_PER_PROJECT,
                )
            else:
                # All facts are invalidated; remove the oldest entry entirely
                project_facts.pop(0)

        project_facts.append(fact)

        logger.info(
            "Stored fact %s for project %s: %s",
            fact_id,
            project_id,
            fact_type,
        )
        return fact_id

    async def retrieve_facts(
        self,
        project_id: str,
        query: str,
        limit: int = 10,
    ) -> list[dict]:
        """Retrieve relevant facts via semantic similarity.

        In production, uses vector similarity search.
        For now, uses simple text matching.
        """
        project_facts = self._facts.get(project_id, [])
        active = [f for f in project_facts if f["valid_until"] is None]

        # Simple text match scoring
        query_lower = query.lower()
        scored = []
        for fact in active:
            text_lower = fact["fact_text"].lower()
            words = query_lower.split()
            matches = sum(1 for w in words if w in text_lower)
            if matches > 0:
                scored.append((matches, fact))

        scored.sort(key=lambda x: x[0], reverse=True)
        # SECURITY [H-11]: Sanitize fact_text on retrieval before returning
        # to agent context, in case old unsanitized data exists.
        results = []
        for _, fact in scored[:limit]:
            sanitized = dict(fact)
            sanitized["fact_text"] = sanitize_for_prompt(
                sanitized["fact_text"], max_length=_MAX_FACT_TEXT_LENGTH
            )
            results.append(sanitized)
        return results

    async def invalidate_fact(self, fact_id: str) -> bool:
        """Set valid_until on a superseded fact."""
        now = datetime.now(UTC).isoformat()
        for facts in self._facts.values():
            for fact in facts:
                if fact["id"] == fact_id:
                    fact["valid_until"] = now
                    return True
        return False

    async def get_active_facts(
        self,
        project_id: str,
        fact_type: str | None = None,
    ) -> list[dict]:
        """Get all currently valid facts for a project."""
        project_facts = self._facts.get(project_id, [])
        active = [f for f in project_facts if f["valid_until"] is None]
        if fact_type:
            active = [f for f in active if f["fact_type"] == fact_type]
        # SECURITY [H-11]: Sanitize fact_text on retrieval for agent context.
        results = []
        for fact in active:
            sanitized = dict(fact)
            sanitized["fact_text"] = sanitize_for_prompt(
                sanitized["fact_text"], max_length=_MAX_FACT_TEXT_LENGTH
            )
            results.append(sanitized)
        return results

    def clear(self):
        """Clear all facts (for testing)."""
        self._facts.clear()
