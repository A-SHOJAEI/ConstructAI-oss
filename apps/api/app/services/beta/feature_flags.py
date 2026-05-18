from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class FeatureFlagService:
    """Feature flag evaluation with Unleash client integration."""

    def __init__(self, provider: str = "local"):
        self.provider = provider
        self._flags: dict[str, dict] = {}

    def register_flag(
        self,
        name: str,
        enabled: bool = False,
        rollout_percentage: int = 0,
        tenant_overrides: dict | None = None,
    ):
        """Register a feature flag."""
        self._flags[name] = {
            "enabled": enabled,
            "rollout_percentage": rollout_percentage,
            "tenant_overrides": tenant_overrides or {},
        }
        logger.info("Registered feature flag: %s", name)

    def is_enabled(
        self,
        flag_name: str,
        org_id: str = "",
        user_id: str = "",
    ) -> bool:
        """Evaluate if a feature flag is enabled."""
        flag = self._flags.get(flag_name)
        if not flag:
            return False
        # Check tenant override first
        if org_id and org_id in flag.get(
            "tenant_overrides",
            {},
        ):
            return flag["tenant_overrides"][org_id]
        if not flag["enabled"]:
            return False
        # Check rollout percentage
        if flag["rollout_percentage"] < 100:
            hash_val = hash(f"{flag_name}:{org_id}:{user_id}") % 100
            return hash_val < flag["rollout_percentage"]
        return True

    def get_all_flags(self) -> dict[str, dict]:
        """Get all registered flags."""
        return dict(self._flags)

    def update_flag(self, name: str, **kwargs):
        """Update a feature flag."""
        if name not in self._flags:
            raise ValueError(f"Flag {name} not found")
        self._flags[name].update(kwargs)
        logger.info("Updated feature flag: %s", name)
