"""Frame extraction and preprocessing for detection models."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np

    _HAS_CV2 = True
except ImportError:
    cv2 = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]
    _HAS_CV2 = False


def resize_frame(frame, target_size: tuple[int, int] = (640, 640)):
    """Resize frame to target size for model input."""
    if not _HAS_CV2:
        raise RuntimeError("opencv-python-headless required")
    return cv2.resize(frame, target_size, interpolation=cv2.INTER_LINEAR)


def normalize_frame(frame, scale: float = 1.0 / 255.0):
    """Normalize pixel values to 0-1 range."""
    if not _HAS_CV2:
        raise RuntimeError("opencv-python-headless required")
    return frame.astype(np.float32) * scale


def preprocess_frame(frame, target_size: tuple[int, int] = (640, 640)):
    """Full preprocessing: resize + normalize."""
    resized = resize_frame(frame, target_size)
    normalized = normalize_frame(resized)
    return normalized


def batch_preprocess(frames: list, target_size: tuple[int, int] = (640, 640)):
    """Batch preprocess multiple frames."""
    if not _HAS_CV2:
        raise RuntimeError("opencv-python-headless required")
    processed = [preprocess_frame(f, target_size) for f in frames]
    return np.stack(processed, axis=0) if processed else np.array([])


def frame_to_jpeg(frame, quality: int = 85) -> bytes:
    """Encode frame as JPEG bytes for storage/transmission."""
    if not _HAS_CV2:
        raise RuntimeError("opencv-python-headless required")
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    _, buffer = cv2.imencode(".jpg", frame, encode_param)
    return buffer.tobytes()
