"""Object detection model interface and detection dataclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Detection:
    """A single object detection result."""

    class_name: str
    confidence: float
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2)
    track_id: int | None = None
    attributes: dict = field(default_factory=dict)


class BaseDetector(ABC):
    """Abstract base for object detectors."""

    @abstractmethod
    def load_model(self, model_path: str, device: str = "cpu") -> None:
        """Load detection model from path."""

    @abstractmethod
    def detect(self, frame, confidence_threshold: float = 0.5) -> list[Detection]:
        """Run detection on a single frame."""

    @abstractmethod
    def detect_batch(
        self, frames: list, confidence_threshold: float = 0.5
    ) -> list[list[Detection]]:
        """Run detection on a batch of frames."""


# COCO class name mapping for construction-relevant classes
CONSTRUCTION_CLASSES = {
    0: "person",
    24: "backpack",
    25: "umbrella",
    56: "chair",
    63: "laptop",
    66: "keyboard",
    67: "cell phone",
    # Custom fine-tuned classes (added after training)
    80: "hardhat",
    81: "no_hardhat",
    82: "safety_vest",
    83: "no_vest",
    84: "crane",
    85: "excavator",
    86: "forklift",
    87: "scaffolding",
}
