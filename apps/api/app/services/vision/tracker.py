"""Object tracking using DeepSORT for persistent identity across frames."""

from __future__ import annotations

import logging

from app.services.vision.detector import Detection

logger = logging.getLogger(__name__)

try:
    from deep_sort_realtime.deepsort_tracker import DeepSort

    _HAS_DEEPSORT = True
except ImportError:
    DeepSort = None
    _HAS_DEEPSORT = False


class ObjectTracker:
    """Maintains object identity across frames using DeepSORT."""

    def __init__(self, max_age: int = 30, n_init: int = 3):
        self.max_age = max_age
        self.n_init = n_init
        self._tracker = None
        self._next_id = 1
        self._simple_tracks: dict[int, dict] = {}

    def _ensure_tracker(self):
        if _HAS_DEEPSORT and self._tracker is None:
            try:
                self._tracker = DeepSort(max_age=self.max_age, n_init=self.n_init)
            except Exception:
                logger.warning("DeepSORT init failed, using simple tracker")
                self._tracker = None

    def update(self, detections: list[Detection], frame=None) -> list[Detection]:
        """Update tracker with new detections, return detections with track_ids."""
        if not detections:
            return []

        self._ensure_tracker()

        if _HAS_DEEPSORT and self._tracker is not None and frame is not None:
            return self._deepsort_update(detections, frame)
        return self._simple_update(detections)

    def _deepsort_update(self, detections: list[Detection], frame) -> list[Detection]:
        raw_dets = []
        for det in detections:
            x1, y1, x2, y2 = det.bbox
            raw_dets.append(([x1, y1, x2 - x1, y2 - y1], det.confidence, det.class_name))

        assert self._tracker is not None  # narrowed by `_HAS_DEEPSORT and not None` above
        tracks = self._tracker.update_tracks(raw_dets, frame=frame)
        tracked = []
        for track in tracks:
            if not track.is_confirmed():
                continue
            ltrb = track.to_ltrb()
            x1, y1, x2, y2 = map(int, ltrb)
            det_class = track.det_class if hasattr(track, "det_class") else "person"
            det_conf = track.det_conf if hasattr(track, "det_conf") else 0.5
            tracked.append(
                Detection(
                    class_name=str(det_class) if det_class else "person",
                    confidence=float(det_conf) if det_conf else 0.5,
                    bbox=(x1, y1, x2, y2),
                    track_id=track.track_id,
                    attributes={},
                )
            )
        return tracked

    def _simple_update(self, detections: list[Detection]) -> list[Detection]:
        """Simple ID assignment fallback when DeepSORT unavailable."""
        tracked = []
        for det in detections:
            track_id = self._next_id
            self._next_id += 1
            tracked.append(
                Detection(
                    class_name=det.class_name,
                    confidence=det.confidence,
                    bbox=det.bbox,
                    track_id=track_id,
                    attributes=det.attributes,
                )
            )
        return tracked

    def reset(self):
        """Reset all tracks."""
        if self._tracker is not None:
            self._tracker = None
        self._simple_tracks.clear()
        self._next_id = 1
