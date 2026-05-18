"""Tests for the edge pipeline."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from edge.src.edge_pipeline import EdgePipeline


class TestEdgePipeline:
    def test_init(self):
        pipeline = EdgePipeline(
            model_path="/models/test.engine",
            device_id="test-001",
            camera_urls=["rtsp://cam1", "rtsp://cam2"],
        )
        assert pipeline.device_id == "test-001"
        assert len(pipeline.camera_urls) == 2
        assert pipeline.target_fps == 15

    def test_process_frame_no_model(self):
        pipeline = EdgePipeline(
            model_path="/models/test.engine",
            device_id="test-001",
        )
        # Without model loaded, should return empty
        detections = pipeline.process_frame("cam1", MagicMock())
        assert detections == []

    def test_stop(self):
        pipeline = EdgePipeline(
            model_path="/models/test.engine",
            device_id="test-001",
        )
        pipeline._running = True
        pipeline.stop()
        assert pipeline._running is False
