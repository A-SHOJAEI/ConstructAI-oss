"""Multi-camera RTSP stream handler with frame extraction."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Allowed stream URL schemes — only RTSP and local file
_ALLOWED_SCHEMES = {"rtsp", "rtsps", "file"}


def _validate_stream_url(url: str) -> str:
    """Validate stream URL to prevent SSRF attacks.

    Only allows RTSP/RTSPS protocols and local file:// paths.
    Blocks HTTP/HTTPS, internal IPs, and metadata endpoints.

    Raises ValueError if the URL is not allowed.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Unsupported stream protocol: {scheme}. Only RTSP/RTSPS and file:// are allowed."
        )

    # file:// paths are local-only — no network risk
    if scheme == "file":
        return url

    # For RTSP: resolve hostname and block internal/private IPs
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Stream URL must include a hostname")

    # Block well-known metadata endpoints
    if hostname in ("169.254.169.254", "metadata.google.internal", "metadata"):
        raise ValueError("Stream URL points to a blocked metadata endpoint")

    try:
        resolved_ips = socket.getaddrinfo(hostname, None)
        for _family, _type, _proto, _canonname, sockaddr in resolved_ips:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError(f"Stream URL resolves to internal/private IP: {sockaddr[0]}")
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve stream hostname: {hostname}") from exc

    return url


try:
    import cv2
    import numpy as np

    _HAS_CV2 = True
except ImportError:
    cv2 = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]
    _HAS_CV2 = False


class CameraStream:
    """Manages a single RTSP or video file stream."""

    def __init__(self, camera_id: str, stream_url: str, target_fps: int = 5):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.target_fps = target_fps
        self.cap = None
        self.is_running = False
        self.frame_buffer: asyncio.Queue = asyncio.Queue(maxsize=30)
        self._source_fps = 30.0
        self._frame_interval = 1.0 / target_fps

    async def start(self, executor: ThreadPoolExecutor | None = None):
        """Open the video capture in a thread pool."""
        if not _HAS_CV2:
            raise RuntimeError("opencv-python-headless is required")
        loop = asyncio.get_event_loop()
        exc = executor or ThreadPoolExecutor(max_workers=1)
        cap_obj: Any = await loop.run_in_executor(exc, self._open_capture)
        self.cap = cap_obj
        if self.cap and self.cap.isOpened():
            self.is_running = True
            self._source_fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
            logger.info("Camera %s started: %s", self.camera_id, self.stream_url)
        else:
            raise RuntimeError(f"Failed to open stream: {self.stream_url}")

    def _open_capture(self) -> Any:
        """Open a cv2.VideoCapture; returns the cv2 capture object (or None
        when cv2 isn't available)."""
        if cv2 is None:
            return None
        url = self.stream_url
        if url.startswith("file://"):
            url = url[7:]
        return cv2.VideoCapture(url)

    async def stop(self):
        self.is_running = False
        if self.cap:
            self.cap.release()
            self.cap = None

    def read_frame(self):
        """Read a single frame (blocking, call from thread pool)."""
        if self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            return (ret, frame) if ret else (False, None)
        return (False, None)

    async def get_frame(self, executor: ThreadPoolExecutor | None = None):
        """Get next frame asynchronously."""
        if not self.is_running:
            return (False, None)
        loop = asyncio.get_event_loop()
        exc = executor or ThreadPoolExecutor(max_workers=1)
        return await loop.run_in_executor(exc, self.read_frame)


class StreamManager:
    """Manages multiple simultaneous camera streams."""

    def __init__(self, max_cameras: int = 16):
        self.streams: dict[str, CameraStream] = {}
        self.executor = ThreadPoolExecutor(max_workers=max_cameras)
        self.max_cameras = max_cameras

    async def add_camera(self, camera_id: str, stream_url: str, fps: int = 5):
        if len(self.streams) >= self.max_cameras:
            raise RuntimeError(f"Max cameras ({self.max_cameras}) reached")
        # Validate URL to prevent SSRF — only allow RTSP/RTSPS/file
        validated_url = _validate_stream_url(stream_url)
        stream = CameraStream(camera_id, validated_url, fps)
        await stream.start(self.executor)
        self.streams[camera_id] = stream

    async def remove_camera(self, camera_id: str):
        stream = self.streams.pop(camera_id, None)
        if stream:
            await stream.stop()

    async def get_frame(self, camera_id: str):
        stream = self.streams.get(camera_id)
        if not stream:
            return (False, None)
        return await stream.get_frame(self.executor)

    async def get_all_frames(self) -> dict:
        """Get latest frame from all cameras."""
        results = {}
        tasks = []
        for cam_id, stream in self.streams.items():
            tasks.append((cam_id, stream.get_frame(self.executor)))
        for cam_id, task in tasks:
            ret, frame = await task
            if ret:
                results[cam_id] = frame
        return results

    async def shutdown(self):
        for stream in self.streams.values():
            await stream.stop()
        self.streams.clear()
        self.executor.shutdown(wait=False)
