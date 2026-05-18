from __future__ import annotations

import logging
from typing import ClassVar

logger = logging.getLogger(__name__)


class ModelCardGenerator:
    """Auto-generate model documentation (model cards)."""

    TEMPLATE: ClassVar[dict[str, object]] = {
        "model_name": "",
        "version": "",
        "description": "",
        "intended_use": "",
        "training_data": "",
        "evaluation_metrics": {},
        "limitations": [],
        "ethical_considerations": [],
    }

    async def generate(
        self,
        model_name: str,
        version: str,
        metrics: dict,
        description: str = "",
    ) -> dict:
        """Generate a model card."""
        card = dict(self.TEMPLATE)
        card["model_name"] = model_name
        card["version"] = version
        card["description"] = description or f"{model_name} v{version}"
        card["evaluation_metrics"] = metrics
        card["intended_use"] = self._infer_use(model_name)
        card["limitations"] = self._infer_limitations(
            model_name,
        )
        logger.info(
            "Generated model card for %s v%s",
            model_name,
            version,
        )
        return card

    def _infer_use(self, model_name: str) -> str:
        """Infer intended use from model name."""
        uses = {
            "defect_detector": ("Construction defect detection in site photos"),
            "ppe_detector": ("PPE compliance detection in video streams"),
            "activity_recognizer": ("Construction activity classification"),
            "document_classifier": ("Construction document type classification"),
        }
        return uses.get(model_name, f"ML inference for {model_name}")

    def _infer_limitations(
        self,
        model_name: str,
    ) -> list[str]:
        """Infer model limitations."""
        return [
            "Performance may vary with lighting conditions",
            "Trained on North American construction practices",
            "Requires minimum image resolution of 640x480",
        ]
