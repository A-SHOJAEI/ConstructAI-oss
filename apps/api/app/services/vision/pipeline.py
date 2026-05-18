"""Full detection pipeline orchestrator: frame -> detect -> track -> smooth -> zone -> alert."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.services.vision.detector import BaseDetector
from app.services.vision.temporal_smoother import TemporalSmoother
from app.services.vision.tracker import ObjectTracker
from app.services.vision.zone_enforcer import ZoneEnforcer

logger = logging.getLogger(__name__)


class DetectionPipeline:
    """Full pipeline: frame -> detect -> track -> smooth -> zone check -> events."""

    def __init__(
        self,
        detector: BaseDetector,
        tracker: ObjectTracker | None = None,
        smoother: TemporalSmoother | None = None,
        zone_enforcer: ZoneEnforcer | None = None,
    ):
        self.detector = detector
        self.tracker = tracker or ObjectTracker()
        self.smoother = smoother or TemporalSmoother()
        self.zone_enforcer = zone_enforcer or ZoneEnforcer()

    async def process_frame(self, camera_id: str, frame) -> list[dict]:
        """Process single frame through full pipeline."""
        # 1. Detect objects
        detections = self.detector.detect(frame)

        # 2. Track objects
        tracked = self.tracker.update(detections, frame)

        # 3. Check zones and apply temporal smoothing
        events = []
        for det in tracked:
            violations = self.zone_enforcer.check_detection(camera_id, det)
            for violation in violations:
                confirmed = self.smoother.update(
                    str(det.track_id),
                    violation["violation"],
                    True,
                )
                if confirmed:
                    events.append(
                        {
                            "camera_id": camera_id,
                            "detection": {
                                "class_name": det.class_name,
                                "confidence": det.confidence,
                                "bbox": list(det.bbox),
                                "track_id": det.track_id,
                            },
                            "violation": violation,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    )

        # 4. Clear stale smoother entries periodically
        self.smoother.clear_stale()

        return events

    async def process_batch(self, camera_frames: dict) -> dict[str, list[dict]]:
        """Process frames from multiple cameras."""
        results = {}
        for camera_id, frame in camera_frames.items():
            events = await self.process_frame(camera_id, frame)
            if events:
                results[camera_id] = events
        return results
