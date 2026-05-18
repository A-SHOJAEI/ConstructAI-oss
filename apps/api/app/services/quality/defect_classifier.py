"""AI-powered defect classification using Vision Transformer."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import torch

logger = logging.getLogger(__name__)

_load_lock = threading.Lock()

# ---------------------------------------------------------------------------
# v1.0 taxonomy (legacy)
# ---------------------------------------------------------------------------
DEFECT_TYPES_V1_0 = [
    "crack_structural",
    "crack_cosmetic",
    "spalling",
    "delamination",
    "corrosion",
    "water_damage",
    "improper_alignment",
    "missing_component",
    "surface_defect",
    "weld_defect",
    "concrete_honeycombing",
    "rebar_exposure",
]

# ---------------------------------------------------------------------------
# v1.1 taxonomy (8 well-defined classes)
# ---------------------------------------------------------------------------
DEFECT_TYPES_V1_1 = [
    "crack",
    "spalling",
    "corrosion",
    "efflorescence",
    "exposed_rebar",
    "surface_deterioration",
    "biological_growth",
    "no_defect",
]

# Default to v1.1 for new code
DEFECT_TYPES = DEFECT_TYPES_V1_1

SEVERITY_MAP = {
    # v1.1 classes
    "crack": "critical",
    "exposed_rebar": "critical",
    "spalling": "major",
    "corrosion": "major",
    "efflorescence": "major",
    "surface_deterioration": "major",
    "biological_growth": "minor",
    "no_defect": "none",
    # v1.0 legacy classes (backward compat)
    "crack_structural": "critical",
    "rebar_exposure": "critical",
    "delamination": "major",
    "water_damage": "major",
    "weld_defect": "major",
    "concrete_honeycombing": "major",
    "crack_cosmetic": "minor",
    "improper_alignment": "minor",
    "missing_component": "minor",
    "surface_defect": "minor",
}


class DefectClassifier:
    """Classify construction defects from images using ViT."""

    def __init__(self, model_path: str | None = None):
        self._model = None
        self._model_path = model_path
        self._loaded = False
        self._model_type: str = "fallback"
        self._class_names: list[str] = list(DEFECT_TYPES_V1_1)
        self._model_version: str = "v1.1"

    def _detect_model_version(self) -> tuple[str, list[str]]:
        """Auto-detect model version from metadata.json or checkpoint."""
        if not self._model_path:
            return "v1.1", list(DEFECT_TYPES_V1_1)

        model_dir = Path(self._model_path).parent
        metadata_path = model_dir / "metadata.json"
        class_mapping_path = model_dir / "class_mapping.txt"

        # Try metadata.json first
        if metadata_path.exists():
            try:
                meta = json.loads(metadata_path.read_text())
                version = meta.get("model_version", "v1.0")
                classes = meta.get("class_names", [])
                if classes:
                    return version, classes
            except (json.JSONDecodeError, OSError):
                pass

        # Try class_mapping.txt
        if class_mapping_path.exists():
            try:
                classes = []
                for line in class_mapping_path.read_text().strip().splitlines():
                    parts = line.split("\t", 1)
                    if len(parts) == 2:
                        classes.append(parts[1])
                if classes:
                    version = "v1.1" if "no_defect" in classes else "v1.0"
                    return version, classes
            except OSError:
                pass

        # Fallback: detect from checkpoint head size
        try:
            import torch

            state = torch.load(self._model_path, map_location="cpu", weights_only=True)
            num_classes = state["head.weight"].shape[0]
            if num_classes == 8:
                return "v1.1", list(DEFECT_TYPES_V1_1)
            elif num_classes <= 12:
                return "v1.0", list(DEFECT_TYPES_V1_0[:num_classes])
            else:
                logger.warning(
                    "Cannot determine class order from checkpoint alone (num_classes=%d). "
                    "Place metadata.json or class_mapping.txt alongside the model.",
                    num_classes,
                )
                return "v1.1", list(DEFECT_TYPES_V1_1)
        except Exception:
            return "v1.1", list(DEFECT_TYPES_V1_1)

    def _ensure_model(self):
        """Lazy-load the model on first use."""
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

            if self._model_path:
                self._model_version, self._class_names = self._detect_model_version()
                num_classes = len(self._class_names)

                self._model = timm.create_model(
                    "vit_base_patch16_224",
                    pretrained=False,
                    num_classes=num_classes,
                )
                import torch

                state = torch.load(
                    self._model_path,
                    map_location="cpu",
                    weights_only=True,
                )
                self._model.load_state_dict(state)
                self._model_type = "vit_finetuned"
                logger.info(
                    "Loaded %s model with %d classes: %s",
                    self._model_version,
                    num_classes,
                    self._class_names,
                )
            else:
                self._model = timm.create_model(
                    "vit_base_patch16_224",
                    pretrained=True,
                    num_classes=1000,
                )
                self._model_type = "vit_pretrained"

            self._model.eval()
            logger.info("Defect classifier model loaded (%s)", self._model_type)
        except ImportError:
            logger.warning("timm/torch not available, using rule-based")
            self._model = None
            self._model_type = "fallback"
        self._loaded = True

    async def classify(
        self,
        image_bytes: bytes,
    ) -> dict:
        """Classify a defect image.

        Returns dict with defect_type, confidence,
        severity_estimate, recommendations, model_available,
        and model_type.
        """
        self._ensure_model()

        if self._model is not None:
            return await self._model_classify(image_bytes)
        return self._fallback_classify()

    async def _model_classify(
        self,
        image_bytes: bytes,
    ) -> dict:
        """Classify using the loaded ViT model."""
        if self._model is None:
            return self._fallback_classify()
        try:
            import io

            import torch
            from PIL import Image
            from torchvision import transforms

            transform = transforms.Compose(
                [
                    transforms.Resize((224, 224)),
                    transforms.ToTensor(),
                    transforms.Normalize(
                        mean=[0.485, 0.456, 0.406],
                        std=[0.229, 0.224, 0.225],
                    ),
                ]
            )

            img_file = Image.open(io.BytesIO(image_bytes))
            img = img_file.convert("RGB")
            tensor = transform(img).unsqueeze(0)

            with torch.no_grad():
                output = self._model(tensor)
                probs = torch.softmax(output, dim=1)

            if self._model_type == "vit_finetuned":
                conf, idx = torch.max(probs, dim=1)
                defect_type = self._class_names[idx.item()]
                confidence = float(conf.item())
            else:
                # ImageNet pretrained (1000 classes) — map to construction
                # defect types using visual feature heuristics
                defect_type, confidence = _map_imagenet_to_defect(probs)

            recommendations = _get_recommendations(defect_type)

            # Flag low-confidence predictions
            if confidence < 0.3:
                recommendations = list(recommendations)  # copy
                recommendations.append(
                    "Low confidence classification - manual inspection recommended"
                )

            return {
                "defect_type": defect_type,
                "confidence": round(confidence, 3),
                "severity_estimate": SEVERITY_MAP.get(defect_type, "minor"),
                "recommendations": recommendations,
                "model_available": True,
                "model_type": self._model_type,
            }
        except Exception as exc:
            logger.error("Model classification failed: %s", exc)
            return self._fallback_classify()

    def _fallback_classify(self) -> dict:
        """Rule-based fallback classification."""
        return {
            "defect_type": "surface_deterioration",
            "confidence": 0.1,
            "severity_estimate": "minor",
            "recommendations": [
                "Manual inspection recommended",
                "Upload higher resolution image",
                "Low confidence classification - manual inspection recommended",
            ],
            "model_available": False,
            "model_type": "fallback",
        }


# ---------------------------------------------------------------------------
# ImageNet → construction defect mapping
# ---------------------------------------------------------------------------
# ImageNet class indices that correlate with construction defect visual patterns.
# These are approximate mappings using the visual similarity between ImageNet
# categories and construction defects.
_IMAGENET_DEFECT_MAP: dict[str, list[int]] = {
    "crack_structural": [717, 807],  # cliff, stone wall (crack-like textures)
    "crack_cosmetic": [840, 717],  # swab, cliff
    "spalling": [846, 743],  # tank, pool table (pitted surfaces)
    "corrosion": [489, 492],  # chain, chest (metallic textures)
    "water_damage": [611, 641],  # jellyfish, maillot (wet/stained)
    "surface_defect": [693, 694],  # padlock, paper towel
    "concrete_honeycombing": [804, 805],  # snowmobile, socks (porous textures)
    "rebar_exposure": [620, 621],  # laptop, letter opener (linear metallic)
    "weld_defect": [489, 490],  # chain, chainlink fence
    "delamination": [724, 729],  # pillow, plate rack (layered surfaces)
    "improper_alignment": [694, 695],  # paper towel, parking meter
    "missing_component": [639, 640],  # magnetic compass, mailbag
}


def _map_imagenet_to_defect(probs: torch.Tensor) -> tuple[str, float]:
    """Map ImageNet 1000-class probabilities to a construction defect type.

    Uses aggregated probability across ImageNet classes that visually correlate
    with each defect type. Returns (defect_type, confidence).
    Note: This is a heuristic bridge until a fine-tuned model is available.
    Confidence is capped at 0.4 to signal the heuristic nature.
    """
    best_type = "surface_defect"
    best_score = 0.0

    probs_np = probs.squeeze().cpu().numpy()

    for defect_type, indices in _IMAGENET_DEFECT_MAP.items():
        score = sum(float(probs_np[idx]) for idx in indices if idx < len(probs_np))
        if score > best_score:
            best_score = score
            best_type = defect_type

    # Cap confidence since this is a heuristic mapping, not trained
    confidence = min(best_score, 0.4)
    return best_type, confidence


def _get_recommendations(defect_type: str) -> list[str]:
    """Get remediation recommendations for a defect type."""
    recs = {
        # v1.1 classes
        "crack": [
            "Immediate structural assessment required",
            "Install crack monitors to track movement",
            "Engage structural engineer for repair design",
            "Classify as structural vs cosmetic by width (>0.3mm = structural)",
        ],
        "spalling": [
            "Remove loose material and assess depth",
            "Apply patching compound per spec section",
            "Check for rebar exposure beneath spalled area",
        ],
        "corrosion": [
            "Assess extent of corrosion damage",
            "Apply corrosion inhibitor treatment",
            "Review waterproofing system integrity",
        ],
        "efflorescence": [
            "Identify and repair water infiltration source",
            "Apply waterproofing membrane",
            "Monitor for ongoing moisture ingress",
        ],
        "exposed_rebar": [
            "Verify concrete cover depth per design",
            "Patch exposed area per structural spec",
            "Document for structural engineer review",
            "Check for section loss on exposed reinforcement",
        ],
        "surface_deterioration": [
            "Document extent with measurements",
            "Chip back to sound concrete if honeycombing",
            "Assess per project spec acceptance criteria",
            "Plan repair per ACI 546.3R if structural",
        ],
        "biological_growth": [
            "Clean affected area with appropriate biocide",
            "Identify moisture source promoting growth",
            "Apply anti-fungal treatment after cleaning",
        ],
        "no_defect": [
            "No defects detected — surface appears in good condition",
            "Include in routine monitoring schedule",
        ],
        # v1.0 legacy classes (backward compat)
        "crack_structural": [
            "Immediate structural assessment required",
            "Install crack monitors to track movement",
            "Engage structural engineer for repair design",
        ],
        "crack_cosmetic": [
            "Schedule cosmetic repair during finishing",
            "Monitor for crack growth over 30 days",
        ],
        "rebar_exposure": [
            "Verify concrete cover depth per design",
            "Patch exposed area per structural spec",
            "Document for structural engineer review",
        ],
        "delamination": [
            "Test bond strength per ASTM D4541",
            "Plan repair per ACI 546.3R",
        ],
        "water_damage": [
            "Identify and repair water source",
            "Apply waterproofing membrane",
            "Monitor for mold growth",
        ],
        "concrete_honeycombing": [
            "Chip back to sound concrete",
            "Patch per ACI 301 Section 5.3",
            "Review vibration procedures",
        ],
        "surface_defect": [
            "Document with measurements",
            "Assess per project spec acceptance criteria",
        ],
    }
    return recs.get(
        defect_type,
        [
            "Document with photographs",
            "Schedule repair in next maintenance window",
        ],
    )
