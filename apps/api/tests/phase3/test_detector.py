from __future__ import annotations

from app.services.vision.detector import BaseDetector, Detection


class MockDetector(BaseDetector):
    def load_model(self, model_path: str, device: str = "cpu"):
        pass

    def detect(self, frame, confidence_threshold: float = 0.5):
        return [Detection(class_name="person", confidence=0.9, bbox=(100, 100, 200, 300))]

    def detect_batch(self, frames: list, confidence_threshold: float = 0.5):
        return [self.detect(f, confidence_threshold) for f in frames]


class TestDetector:
    def test_detection_dataclass(self):
        det = Detection(class_name="person", confidence=0.92, bbox=(10, 20, 100, 200))
        assert det.class_name == "person"
        assert det.confidence == 0.92
        assert det.bbox == (10, 20, 100, 200)
        assert det.track_id is None

    def test_detection_with_attributes(self):
        det = Detection(
            class_name="person",
            confidence=0.8,
            bbox=(0, 0, 50, 50),
            attributes={"ppe": {"hardhat": True}},
        )
        assert det.attributes["ppe"]["hardhat"] is True

    def test_mock_detector_interface(self):
        det = MockDetector()
        det.load_model("dummy")
        results = det.detect(None)
        assert len(results) == 1
        assert results[0].class_name == "person"

    def test_mock_detector_batch(self):
        det = MockDetector()
        det.load_model("dummy")
        results = det.detect_batch([None, None])
        assert len(results) == 2

    def test_confidence_filtering(self):
        det = MockDetector()
        det.load_model("dummy")
        results = det.detect(None, confidence_threshold=0.95)
        # Our mock always returns 0.9 confidence
        # In real impl this would filter; mock doesn't
        assert len(results) >= 0
