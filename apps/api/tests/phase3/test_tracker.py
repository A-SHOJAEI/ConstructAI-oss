from __future__ import annotations

from app.services.vision.detector import Detection
from app.services.vision.tracker import ObjectTracker


class TestTracker:
    def test_assign_track_ids(self):
        tracker = ObjectTracker()
        detections = [
            Detection(class_name="person", confidence=0.9, bbox=(100, 100, 200, 300)),
            Detection(class_name="person", confidence=0.85, bbox=(300, 100, 400, 300)),
        ]
        tracked = tracker.update(detections)
        assert len(tracked) == 2
        assert all(t.track_id is not None for t in tracked)

    def test_unique_track_ids(self):
        tracker = ObjectTracker()
        detections = [
            Detection(class_name="person", confidence=0.9, bbox=(100, 100, 200, 300)),
            Detection(class_name="person", confidence=0.85, bbox=(300, 100, 400, 300)),
        ]
        tracked = tracker.update(detections)
        ids = [t.track_id for t in tracked]
        assert len(set(ids)) == 2

    def test_new_object_gets_new_id(self):
        tracker = ObjectTracker()
        det1 = [Detection(class_name="person", confidence=0.9, bbox=(100, 100, 200, 300))]
        tracked1 = tracker.update(det1)
        det2 = [Detection(class_name="person", confidence=0.9, bbox=(500, 500, 600, 600))]
        tracked2 = tracker.update(det2)
        assert tracked1[0].track_id != tracked2[0].track_id

    def test_reset_clears_tracks(self):
        tracker = ObjectTracker()
        tracker.update([Detection(class_name="person", confidence=0.9, bbox=(100, 100, 200, 300))])
        tracker.reset()
        assert tracker._next_id == 1
