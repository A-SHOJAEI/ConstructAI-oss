"""Sliding window false positive reduction for detection events."""

from __future__ import annotations

import time
from collections import deque


class TemporalSmoother:
    """Requires sustained detection across a sliding window before confirming."""

    def __init__(self, window_size: int = 10, threshold_pct: float = 0.70):
        self.window_size = window_size
        self.threshold_pct = threshold_pct
        self.windows: dict[str, deque[bool]] = {}
        self._last_update: dict[str, float] = {}

    def update(self, track_id: str, violation_type: str, detected: bool) -> bool:
        """Returns True if violation is confirmed (sustained across window)."""
        key = f"{track_id}:{violation_type}"
        if key not in self.windows:
            self.windows[key] = deque(maxlen=self.window_size)
        self.windows[key].append(detected)
        self._last_update[key] = time.monotonic()
        window = self.windows[key]
        if len(window) < self.window_size:
            return False
        positive_count = sum(window)
        return (positive_count / len(window)) >= self.threshold_pct

    def clear_stale(self, max_age_seconds: float = 30.0):
        """Remove windows that haven't been updated recently."""
        now = time.monotonic()
        stale_keys = [k for k, t in self._last_update.items() if now - t >= max_age_seconds]
        for key in stale_keys:
            self.windows.pop(key, None)
            self._last_update.pop(key, None)

    def reset(self):
        """Clear all windows."""
        self.windows.clear()
        self._last_update.clear()
