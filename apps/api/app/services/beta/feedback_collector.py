from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


class FeedbackCollector:
    """Collect and aggregate user feedback (thumbs up/down + text)."""

    def __init__(self):
        self._feedback: list[dict] = []

    async def collect(
        self,
        user_id: str,
        agent_name: str,
        rating: int,
        feedback_text: str = "",
        trace_id: str = "",
        project_id: str = "",
    ) -> dict:
        """Collect user feedback.

        Rating: 1 (thumbs up) or -1 (thumbs down).
        """
        if rating not in (1, -1):
            raise ValueError("Rating must be 1 or -1")
        entry = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "agent_name": agent_name,
            "rating": rating,
            "feedback_text": feedback_text,
            "trace_id": trace_id,
            "project_id": project_id,
        }
        self._feedback.append(entry)
        logger.info(
            "Collected feedback from %s for %s (rating=%d)",
            user_id,
            agent_name,
            rating,
        )
        return entry

    async def get_summary(
        self,
        agent_name: str | None = None,
    ) -> list[dict]:
        """Get aggregated feedback summary per agent."""
        agents: dict[str, dict] = {}
        for f in self._feedback:
            name = f["agent_name"]
            if agent_name and name != agent_name:
                continue
            if name not in agents:
                agents[name] = {
                    "positive": 0,
                    "negative": 0,
                    "total": 0,
                }
            agents[name]["total"] += 1
            if f["rating"] == 1:
                agents[name]["positive"] += 1
            else:
                agents[name]["negative"] += 1
        result = []
        for name, stats in agents.items():
            rate = stats["positive"] / stats["total"] if stats["total"] > 0 else 0
            result.append(
                {
                    "agent_name": name,
                    "total_ratings": stats["total"],
                    "positive_count": stats["positive"],
                    "negative_count": stats["negative"],
                    "approval_rate": round(rate, 3),
                }
            )
        return result

    async def get_feedback(
        self,
        agent_name: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get raw feedback entries."""
        items = self._feedback
        if agent_name:
            items = [f for f in items if f["agent_name"] == agent_name]
        return items[-limit:]
