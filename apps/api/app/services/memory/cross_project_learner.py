"""Cross-project pattern learning (anonymized).

SECURITY (C-10): Patterns are scoped by ``org_id`` to prevent cross-tenant
data leakage.  Parameters are anonymized (project-specific identifiers
stripped) before storage, and the pattern list is bounded to prevent
unbounded memory growth.
"""

from __future__ import annotations

import logging
import re
import uuid

logger = logging.getLogger(__name__)

# Valid pattern types
PATTERN_TYPES = {
    "cost_driver",
    "schedule_risk",
    "safety_pattern",
    "productivity_benchmark",
    "quality_issue",
    "weather_impact",
    "supply_chain",
}

# SECURITY (C-10): Maximum number of patterns per org to prevent memory DoS.
_MAX_PATTERNS_PER_ORG = 5000

# Regex to detect and redact potential project-specific identifiers in parameters.
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_PROJECT_ID_KEYS = {"project_id", "project_name", "project_number", "client_name", "client_id"}


def _anonymize_parameters(params: dict) -> dict:
    """Strip project-specific identifiers from parameters.

    SECURITY (C-10): Prevents sensitive project data from leaking across
    the org boundary when patterns are shared.
    """
    if not params:
        return {}
    cleaned: dict = {}
    for key, value in params.items():
        # Skip known project-identifying keys entirely
        if key.lower() in _PROJECT_ID_KEYS:
            continue
        # Redact UUID values that look like entity IDs
        if isinstance(value, str) and _UUID_RE.fullmatch(value):
            continue
        cleaned[key] = value
    return cleaned


class CrossProjectLearner:
    """Learn anonymized patterns across projects.

    SECURITY (C-10): All patterns are scoped by ``org_id`` to prevent
    cross-tenant data leakage.  The pattern list is bounded per-org
    (oldest patterns evicted when limit is reached), and parameters
    are anonymized before storage.
    """

    def __init__(self) -> None:
        # SECURITY (C-10): Patterns keyed by org_id for tenant isolation.
        self._patterns_by_org: dict[str, list[dict]] = {}

    async def record_pattern(
        self,
        pattern_type: str,
        description: str,
        org_id: str,
        parameters: dict | None = None,
        confidence: float = 0.5,
    ) -> str:
        """Record a new learned pattern within an org scope.

        Returns the pattern ID.
        """
        if pattern_type not in PATTERN_TYPES:
            msg = f"Invalid pattern_type: {pattern_type}"
            raise ValueError(msg)

        # SECURITY (C-10): Anonymize parameters to remove project-specific data.
        safe_params = _anonymize_parameters(parameters) if parameters else {}

        org_patterns = self._patterns_by_org.setdefault(org_id, [])

        # Check for existing similar pattern within this org
        existing = self._find_similar(org_id, pattern_type, description)
        if existing:
            existing["project_count"] += 1
            existing["confidence"] = min(
                1.0,
                existing["confidence"] + 0.05,
            )
            if safe_params:
                existing["parameters"].update(safe_params)
            logger.info(
                "Updated pattern %s (count=%d, org=%s)",
                existing["id"],
                existing["project_count"],
                org_id,
            )
            return existing["id"]

        # SECURITY (C-10): Enforce bounded pattern list per org.
        if len(org_patterns) >= _MAX_PATTERNS_PER_ORG:
            # Evict lowest-confidence patterns (bottom 10%)
            evict_count = max(1, _MAX_PATTERNS_PER_ORG // 10)
            org_patterns.sort(key=lambda p: p["confidence"])
            del org_patterns[:evict_count]
            logger.info(
                "Evicted %d low-confidence patterns for org %s (at capacity %d)",
                evict_count,
                org_id,
                _MAX_PATTERNS_PER_ORG,
            )

        pattern_id = str(uuid.uuid4())
        pattern = {
            "id": pattern_id,
            "pattern_type": pattern_type,
            "description": description,
            "parameters": safe_params,
            "project_count": 1,
            "confidence": confidence,
            "org_id": org_id,
        }
        org_patterns.append(pattern)
        logger.info(
            "Recorded new pattern %s: %s (org=%s)",
            pattern_id,
            pattern_type,
            org_id,
        )
        return pattern_id

    async def get_patterns(
        self,
        org_id: str,
        pattern_type: str | None = None,
        min_confidence: float = 0.0,
        min_projects: int = 1,
    ) -> list[dict]:
        """Get learned patterns matching criteria within an org scope."""
        results = self._patterns_by_org.get(org_id, [])
        if pattern_type:
            results = [p for p in results if p["pattern_type"] == pattern_type]
        results = [
            p
            for p in results
            if p["confidence"] >= min_confidence and p["project_count"] >= min_projects
        ]
        return sorted(
            results,
            key=lambda p: p["confidence"],
            reverse=True,
        )

    async def get_recommendations(
        self,
        project_type: str,
        stage: str,
        org_id: str,
    ) -> list[dict]:
        """Get recommendations based on learned patterns within an org scope.

        Filters patterns relevant to the project type and
        current construction stage.
        """
        all_patterns = await self.get_patterns(
            org_id=org_id,
            min_confidence=0.5,
            min_projects=2,
        )

        recommendations = []
        for pattern in all_patterns:
            desc_lower = pattern["description"].lower()
            if project_type.lower() in desc_lower or stage.lower() in desc_lower:
                recommendations.append(
                    {
                        "pattern_id": pattern["id"],
                        "type": pattern["pattern_type"],
                        "description": pattern["description"],
                        "confidence": pattern["confidence"],
                        "based_on_projects": pattern["project_count"],
                    }
                )

        return recommendations

    def _find_similar(
        self,
        org_id: str,
        pattern_type: str,
        description: str,
    ) -> dict | None:
        """Find existing pattern with same type and desc within an org."""
        desc_lower = description.lower()
        for pattern in self._patterns_by_org.get(org_id, []):
            if (
                pattern["pattern_type"] == pattern_type
                and pattern["description"].lower() == desc_lower
            ):
                return pattern
        return None

    def clear(self, org_id: str | None = None) -> None:
        """Clear patterns (for testing).

        If ``org_id`` is provided, clears only that org's patterns.
        Otherwise clears all.
        """
        if org_id:
            self._patterns_by_org.pop(org_id, None)
        else:
            self._patterns_by_org.clear()
