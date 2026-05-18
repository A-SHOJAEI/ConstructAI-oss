from __future__ import annotations

import pytest

cv2 = pytest.importorskip("cv2")
import numpy as np

from app.services.vision.stream_manager import CameraStream, StreamManager


def _create_test_video(path: str, num_frames: int = 30, fps: int = 30):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, fps, (640, 480))
    for i in range(num_frames):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.rectangle(frame, (100 + i * 5, 100), (200 + i * 5, 300), (0, 255, 0), -1)
        writer.write(frame)
    writer.release()


class TestStreamManager:
    async def test_open_video_file(self, tmp_path):
        video_path = str(tmp_path / "test.mp4")
        _create_test_video(video_path)
        stream = CameraStream("cam1", f"file://{video_path}", target_fps=5)
        await stream.start()
        assert stream.is_running
        ret, frame = await stream.get_frame()
        assert ret is True
        assert frame is not None
        assert frame.shape[0] > 0
        await stream.stop()

    async def test_frame_extraction(self, tmp_path):
        video_path = str(tmp_path / "test.mp4")
        _create_test_video(video_path, num_frames=30, fps=30)
        stream = CameraStream("cam1", f"file://{video_path}", target_fps=5)
        await stream.start()
        frames_read = 0
        for _ in range(10):
            ret, _frame = await stream.get_frame()
            if ret:
                frames_read += 1
        await stream.stop()
        assert frames_read > 0

    async def test_stream_manager_add_remove(self, tmp_path):
        video_path = str(tmp_path / "test.mp4")
        _create_test_video(video_path)
        mgr = StreamManager(max_cameras=4)
        await mgr.add_camera("cam1", f"file://{video_path}", fps=5)
        assert "cam1" in mgr.streams
        await mgr.remove_camera("cam1")
        assert "cam1" not in mgr.streams
        await mgr.shutdown()
