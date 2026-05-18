"""Per-tenant configuration: feature flags, model preferences."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TenantConfigManager:
    """Manage per-tenant configuration: feature flags, model prefs."""

    def __init__(self) -> None:
        self._configs: dict[str, dict] = {}

    async def get_config(self, org_id: str) -> dict:
        """Get tenant configuration."""
        return self._configs.get(
            org_id,
            {
                "feature_flags": {},
                "model_preferences": {},
                "notification_settings": {},
                "billing_plan": "startup",
            },
        )

    async def update_config(
        self,
        org_id: str,
        updates: dict,
    ) -> dict:
        """Update tenant configuration. Merges with existing."""
        current = await self.get_config(org_id)
        current.update(updates)
        self._configs[org_id] = current
        logger.info("Updated config for org %s", org_id)
        return current

    async def get_model_preference(
        self,
        org_id: str,
        agent_name: str,
    ) -> str | None:
        """Get tenant's preferred model for a specific agent."""
        config = await self.get_config(org_id)
        return config.get("model_preferences", {}).get(agent_name)
