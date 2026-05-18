from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


def _get_redis():
    """Get Redis connection if available."""
    try:
        from app.services.security.redis_state import get_redis_client

        return get_redis_client()
    except Exception:
        return None


class CanaryDeployer:
    """Canary deployment for ML models.

    Route 5% of inference to new model, compare 24h metrics,
    auto-promote if equal/better, auto-rollback if worse.

    Persists deployment state to Redis when available, with in-memory
    fallback for development.
    """

    REDIS_PREFIX = "cai:canary:"

    def __init__(
        self,
        traffic_percent: int = 5,
        evaluation_hours: int = 24,
    ):
        self.traffic_percent = traffic_percent
        self.evaluation_hours = evaluation_hours
        self._deployments: dict[str, dict] = {}

    def _persist(self, model_name: str, deployment: dict) -> None:
        """Persist deployment to Redis if available."""
        self._deployments[model_name] = deployment
        redis = _get_redis()
        if redis:
            key = f"{self.REDIS_PREFIX}{model_name}"
            redis.set(key, json.dumps(deployment), ex=86400 * 7)

    def _load(self, model_name: str) -> dict | None:
        """Load deployment from Redis or memory."""
        redis = _get_redis()
        if redis:
            key = f"{self.REDIS_PREFIX}{model_name}"
            data = redis.get(key)
            if data:
                deployment = json.loads(data)
                self._deployments[model_name] = deployment
                return deployment
        return self._deployments.get(model_name)

    async def deploy_canary(
        self,
        model_name: str,
        new_version: str,
        traffic_percent: int | None = None,
    ) -> dict:
        """Start canary deployment."""
        pct = traffic_percent if traffic_percent is not None else self.traffic_percent
        deployment = {
            "model_name": model_name,
            "new_version": new_version,
            "traffic_percent": pct,
            "status": "active",
            "started_at": datetime.now(UTC).isoformat(),
            "metrics": {"canary": {}, "production": {}},
        }
        self._persist(model_name, deployment)
        logger.info(
            "Started canary deployment for %s v%s at %d%%",
            model_name,
            new_version,
            pct,
        )
        return deployment

    async def record_metrics(
        self,
        model_name: str,
        is_canary: bool,
        metrics: dict,
    ):
        """Record metrics for canary or production model."""
        deployment = self._load(model_name)
        if not deployment:
            return
        key = "canary" if is_canary else "production"
        deployment["metrics"][key] = metrics
        self._persist(model_name, deployment)

    async def evaluate_canary(
        self,
        model_name: str,
    ) -> dict:
        """Compare canary vs production metrics."""
        deployment = self._load(model_name)
        if not deployment:
            raise ValueError(f"No canary deployment for {model_name}")

        # Enforce minimum evaluation period
        started_at = datetime.fromisoformat(deployment["started_at"])
        min_end = started_at + timedelta(hours=self.evaluation_hours)
        if datetime.now(UTC) < min_end:
            return {
                "model_name": model_name,
                "canary_metrics": deployment["metrics"].get("canary", {}),
                "production_metrics": deployment["metrics"].get("production", {}),
                "recommendation": "wait",
                "reason": f"Minimum evaluation period ({self.evaluation_hours}h) has not elapsed",
            }

        canary = deployment["metrics"].get("canary", {})
        production = deployment["metrics"].get("production", {})

        # Require non-empty metrics before making a decision
        if not canary or not production:
            return {
                "model_name": model_name,
                "canary_metrics": canary,
                "production_metrics": production,
                "recommendation": "wait",
                "reason": "Insufficient metrics for evaluation",
            }

        canary_score = canary.get("accuracy", 0)
        prod_score = production.get("accuracy", 0)
        recommendation = "promote" if canary_score >= prod_score else "rollback"
        return {
            "model_name": model_name,
            "canary_metrics": canary,
            "production_metrics": production,
            "recommendation": recommendation,
        }

    async def promote_or_rollback(
        self,
        model_name: str,
    ) -> dict:
        """Auto-decide based on metrics."""
        evaluation = await self.evaluate_canary(model_name)
        action = evaluation["recommendation"]

        if action == "wait":
            return {
                "model_name": model_name,
                "action": "wait",
                "status": "active",
                "reason": evaluation.get("reason", "Not ready for evaluation"),
            }

        deployment = self._load(model_name) or self._deployments[model_name]
        if action == "promote":
            deployment["status"] = "promoted"
            deployment["traffic_percent"] = 100
            deployment["decided_at"] = datetime.now(UTC).isoformat()
            logger.info("Promoted canary for %s", model_name)
        else:
            deployment["status"] = "rolled_back"
            deployment["traffic_percent"] = 0
            deployment["decided_at"] = datetime.now(UTC).isoformat()
            logger.info(
                "Rolled back canary for %s",
                model_name,
            )
        self._persist(model_name, deployment)
        return {
            "model_name": model_name,
            "action": action,
            "status": deployment["status"],
        }

    def should_route_to_canary(
        self,
        model_name: str,
        request_hash: int,
    ) -> bool:
        """Determine if a request should go to canary model."""
        deployment = self._load(model_name)
        if not deployment or deployment["status"] != "active":
            return False
        return (request_hash % 100) < deployment["traffic_percent"]

    def get_deployment(
        self,
        model_name: str,
    ) -> dict | None:
        """Get current deployment info."""
        return self._load(model_name)

    def list_deployments(self) -> list[dict]:
        """List all active and recent deployments."""
        redis = _get_redis()
        if redis:
            deployments = []
            for key in redis.scan_iter(match=f"{self.REDIS_PREFIX}*"):
                data = redis.get(key)
                if data:
                    deployments.append(json.loads(data))
            return deployments
        return list(self._deployments.values())
