from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class UsageAnalytics:
    """Event tracking for feature adoption metrics."""

    def __init__(self):
        self._events: list[dict] = []

    async def track(
        self,
        event_name: str,
        user_id: str = "",
        org_id: str = "",
        properties: dict | None = None,
    ):
        """Track a usage event."""
        self._events.append(
            {
                "event": event_name,
                "user_id": user_id,
                "org_id": org_id,
                "properties": properties or {},
            }
        )
        logger.debug("Tracked event: %s", event_name)

    async def get_feature_adoption(self) -> dict:
        """Get feature adoption metrics."""
        features: dict[str, set] = {}
        for e in self._events:
            name = e["event"]
            uid = e["user_id"]
            if name not in features:
                features[name] = set()
            if uid:
                features[name].add(uid)
        return {
            name: {
                "unique_users": len(users),
                "total_events": sum(1 for ev in self._events if ev["event"] == name),
            }
            for name, users in features.items()
        }

    async def get_events(
        self,
        event_name: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get raw events."""
        items = self._events
        if event_name:
            items = [e for e in items if e["event"] == event_name]
        return items[-limit:]

    def clear(self):
        """Clear all events (for testing)."""
        self._events.clear()
