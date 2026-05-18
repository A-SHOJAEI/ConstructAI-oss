"""Video processing Celery worker."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_PATH = "models/safety_yolo_v1.0/best.pt"


def _models_root() -> Path:
    """Canonical, resolved root directory that model files must live under.

    Honors MODELS_ROOT env override for test/staging; falls back to the repo
    root's ``models/`` directory relative to this file.
    """
    override = os.environ.get("MODELS_ROOT")
    if override:
        return Path(override).resolve()
    # app/workers/vision_worker.py -> parents[3] is apps/api/; model artifacts
    # live at the repo root (apps/api/../../models), so walk up one more.
    return (Path(__file__).resolve().parents[3] / "models").resolve()


def _resolve_model_path(candidate: str | os.PathLike[str]) -> Path:
    """Resolve ``candidate`` and require it to live under the models root.

    Protects against attacker-supplied paths in task arguments (path traversal
    via ``../``, absolute-path escape, or symlinks pointing outside the jail).
    Raises ValueError when the resolved path escapes or the file is missing.
    """
    root = _models_root()
    raw = Path(candidate)
    # Interpret relative paths against the models root (typical "models/..." input)
    # and absolute paths as-is; Path.resolve() follows symlinks, which is what
    # makes the containment check safe.
    resolved = (raw if raw.is_absolute() else (root.parent / raw)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"model_path {candidate!r} resolves outside of models root {root}"
        ) from exc
    if not resolved.is_file():
        raise ValueError(f"model_path {candidate!r} does not point to a file")
    return resolved


try:
    from celery import Celery

    _HAS_CELERY = True
except ImportError:
    _HAS_CELERY = False

if _HAS_CELERY:
    from app.config import settings as _settings

    celery_app = Celery("vision", broker=_settings.REDIS_URL)

    class _DLQTask(celery_app.Task):  # type: ignore[name-defined,misc]
        """Base task that logs permanent failures."""

        def on_failure(self, exc, task_id, args, kwargs, einfo):
            logger.error(
                "Vision task %s permanently failed: %s. Args: %s",
                task_id,
                exc,
                args,
            )

    # Module-level cached detector — loaded once at worker startup, reused across tasks.
    _cached_detector = None
    _detector_lock = __import__("threading").Lock()

    def _get_detector(model_path: str = _DEFAULT_MODEL_PATH):
        """Get or create the cached detector singleton.

        ``model_path`` is resolved and required to live inside the models root
        before any loader sees it.
        """
        safe_path = _resolve_model_path(model_path)
        global _cached_detector
        if _cached_detector is not None:
            return _cached_detector
        with _detector_lock:
            if _cached_detector is not None:
                return _cached_detector
            from app.services.vision.detector_rtmdet import RTMDetDetector
            from app.services.vision.detector_yolo import YOLODetector

            path_str = str(safe_path)
            detector: YOLODetector | RTMDetDetector
            try:
                detector = YOLODetector()
                detector.load_model(model_path=path_str)
                logger.info("Vision worker: YOLO detector loaded from %s", path_str)
            except Exception as yolo_err:
                logger.warning("YOLO load failed (%s), falling back to RTMDet", yolo_err)
                detector = RTMDetDetector()
                detector.load_model(model_path=path_str)
                logger.info("Vision worker: RTMDet detector loaded from %s", path_str)
            _cached_detector = detector
            return _cached_detector

    @celery_app.task(
        name="process_camera_frame",
        bind=True,
        base=_DLQTask,
        max_retries=3,
        soft_time_limit=60,
        time_limit=120,
    )
    def process_camera_frame_task(self, camera_id: str, frame_data: dict):
        """Process a camera frame through the safety detection pipeline."""
        import uuid

        # Validate camera_id is a valid UUID
        try:
            uuid.UUID(camera_id)
        except ValueError:
            logger.error("Invalid camera_id format: %s", camera_id)
            return {"status": "error", "reason": "invalid_camera_id"}

        model_path = frame_data.get("model_path", _DEFAULT_MODEL_PATH)
        try:
            detector = _get_detector(model_path)
        except ValueError as path_err:
            # Don't retry on attacker-controlled or missing paths — surface as permanent error.
            logger.error("Rejecting model_path for camera %s: %s", camera_id, path_err)
            return {"status": "error", "reason": "invalid_model_path"}

        try:
            # Run detection
            image_data = frame_data.get("image_bytes") or frame_data.get("image_path")
            if not image_data:
                return {"status": "error", "reason": "no_image_data"}

            detections = detector.detect(image_data)

            return {
                "status": "completed",
                "camera_id": camera_id,
                "detections": detections,
                "detection_count": len(detections) if detections else 0,
            }
        except Exception as e:
            logger.error("Frame processing failed for camera %s: %s", camera_id, e, exc_info=True)
            raise self.retry(exc=e, countdown=30)

    @celery_app.task(
        name="generate_safety_report",
        bind=True,
        base=_DLQTask,
        max_retries=3,
        soft_time_limit=120,
        time_limit=180,
    )
    def generate_safety_report_task(self, project_id: str, date_range: str):
        """Generate daily/weekly safety report from detection results."""
        import uuid
        from datetime import datetime

        # Validate project_id is a valid UUID
        try:
            uuid.UUID(project_id)
        except ValueError:
            logger.error("Invalid project_id format: %s", project_id)
            return {"project_id": project_id, "status": "error", "reason": "invalid_project_id"}

        try:
            # Parse date range
            dates = date_range.split(",")
            start_date = dates[0].strip() if dates else None
            end_date = dates[1].strip() if len(dates) > 1 else start_date

            if not start_date or not end_date:
                return {"project_id": project_id, "status": "error", "reason": "invalid_date_range"}

            logger.info(
                "Generating safety report for project %s from %s to %s",
                project_id,
                start_date,
                end_date,
            )

            # Query detection results from the database for the date range
            from asgiref.sync import async_to_sync
            from sqlalchemy import and_, select

            from app.database import async_session

            async def _generate():
                async with async_session() as db:
                    # Aggregate detection events for the project in the date range
                    # This uses whatever detection/alert models are available
                    try:
                        from app.models.safety_incident import SafetyAlert

                        stmt = select(SafetyAlert).where(
                            and_(
                                SafetyAlert.project_id == uuid.UUID(project_id),
                                SafetyAlert.created_at >= datetime.fromisoformat(start_date),
                                SafetyAlert.created_at <= datetime.fromisoformat(end_date),
                            )
                        )
                        result = await db.execute(stmt)
                        alerts = result.scalars().all()

                        # Build summary
                        total_alerts = len(alerts)
                        by_type: dict[str, int] = {}
                        for alert in alerts:
                            alert_type = getattr(alert, "alert_type", "unknown")
                            by_type[alert_type] = by_type.get(alert_type, 0) + 1

                        return {
                            "total_alerts": total_alerts,
                            "alerts_by_type": by_type,
                            "date_range": {"start": start_date, "end": end_date},
                        }
                    except ImportError:
                        logger.warning("SafetyAlert model not available; returning empty report")
                        return {
                            "total_alerts": 0,
                            "alerts_by_type": {},
                            "date_range": {"start": start_date, "end": end_date},
                        }

            summary = async_to_sync(_generate)()

            return {
                "project_id": project_id,
                "status": "report_generated",
                "summary": summary,
            }
        except Exception as e:
            logger.error(
                "Safety report generation failed for project %s: %s",
                project_id,
                e,
                exc_info=True,
            )
            raise self.retry(exc=e, countdown=60)

else:

    def process_camera_frame_task(camera_id: str, frame_data: dict):
        logger.warning("Celery not available")
        return {"camera_id": camera_id, "status": "celery_unavailable"}

    def generate_safety_report_task(project_id: str, date_range: str):
        logger.warning("Celery not available")
        return {"project_id": project_id, "status": "celery_unavailable"}
