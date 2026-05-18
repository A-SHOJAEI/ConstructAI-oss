"""Pre-load ML models on startup to avoid cold start latency."""

from __future__ import annotations

import logging
from typing import ClassVar

logger = logging.getLogger(__name__)


class ModelWarmup:
    """Pre-load ML models on startup to avoid cold start latency."""

    MODELS_TO_WARMUP: ClassVar[list[dict[str, str]]] = [
        {"name": "document_classifier", "type": "sklearn"},
        {"name": "defect_detector", "type": "pytorch"},
        {"name": "activity_recognizer", "type": "pytorch"},
        {"name": "ppe_detector", "type": "yolo"},
        {
            "name": "embedding_model",
            "type": "sentence_transformer",
        },
    ]

    def __init__(self) -> None:
        self._loaded_models: dict[str, dict] = {}

    async def warmup_all(self) -> dict:
        """Load all models into memory."""
        results: dict[str, dict] = {}
        for model_info in self.MODELS_TO_WARMUP:
            name = model_info["name"]
            try:
                await self._load_model(model_info)
                results[name] = {
                    "status": "loaded",
                    "type": model_info["type"],
                }
                logger.info("Warmed up model: %s", name)
            except Exception as e:
                results[name] = {
                    "status": "failed",
                    "error": str(e),
                }
                logger.warning(
                    "Failed to warm up %s: %s",
                    name,
                    e,
                )
        return results

    async def _load_model(self, model_info: dict) -> None:
        """Load a single model (placeholder)."""
        # In production, loads actual model files
        self._loaded_models[model_info["name"]] = {
            "type": model_info["type"],
            "loaded": True,
        }

    def is_loaded(self, model_name: str) -> bool:
        """Check if a model is loaded."""
        return model_name in self._loaded_models

    def get_loaded_models(self) -> list[str]:
        """Get list of loaded model names."""
        return list(self._loaded_models.keys())
