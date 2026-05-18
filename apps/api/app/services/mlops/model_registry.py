from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ModelRegistry:
    """MLflow-based model registry for all production models."""

    def __init__(
        self,
        tracking_uri: str = "http://localhost:5000",
    ):
        self.tracking_uri = tracking_uri
        # In-memory registry for testing (production uses MLflow)
        self._models: dict[str, list[dict]] = {}

    async def register_model(
        self,
        name: str,
        artifact_path: str,
        metrics: dict,
        tags: dict | None = None,
    ) -> str:
        """Register a new model version. Returns version string."""
        if name not in self._models:
            self._models[name] = []
        version = str(len(self._models[name]) + 1)
        self._models[name].append(
            {
                "version": version,
                "artifact_path": artifact_path,
                "metrics": metrics,
                "tags": tags or {},
                "stage": "staging",
            }
        )
        logger.info(
            "Registered model %s version %s",
            name,
            version,
        )
        return version

    async def get_production_model(
        self,
        name: str,
    ) -> dict | None:
        """Get the current production model."""
        versions = self._models.get(name, [])
        for v in reversed(versions):
            if v["stage"] == "production":
                return v
        return None

    async def promote_to_production(
        self,
        name: str,
        version: str,
    ):
        """Promote a model version to production stage."""
        versions = self._models.get(name, [])
        # Demote current production
        for v in versions:
            if v["stage"] == "production":
                v["stage"] = "archived"
        # Promote target
        for v in versions:
            if v["version"] == version:
                v["stage"] = "production"
                logger.info(
                    "Promoted %s version %s to production",
                    name,
                    version,
                )
                return
        raise ValueError(f"Version {version} not found for {name}")

    async def list_models(self) -> list[dict]:
        """List all registered models with their latest version."""
        result = []
        for name, versions in self._models.items():
            result.append(
                {
                    "name": name,
                    "versions": len(versions),
                    "latest_version": (versions[-1]["version"] if versions else None),
                    "production_version": next(
                        (v["version"] for v in reversed(versions) if v["stage"] == "production"),
                        None,
                    ),
                }
            )
        return result

    async def get_model_versions(
        self,
        name: str,
    ) -> list[dict]:
        """Get all versions of a model."""
        return list(self._models.get(name, []))
