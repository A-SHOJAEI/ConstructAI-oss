"""Five-level degradation architecture."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Degradation levels and their capabilities
DEGRADATION_LEVELS: dict[int, dict[str, Any]] = {
    0: {
        "name": "Full cloud",
        "description": "All providers available",
        "capabilities": {
            "cloud_llm": True,
            "local_llm": True,
            "vision_models": True,
            "rag_search": True,
            "real_time_alerts": True,
            "report_generation": True,
            "transcription": True,
        },
    },
    1: {
        "name": "Provider failover",
        "description": "Primary down, using backup",
        "capabilities": {
            "cloud_llm": True,
            "local_llm": True,
            "vision_models": True,
            "rag_search": True,
            "real_time_alerts": True,
            "report_generation": True,
            "transcription": True,
        },
    },
    2: {
        "name": "Intermittent",
        "description": ("Aggressive timeouts, semantic cache priority"),
        "capabilities": {
            "cloud_llm": True,
            "local_llm": True,
            "vision_models": True,
            "rag_search": True,
            "real_time_alerts": True,
            "report_generation": False,
            "transcription": False,
        },
    },
    3: {
        "name": "Full offline",
        "description": "Local Llama + YOLO only",
        "capabilities": {
            "cloud_llm": False,
            "local_llm": True,
            "vision_models": True,
            "rag_search": False,
            "real_time_alerts": True,
            "report_generation": False,
            "transcription": False,
        },
    },
    4: {
        "name": "Low power",
        "description": "Rules engine only (no ML)",
        "capabilities": {
            "cloud_llm": False,
            "local_llm": False,
            "vision_models": False,
            "rag_search": False,
            "real_time_alerts": True,
            "report_generation": False,
            "transcription": False,
        },
    },
    5: {
        "name": "Emergency",
        "description": "Flush buffers, shutdown gracefully",
        "capabilities": {
            "cloud_llm": False,
            "local_llm": False,
            "vision_models": False,
            "rag_search": False,
            "real_time_alerts": False,
            "report_generation": False,
            "transcription": False,
        },
    },
}


class DegradationManager:
    """Five-level degradation architecture.

    Level 0: Full cloud
    Level 1: Provider failover
    Level 2: Intermittent
    Level 3: Full offline
    Level 4: Low power (rules only)
    Level 5: Emergency shutdown
    """

    def __init__(self):
        self.current_level = 0
        self._provider_health: dict[str, bool] = {}

    async def evaluate_health(
        self,
        provider_states: dict[str, str] | None = None,
    ) -> int:
        """Check provider health, determine degradation level.

        Args:
            provider_states: Dict of provider -> circuit state.
                States: "closed" (healthy), "open" (down),
                "half_open" (recovering).
        """
        if not provider_states:
            return self.current_level

        total = len(provider_states)
        if total == 0:
            self.current_level = 0
            return 0

        open_count = sum(1 for s in provider_states.values() if s == "open")
        half_open_count = sum(1 for s in provider_states.values() if s == "half_open")

        if open_count == 0 and half_open_count == 0:
            new_level = 0
        elif (open_count == 0 and half_open_count > 0) or open_count < total:
            new_level = 1
        elif open_count == total and half_open_count == 0:
            new_level = 3
        else:
            new_level = 2

        if new_level != self.current_level:
            logger.warning(
                "Degradation level changed: %d -> %d (%s)",
                self.current_level,
                new_level,
                DEGRADATION_LEVELS[new_level]["name"],
            )
        self.current_level = new_level
        return new_level

    async def get_available_capabilities(self) -> dict:
        """Return capabilities at current degradation level."""
        level_info = DEGRADATION_LEVELS.get(
            self.current_level,
            DEGRADATION_LEVELS[5],
        )
        return {
            "level": self.current_level,
            "name": level_info["name"],
            "description": level_info["description"],
            "capabilities": level_info["capabilities"],
        }

    async def set_level(self, level: int):
        """Manually set degradation level."""
        if level not in DEGRADATION_LEVELS:
            msg = f"Invalid degradation level: {level}"
            raise ValueError(msg)
        old = self.current_level
        self.current_level = level
        if old != level:
            logger.warning(
                "Degradation level manually set: %d -> %d",
                old,
                level,
            )

    def is_capability_available(
        self,
        capability: str,
    ) -> bool:
        """Check if a specific capability is available."""
        level_info = DEGRADATION_LEVELS.get(
            self.current_level,
            DEGRADATION_LEVELS[5],
        )
        return level_info["capabilities"].get(
            capability,
            False,
        )
