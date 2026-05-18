"""MMDetection RTMDet implementation (Apache-2.0 license - production safe)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.services.vision.detector import BaseDetector, Detection

logger = logging.getLogger(__name__)

try:
    from mmdet.apis import inference_detector, init_detector

    _HAS_MMDET = True
except ImportError:
    _HAS_MMDET = False

# COCO class names for RTMDet pretrained model
COCO_CLASSES = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
]

CONSTRUCTION_RELEVANT = {"person", "truck", "car", "bicycle", "backpack"}


class RTMDetDetector(BaseDetector):
    """RTMDet object detector using MMDetection."""

    _load_lock = threading.Lock()

    def __init__(self):
        self.model = None
        self.device = "cpu"

    def load_model(self, model_path: str, device: str = "cpu") -> None:
        with self._load_lock:
            if self.model is not None:
                return  # Already loaded
            if not _HAS_MMDET:
                raise RuntimeError("mmdet is required. Install with: pip install mmdet mmengine")
            self.device = device
            # model_path should be the config file, with checkpoint in same directory
            config = model_path
            checkpoint = model_path.replace(".py", ".pth")
            if not Path(checkpoint).exists():
                raise FileNotFoundError(f"RTMDet checkpoint not found: {checkpoint}")
            self.model = init_detector(config, checkpoint, device=device)
            logger.info("RTMDet model loaded on %s", device)

    def detect(self, frame, confidence_threshold: float = 0.5) -> list[Detection]:
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load_model() first.")
        result = inference_detector(self.model, frame)
        return self._parse_results(result, confidence_threshold)

    def detect_batch(
        self,
        frames: list,
        confidence_threshold: float = 0.5,
    ) -> list[list[Detection]]:
        if self.model is None:
            raise RuntimeError("Model not loaded")
        return [self.detect(frame, confidence_threshold) for frame in frames]

    def _parse_results(self, result, confidence_threshold: float) -> list[Detection]:
        detections = []
        pred_instances = result.pred_instances
        bboxes = pred_instances.bboxes.cpu().numpy()
        scores = pred_instances.scores.cpu().numpy()
        labels = pred_instances.labels.cpu().numpy()

        for bbox, score, label in zip(bboxes, scores, labels, strict=False):
            if score < confidence_threshold:
                continue
            class_name = COCO_CLASSES[int(label)] if int(label) < len(COCO_CLASSES) else "unknown"
            if class_name not in CONSTRUCTION_RELEVANT:
                continue
            x1, y1, x2, y2 = map(int, bbox)
            detections.append(
                Detection(
                    class_name=class_name,
                    confidence=float(score),
                    bbox=(x1, y1, x2, y2),
                )
            )
        return detections
