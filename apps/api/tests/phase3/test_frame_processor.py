from __future__ import annotations

import pytest

cv2 = pytest.importorskip("cv2")
import numpy as np

from app.services.vision.frame_processor import (
    batch_preprocess,
    normalize_frame,
    preprocess_frame,
    resize_frame,
)


class TestFrameProcessor:
    def test_resize_to_640(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        resized = resize_frame(frame, (640, 640))
        assert resized.shape == (640, 640, 3)

    def test_normalize_values(self):
        frame = np.full((100, 100, 3), 255, dtype=np.uint8)
        normalized = normalize_frame(frame)
        assert normalized.max() <= 1.0
        assert normalized.min() >= 0.0

    def test_preprocess_frame(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        processed = preprocess_frame(frame, (640, 640))
        assert processed.shape == (640, 640, 3)
        assert processed.dtype == np.float32

    def test_batch_processing(self):
        frames = [np.zeros((480, 640, 3), dtype=np.uint8) for _ in range(4)]
        batch = batch_preprocess(frames, (640, 640))
        assert batch.shape == (4, 640, 640, 3)
