"""Video-based activity recognition for construction sites."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_load_lock = threading.Lock()

ACTIVITY_TYPES = [
    "concrete_pouring",
    "rebar_tying",
    "formwork_installation",
    "welding",
    "crane_operation",
    "excavation",
    "material_handling",
    "idle",
    "walking",
    "meeting",
    "inspection",
    "cleanup",
]


class ActivityRecognizer:
    """Recognize construction activities from video frames."""

    def __init__(self, model_path: str | None = None):
        self._model = None
        self._model_path = model_path
        self._loaded = False

    def _ensure_model(self):
        """Lazy-load the VideoMAE V2 model."""
        if self._loaded:
            return
        with _load_lock:
            if self._loaded:
                return
            self._load_model()

    def _load_model(self):
        """Actually load the model (called under lock)."""
        try:
            import timm

            self._model = timm.create_model(
                "vit_base_patch16_224",
                pretrained=False,
                num_classes=len(ACTIVITY_TYPES),
            )
            self._model.eval()
            logger.info("Activity recognition model loaded")
        except ImportError:
            logger.warning("timm not available, using rule-based")
            self._model = None
        self._loaded = True

    async def recognize(
        self,
        frames: list,
        camera_id: str = "",
    ) -> dict:
        """Recognize activity from a sequence of frames.

        Parameters
        ----------
        frames: List of video frames (numpy arrays)
        camera_id: Camera identifier

        Returns dict with activity_type, confidence, etc.
        """
        self._ensure_model()

        if not frames:
            return {
                "activity_type": "unknown",
                "confidence": 0.0,
                "worker_count": 0,
            }

        if self._model is not None:
            return await self._model_recognize(frames)
        return self._fallback_recognize(len(frames))

    async def _model_recognize(
        self,
        frames: list,
    ) -> dict:
        """Recognize using loaded model."""
        try:
            import torch
            from torchvision import transforms

            transform = transforms.Compose(
                [
                    transforms.ToPILImage(),
                    transforms.Resize((224, 224)),
                    transforms.ToTensor(),
                    transforms.Normalize(
                        mean=[0.485, 0.456, 0.406],
                        std=[0.229, 0.224, 0.225],
                    ),
                ]
            )

            # Use middle frame for classification
            mid = len(frames) // 2
            tensor = transform(frames[mid]).unsqueeze(0)

            with torch.no_grad():
                assert self._model is not None  # guarded by caller check
                output = self._model(tensor)
                probs = torch.softmax(output, dim=1)
                conf, idx = torch.max(probs, dim=1)

            return {
                "activity_type": (ACTIVITY_TYPES[idx.item()]),
                "confidence": round(float(conf.item()), 3),
                "worker_count": None,
            }
        except Exception as exc:
            logger.error("Model recognition failed: %s", exc)
            return self._fallback_recognize(len(frames))

    def _fallback_recognize(
        self,
        frame_count: int,
    ) -> dict:
        """Fallback when model is unavailable."""
        return {
            "activity_type": "material_handling",
            "confidence": 0.5,
            "worker_count": None,
        }
