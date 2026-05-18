from __future__ import annotations

import logging
import uuid
from typing import ClassVar

logger = logging.getLogger(__name__)


class RetrainingPipeline:
    """Automated retraining triggered on drift detection."""

    RETRAINABLE_MODELS: ClassVar[set[str]] = {
        "defect_detector",
        "ppe_detector",
        "activity_recognizer",
        "document_classifier",
    }

    def __init__(self):
        self._training_runs: list[dict] = []

    async def trigger_retraining(
        self,
        model_name: str,
        reason: str = "drift_detected",
        dataset_version: str = "latest",
    ) -> dict:
        """Trigger model retraining."""
        if model_name not in self.RETRAINABLE_MODELS:
            raise ValueError(f"Model {model_name} is not retrainable")
        run_id = str(uuid.uuid4())[:8]
        run = {
            "run_id": run_id,
            "model_name": model_name,
            "reason": reason,
            "dataset_version": dataset_version,
            "status": "queued",
        }
        self._training_runs.append(run)
        logger.info(
            "Triggered retraining for %s (run_id=%s, reason=%s)",
            model_name,
            run_id,
            reason,
        )
        return run

    async def get_run_status(
        self,
        run_id: str,
    ) -> dict | None:
        """Get training run status."""
        for run in self._training_runs:
            if run["run_id"] == run_id:
                return run
        return None

    async def list_runs(
        self,
        model_name: str | None = None,
    ) -> list[dict]:
        """List training runs, optionally filtered by model."""
        if model_name:
            return [r for r in self._training_runs if r["model_name"] == model_name]
        return list(self._training_runs)
