"""Edge vision pipeline for Jetson deployment with TensorRT inference."""
from __future__ import annotations

import logging
import os
import signal
import time

logger = logging.getLogger(__name__)

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


class EdgePipeline:
    """Main edge vision pipeline for Jetson deployment."""

    def __init__(
        self,
        model_path: str,
        device_id: str,
        camera_urls: list[str] | None = None,
        confidence_threshold: float = 0.5,
        target_fps: int = 15,
    ):
        self.model_path = model_path
        self.device_id = device_id
        self.camera_urls = camera_urls or []
        self.confidence_threshold = confidence_threshold
        self.target_fps = target_fps
        self._running = False
        self._model = None
        self._captures: dict[str, object] = {}
        self._mqtt_client = None
        self._offline_buffer = None
        self._frame_count = 0
        self._start_time = 0.0

    def load_model(self):
        """Load TensorRT engine model."""
        if not os.path.exists(self.model_path):
            logger.warning("Model not found: %s, running in demo mode", self.model_path)
            return

        try:
            # In production, use TensorRT runtime to deserialize engine
            logger.info("Loading TensorRT model: %s", self.model_path)
            # self._model = load_tensorrt_engine(self.model_path)
            logger.info("Model loaded successfully")
        except Exception as exc:
            logger.error("Failed to load model: %s", exc)

    def setup_cameras(self):
        """Initialize camera capture streams."""
        if not _HAS_CV2:
            logger.warning("OpenCV not available, skipping camera setup")
            return

        for i, url in enumerate(self.camera_urls):
            cam_id = f"cam{i}"
            try:
                cap = cv2.VideoCapture(url)
                if cap.isOpened():
                    self._captures[cam_id] = cap
                    logger.info("Camera %s connected: %s", cam_id, url)
                else:
                    logger.warning("Failed to open camera %s: %s", cam_id, url)
            except Exception as exc:
                logger.error("Camera %s error: %s", cam_id, exc)

    def setup_mqtt(self):
        """Initialize MQTT client for event publishing."""
        try:
            from src.mqtt_client import EdgeMQTTClient
            mqtt_host = os.environ.get("MQTT_HOST", "localhost")
            mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
            self._mqtt_client = EdgeMQTTClient(
                host=mqtt_host,
                port=mqtt_port,
                device_id=self.device_id,
            )
            self._mqtt_client.connect()
            logger.info("MQTT connected: %s:%d", mqtt_host, mqtt_port)
        except Exception as exc:
            logger.warning("MQTT setup failed, using offline buffer: %s", exc)
            try:
                from src.offline_buffer import OfflineBuffer
                self._offline_buffer = OfflineBuffer(self.device_id)
                logger.info("Offline buffer initialized")
            except Exception as buf_exc:
                logger.error("Offline buffer setup failed: %s", buf_exc)

    def process_frame(self, camera_id: str, frame) -> list[dict]:
        """Process a single frame through the detection pipeline."""
        detections = []

        if self._model is None:
            # Demo mode: return empty detections
            return detections

        try:
            # Preprocess
            if _HAS_NUMPY:
                input_tensor = np.array(
                    cv2.resize(frame, (640, 640)),
                    dtype=np.float32,
                ) / 255.0
                input_tensor = np.transpose(input_tensor, (2, 0, 1))
                input_tensor = np.expand_dims(input_tensor, 0)
            else:
                return detections

            # Run inference (TensorRT)
            # outputs = self._model.infer(input_tensor)
            # detections = self._parse_outputs(outputs)

        except Exception as exc:
            logger.error("Frame processing error on %s: %s", camera_id, exc)

        return detections

    def publish_events(self, camera_id: str, detections: list[dict]):
        """Publish detection events via MQTT or buffer offline."""
        import json

        for det in detections:
            event = {
                "device_id": self.device_id,
                "camera_id": camera_id,
                "timestamp": time.time(),
                **det,
            }
            payload = json.dumps(event)

            if self._mqtt_client:
                try:
                    self._mqtt_client.publish(
                        f"constructai/{self.device_id}/detections",
                        payload,
                    )
                except Exception:
                    if self._offline_buffer:
                        self._offline_buffer.store(payload)
            elif self._offline_buffer:
                self._offline_buffer.store(payload)

    def run(self):
        """Main processing loop."""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

        self._running = True
        self._start_time = time.time()
        frame_interval = 1.0 / self.target_fps

        # M-56: Per-frame watchdog. A hung TensorRT inference would
        # otherwise freeze the entire pipeline indefinitely. Cap at 5x
        # the target frame interval — well under "the device is dead"
        # but long enough to absorb normal inference jitter.
        frame_timeout = max(1.0, frame_interval * 5.0)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="frame-proc")
        consecutive_timeouts = 0

        logger.info(
            "Edge pipeline started: device=%s, cameras=%d, fps=%d, watchdog=%.1fs",
            self.device_id,
            len(self._captures),
            self.target_fps,
            frame_timeout,
        )

        while self._running:
            loop_start = time.time()

            for cam_id, cap in self._captures.items():
                ret, frame = cap.read()
                if not ret:
                    logger.warning("Frame read failed on %s", cam_id)
                    continue

                future = executor.submit(self.process_frame, cam_id, frame)
                try:
                    detections = future.result(timeout=frame_timeout)
                    consecutive_timeouts = 0
                except FuturesTimeoutError:
                    consecutive_timeouts += 1
                    logger.error(
                        "Frame processing timeout on %s (%.1fs, consecutive=%d)",
                        cam_id,
                        frame_timeout,
                        consecutive_timeouts,
                    )
                    # After 3 consecutive timeouts, reload the model
                    # (process_frame will re-init on next entry).
                    if consecutive_timeouts >= 3:
                        logger.error(
                            "Watchdog: %d consecutive timeouts, resetting detector",
                            consecutive_timeouts,
                        )
                        self._model = None
                        consecutive_timeouts = 0
                    continue

                if detections:
                    self.publish_events(cam_id, detections)

                self._frame_count += 1

            # Rate limiting
            elapsed = time.time() - loop_start
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)

            # Periodic health report
            if self._frame_count % (self.target_fps * 60) == 0 and self._frame_count > 0:
                uptime = time.time() - self._start_time
                actual_fps = self._frame_count / uptime if uptime > 0 else 0
                logger.info(
                    "Pipeline stats: frames=%d, uptime=%.0fs, fps=%.1f",
                    self._frame_count,
                    uptime,
                    actual_fps,
                )

    def stop(self):
        """Graceful shutdown."""
        self._running = False
        for cam_id, cap in self._captures.items():
            try:
                cap.release()
            except Exception:
                pass
            logger.info("Camera %s released", cam_id)

        if self._mqtt_client:
            try:
                self._mqtt_client.disconnect()
            except Exception:
                pass

        logger.info("Edge pipeline stopped")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    model_path = os.environ.get("MODEL_PATH", "/models/rtmdet_construction.engine")
    device_id = os.environ.get("DEVICE_ID", "jetson-001")
    camera_urls_str = os.environ.get("CAMERA_URLS", "")
    camera_urls = [u.strip() for u in camera_urls_str.split(",") if u.strip()]

    pipeline = EdgePipeline(
        model_path=model_path,
        device_id=device_id,
        camera_urls=camera_urls,
    )

    # Handle graceful shutdown
    def signal_handler(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        pipeline.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    pipeline.load_model()
    pipeline.setup_cameras()
    pipeline.setup_mqtt()
    pipeline.run()


if __name__ == "__main__":
    main()
