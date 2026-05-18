from __future__ import annotations

from unittest.mock import patch

import pytest

cv2 = pytest.importorskip("cv2")
import numpy as np

from app.services.vision.detector import BaseDetector, Detection
from app.services.vision.pipeline import DetectionPipeline
from app.services.vision.temporal_smoother import TemporalSmoother
from app.services.vision.zone_enforcer import ZoneEnforcer
from tests.fixtures.sample_zones import MOCK_RESTRICTED_ZONE


class _MockDetector(BaseDetector):
    def __init__(self, detections=None):
        self._detections = detections or []

    def load_model(self, model_path, device="cpu"):
        pass

    def detect(self, frame, confidence_threshold=0.5):
        return self._detections

    def detect_batch(self, frames, confidence_threshold=0.5):
        return [self._detections] * len(frames)


@patch("app.services.vision.tracker._HAS_DEEPSORT", False)
class TestPipeline:
    async def test_end_to_end_with_violation(self):
        person_in_zone = Detection(class_name="person", confidence=0.9, bbox=(150, 200, 250, 350))
        detector = _MockDetector([person_in_zone])
        smoother = TemporalSmoother(window_size=1, threshold_pct=0.5)
        zone_enforcer = ZoneEnforcer()
        zone_enforcer.load_zones("cam1", [MOCK_RESTRICTED_ZONE])
        pipeline = DetectionPipeline(
            detector=detector, smoother=smoother, zone_enforcer=zone_enforcer
        )
        frame = np.zeros((640, 640, 3), dtype=np.uint8)
        events = await pipeline.process_frame("cam1", frame)
        assert len(events) >= 1
        assert events[0]["violation"]["violation"] == "zone_breach"

    async def test_pipeline_no_violations(self):
        person_outside = Detection(class_name="person", confidence=0.9, bbox=(400, 400, 500, 500))
        detector = _MockDetector([person_outside])
        smoother = TemporalSmoother(window_size=1, threshold_pct=0.5)
        zone_enforcer = ZoneEnforcer()
        zone_enforcer.load_zones("cam1", [MOCK_RESTRICTED_ZONE])
        pipeline = DetectionPipeline(
            detector=detector, smoother=smoother, zone_enforcer=zone_enforcer
        )
        frame = np.zeros((640, 640, 3), dtype=np.uint8)
        events = await pipeline.process_frame("cam1", frame)
        assert len(events) == 0

    async def test_pipeline_temporal_smoothing(self):
        person = Detection(class_name="person", confidence=0.9, bbox=(150, 200, 250, 350))
        detector = _MockDetector([person])
        smoother = TemporalSmoother(window_size=10, threshold_pct=0.70)
        zone_enforcer = ZoneEnforcer()
        zone_enforcer.load_zones("cam1", [MOCK_RESTRICTED_ZONE])
        pipeline = DetectionPipeline(
            detector=detector, smoother=smoother, zone_enforcer=zone_enforcer
        )
        frame = np.zeros((640, 640, 3), dtype=np.uint8)
        # First frame should not confirm (only 1/10 = 10% < 70%)
        events = await pipeline.process_frame("cam1", frame)
        assert len(events) == 0
