"""Tests for activity recognition service."""

from __future__ import annotations

import numpy as np

from app.services.productivity.activity_recognizer import (
    ACTIVITY_TYPES,
    ActivityRecognizer,
)


class TestActivityRecognizer:
    async def test_fallback_recognition(self):
        recognizer = ActivityRecognizer()
        frames = [np.zeros((224, 224, 3), dtype=np.uint8)]
        result = await recognizer.recognize(frames)
        assert "activity_type" in result
        assert "confidence" in result

    async def test_empty_frames(self):
        recognizer = ActivityRecognizer()
        result = await recognizer.recognize([])
        assert result["activity_type"] == "unknown"
        assert result["confidence"] == 0.0

    async def test_fallback_returns_known_type(self):
        recognizer = ActivityRecognizer()
        frames = [np.zeros((224, 224, 3), dtype=np.uint8)]
        result = await recognizer.recognize(frames)
        assert result["activity_type"] in ACTIVITY_TYPES or (
            result["activity_type"] == "material_handling"
        )

    def test_activity_types_list(self):
        assert len(ACTIVITY_TYPES) >= 10
        assert "concrete_pouring" in ACTIVITY_TYPES
        assert "welding" in ACTIVITY_TYPES
        assert "idle" in ACTIVITY_TYPES

    async def test_multiple_frames(self):
        recognizer = ActivityRecognizer()
        frames = [np.zeros((224, 224, 3), dtype=np.uint8) for _ in range(16)]
        result = await recognizer.recognize(frames)
        assert result["confidence"] > 0
