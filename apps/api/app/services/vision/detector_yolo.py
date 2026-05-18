"""
YOLO11 implementation for development/prototyping.

WARNING: Ultralytics YOLO is licensed under AGPL-3.0.
This implementation is for DEVELOPMENT AND PROTOTYPING ONLY.
Do NOT use in production SaaS deployment.
For production, use RTMDet (Apache-2.0 license) via detector_rtmdet.py.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.services.vision.detector import BaseDetector, Detection

logger = logging.getLogger(__name__)

try:
    from ultralytics import YOLO

    _HAS_YOLO = True
except ImportError:
    _HAS_YOLO = False

CONSTRUCTION_RELEVANT = {"person", "truck", "car", "bicycle", "backpack"}


class YOLODetector(BaseDetector):
    """YOLO11 detector - DEVELOPMENT/PROTOTYPING ONLY (AGPL-3.0)."""

    _load_lock = threading.Lock()

    def __init__(self):
        self.model = None

    def load_model(self, model_path: str, device: str = "cpu") -> None:
        with self._load_lock:
            if self.model is not None:
                return  # Already loaded
            if not _HAS_YOLO:
                raise RuntimeError("ultralytics is required: pip install ultralytics")
            logger.warning("YOLO is AGPL-3.0 licensed - dev/prototyping only!")
            # Prefer the TensorRT .engine sibling when present — same accuracy,
            # 2-4x faster on NVIDIA hardware. Fall through to .pt otherwise.
            engine_candidate = Path(model_path).with_suffix(".engine")
            chosen_path = str(engine_candidate) if engine_candidate.exists() else model_path
            if chosen_path != model_path:
                logger.info("YOLO: preferring TensorRT engine at %s", chosen_path)
            self.model = YOLO(chosen_path)
            self.model.to(device)
            logger.info("YOLO model loaded: %s on %s", chosen_path, device)

    def detect(self, frame, confidence_threshold: float = 0.5) -> list[Detection]:
        if self.model is None:
            raise RuntimeError("Model not loaded")
        results = self.model.predict(frame, conf=confidence_threshold, verbose=False)
        return self._parse_results(results, confidence_threshold)

    def detect_batch(
        self,
        frames: list,
        confidence_threshold: float = 0.5,
    ) -> list[list[Detection]]:
        if self.model is None:
            raise RuntimeError("Model not loaded")
        all_results = self.model.predict(frames, conf=confidence_threshold, verbose=False)
        return [self._parse_results([r], confidence_threshold) for r in all_results]

    def _parse_results(self, results, confidence_threshold: float) -> list[Detection]:
        detections = []
        for result in results:
            for box in result.boxes:
                class_name = result.names[int(box.cls)]
                conf = float(box.conf)
                if conf < confidence_threshold or class_name not in CONSTRUCTION_RELEVANT:
                    continue
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                detections.append(
                    Detection(
                        class_name=class_name,
                        confidence=conf,
                        bbox=(x1, y1, x2, y2),
                    )
                )
        return detections
