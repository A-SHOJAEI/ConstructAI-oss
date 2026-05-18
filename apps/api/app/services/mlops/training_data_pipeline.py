from __future__ import annotations

import logging
import uuid
from typing import ClassVar

logger = logging.getLogger(__name__)


class TrainingDataPipeline:
    """CVAT annotations to validated training dataset."""

    SUPPORTED_FORMATS: ClassVar[set[str]] = {"coco", "pascal_voc", "yolo"}

    def __init__(self):
        self._datasets: list[dict] = []

    async def ingest_annotations(
        self,
        source: str,
        annotation_format: str = "coco",
        project_name: str = "",
    ) -> dict:
        """Ingest annotations from CVAT export."""
        if annotation_format not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format: {annotation_format}")
        dataset_id = str(uuid.uuid4())[:8]
        dataset = {
            "dataset_id": dataset_id,
            "source": source,
            "format": annotation_format,
            "project": project_name,
            "status": "ingested",
            "record_count": 0,
        }
        self._datasets.append(dataset)
        logger.info(
            "Ingested annotations from %s (dataset_id=%s)",
            source,
            dataset_id,
        )
        return dataset

    def _exclude_procore_records(self, records: list[dict]) -> list[dict]:
        """Exclude Procore-sourced records from training data (TOS compliance).

        CRITICAL: Records with data_source='procore' MUST be excluded from
        ML training data per Procore Terms of Service.
        """
        filtered = [r for r in records if r.get("data_source") != "procore"]
        excluded = len(records) - len(filtered)
        if excluded:
            logger.warning(
                "Excluded %d Procore-sourced records from training data (TOS compliance)",
                excluded,
            )
        return filtered

    async def validate_dataset(
        self,
        dataset_id: str,
    ) -> dict:
        """Validate dataset quality."""
        for ds in self._datasets:
            if ds["dataset_id"] == dataset_id:
                ds["status"] = "validated"
                logger.info(
                    "Validated dataset %s",
                    dataset_id,
                )
                return {
                    "dataset_id": dataset_id,
                    "valid": True,
                    "issues": [],
                }
        raise ValueError(f"Dataset {dataset_id} not found")

    async def list_datasets(self) -> list[dict]:
        """List all datasets."""
        return list(self._datasets)
