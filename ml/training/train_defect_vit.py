"""ViT defect classifier training pipeline (v1.1).

Fine-tunes ViT-B/16 (ImageNet pretrained) for 8-class construction defect
classification using Mendeley, CODEBRIM, SDNET2018, and optionally dacl10k,
MBDD2025, BD3, and S2DS datasets.

v1.1 changes from v1.0:
    - Revised 8-class taxonomy (from 12): cleaner, well-defined classes
    - Fixed Mendeley round-robin label noise — now only crack/no_defect
    - Added no_defect class using negative images
    - CODEBRIM multi-label: copies to ALL active classes (not just first)
    - SDNET includes uncracked images as no_defect
    - FocalLoss with label smoothing (replaces weighted CE)
    - Mixup/CutMix augmentation
    - Class-specific augmentation for scarce classes
    - Unfreeze 6/12 blocks (was 4/12)
    - Early stopping on macro_f1 only (not val_acc)
    - Per-class capping to prevent majority-class dominance
    - Confusion matrix and per-source evaluation

Training strategy:
    - Freeze first 6 transformer blocks, train last 6 + head
    - 50 epochs, AdamW, lr=5e-5, cosine schedule with 3-epoch warmup
    - Mixed precision (FP16) training
    - Early stopping patience: 10 (on macro_f1)
    - FocalLoss(gamma=2.0) + label smoothing(0.1)
    - Mixup(alpha=0.8) / CutMix(alpha=1.0)

Targets:
    - Overall accuracy > 70%
    - Macro F1 > 0.65
    - Per-class F1 > 0.70 for structural defects

Usage:
    # Full pipeline with existing datasets
    python -m ml.training.train_defect_vit \\
        --mendeley-dir constructai-data/cv-training/mendeley-cracks/ \\
        --codebrim-dir constructai-data/cv-training/codebrim/ \\
        --sdnet-dir constructai-data/cv-training/sdnet2018/ \\
        --output-dir models/defect_vit_v1.1

    # With additional datasets
    python -m ml.training.train_defect_vit \\
        --mendeley-dir constructai-data/cv-training/mendeley-cracks/ \\
        --codebrim-dir constructai-data/cv-training/codebrim/ \\
        --sdnet-dir constructai-data/cv-training/sdnet2018/ \\
        --dacl10k-dir constructai-data/cv-training/dacl10k/ \\
        --bd3-dir constructai-data/cv-training/bd3/ \\
        --s2ds-dir constructai-data/cv-training/s2ds/ \\
        --output-dir models/defect_vit_v1.1
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import shutil
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# v1.1 Defect class taxonomy (8 classes)
# ---------------------------------------------------------------------------

DEFECT_CLASSES: dict[int, str] = {
    0: "crack",
    1: "spalling",
    2: "corrosion",
    3: "efflorescence",
    4: "exposed_rebar",
    5: "surface_deterioration",
    6: "biological_growth",
    7: "no_defect",
}

DEFECT_CLASS_NAMES: list[str] = [DEFECT_CLASSES[i] for i in range(8)]

# Structural defect classes — target F1 > 0.70
STRUCTURAL_CLASSES: set[str] = {
    "crack", "spalling", "corrosion", "efflorescence",
    "exposed_rebar", "surface_deterioration",
}

# Classes likely to have insufficient data — get aggressive augmentation
SCARCE_CLASSES: set[str] = {
    "exposed_rebar", "biological_growth", "corrosion", "spalling",
}

# Cap per class to handle extreme imbalance (crack/no_defect dominate)
MAX_IMAGES_PER_CLASS: int = 8000

# ---------------------------------------------------------------------------
# Dataset label mappings (v1.1)
# ---------------------------------------------------------------------------

# CODEBRIM XML fields → v1.1 taxonomy
_CODEBRIM_MAP: dict[str, str] = {
    "Crack": "crack",
    "Spallation": "spalling",
    "Efflorescence": "efflorescence",
    "ExposedBars": "exposed_rebar",
    "CorrosionStain": "corrosion",
}

# SDNET2018 directory → v1.1 taxonomy
_SDNET_MAP: dict[str, str] = {
    "CD": "crack",       # deck cracked
    "CP": "crack",       # pavement cracked
    "CW": "crack",       # wall cracked
    "UD": "no_defect",   # deck uncracked
    "UP": "no_defect",   # pavement uncracked
    "UW": "no_defect",   # wall uncracked
}

# dacl10k damage labels → v1.1 taxonomy
_DACL10K_MAP: dict[str, str] = {
    "Crack": "crack",
    "ACrack": "crack",              # alligator crack
    "Alligator Crack": "crack",
    "Spalling": "spalling",
    "Efflorescence": "efflorescence",
    "ExposedRebars": "exposed_rebar",
    "Exposed Rebars": "exposed_rebar",
    "Corrosion": "corrosion",
    "Corrosion Stain": "corrosion",
    "CorrosionStain": "corrosion",
    "Rust": "corrosion",
    "WConccor": "corrosion",
    "Rockpocket": "surface_deterioration",
    "Cavity": "surface_deterioration",
    "Weathering": "surface_deterioration",
    "Restformwork": "surface_deterioration",
    "Hollowareas": "surface_deterioration",
    "Bearing": "no_defect",
    "Drainage": "no_defect",
    "Joint Tape": "no_defect",
    "JTape": "no_defect",
    "Protective Equipment": "no_defect",
    "PEquipment": "no_defect",
    "EJoint": "no_defect",
    # Skip non-defect labels: Graffiti, Wetspot (not in our taxonomy)
}

# BD3 class names → v1.1 taxonomy
_BD3_MAP: dict[str, str] = {
    "Algae": "biological_growth",
    "algae": "biological_growth",
    "major crack": "crack",
    "Major Crack": "crack",
    "major_crack": "crack",
    "minor crack": "crack",
    "Minor Crack": "crack",
    "minor_crack": "crack",
    "peeling": "surface_deterioration",
    "Peeling": "surface_deterioration",
    "spalling": "spalling",
    "Spalling": "spalling",
    "stain": "efflorescence",
    "Stain": "efflorescence",
    "normal": "no_defect",
    "Normal": "no_defect",
    "plain": "no_defect",
    "Plain": "no_defect",
}

# Brickwork cracks → v1.1 taxonomy
_BRICKWORK_MAP: dict[str, str] = {
    "Positive": "crack",
    "Negative": "no_defect",
}

# Historical Building Cracks → v1.1 taxonomy
_HISTORICAL_MAP: dict[str, str] = {
    "crack": "crack",
    "non-crack": "no_defect",
}

# S2DS segmentation mask RGB colors → v1.1 taxonomy
# Maps (R, G, B) pixel colors in label masks to defect classes
_S2DS_COLOR_MAP: dict[tuple[int, int, int], str] = {
    (255, 255, 255): "crack",          # white
    (255, 0, 0): "spalling",           # red
    (255, 255, 0): "corrosion",        # yellow
    (0, 255, 255): "efflorescence",    # cyan
    (0, 255, 0): "biological_growth",  # green (vegetation)
    # (0, 0, 255) = control_point → skip (not a defect)
    # (0, 0, 0) = background → skip
}

# MBDD2025 class names → v1.1 taxonomy
_MBDD_MAP: dict[str, str] = {
    "crack": "crack",
    "Crack": "crack",
    "leakage": "efflorescence",
    "Leakage": "efflorescence",
    "corrosion": "corrosion",
    "Corrosion": "corrosion",
    "abscission": "surface_deterioration",
    "Abscission": "surface_deterioration",
    "bulge": "surface_deterioration",
    "Bulge": "surface_deterioration",
}


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------


def prepare_mendeley(
    mendeley_dir: Path,
    output_dir: Path,
    train_ratio: float = 0.8,
    seed: int = 42,
) -> dict[str, int]:
    """Prepare Mendeley Concrete Crack dataset (v1.1: no round-robin).

    Positive → crack (all 20K are actual crack images)
    Negative → no_defect (all 20K are clean concrete surfaces)
    """
    logger.info("Processing Mendeley Concrete Cracks from %s", mendeley_dir)

    pos_dir = neg_dir = None
    for candidate in mendeley_dir.rglob("Positive"):
        if candidate.is_dir():
            pos_dir = candidate
            break
    if pos_dir is None:
        for candidate in mendeley_dir.rglob("positive"):
            if candidate.is_dir():
                pos_dir = candidate
                break
    for candidate in mendeley_dir.rglob("Negative"):
        if candidate.is_dir():
            neg_dir = candidate
            break
    if neg_dir is None:
        for candidate in mendeley_dir.rglob("negative"):
            if candidate.is_dir():
                neg_dir = candidate
                break

    total_counts: Counter = Counter()
    random.seed(seed)

    # Positive → crack
    if pos_dir and pos_dir.exists():
        pos_images = sorted(
            list(pos_dir.glob("*.jpg")) + list(pos_dir.glob("*.png"))
        )
        random.shuffle(pos_images)
        split_idx = int(len(pos_images) * train_ratio)

        for split_name, split_imgs in [
            ("train", pos_images[:split_idx]),
            ("val", pos_images[split_idx:]),
        ]:
            dst_dir = output_dir / split_name / "crack"
            dst_dir.mkdir(parents=True, exist_ok=True)
            for img in split_imgs:
                dst = dst_dir / f"mendeley_{img.name}"
                if not dst.exists():
                    shutil.copy2(img, dst)
                total_counts["crack"] += 1
        logger.info("  Positive (cracked): %d → crack", len(pos_images))
    else:
        logger.warning("  Positive directory not found in %s", mendeley_dir)

    # Negative → no_defect
    if neg_dir and neg_dir.exists():
        neg_images = sorted(
            list(neg_dir.glob("*.jpg")) + list(neg_dir.glob("*.png"))
        )
        random.shuffle(neg_images)
        split_idx = int(len(neg_images) * train_ratio)

        for split_name, split_imgs in [
            ("train", neg_images[:split_idx]),
            ("val", neg_images[split_idx:]),
        ]:
            dst_dir = output_dir / split_name / "no_defect"
            dst_dir.mkdir(parents=True, exist_ok=True)
            for img in split_imgs:
                dst = dst_dir / f"mendeley_{img.name}"
                if not dst.exists():
                    shutil.copy2(img, dst)
                total_counts["no_defect"] += 1
        logger.info("  Negative (clean): %d → no_defect", len(neg_images))
    else:
        logger.info("  Negative directory not found, skipping no_defect from Mendeley")

    return dict(total_counts)


def prepare_codebrim(
    codebrim_dir: Path,
    output_dir: Path,
    seed: int = 42,
) -> dict[str, int]:
    """Prepare CODEBRIM multi-label dataset (v1.1: rarest-class assignment).

    For multi-label images, assigns to the RAREST class only (by running
    count) to avoid label confusion in single-label classification. Copying
    the same image to multiple class dirs creates conflicting gradients.
    Background images → no_defect.
    """
    import xml.etree.ElementTree as ET

    logger.info("Processing CODEBRIM from %s", codebrim_dir)

    if not codebrim_dir.exists():
        logger.info("  CODEBRIM not found, skipping")
        return {}

    total_counts: Counter = Counter()
    random.seed(seed)

    # Parse XML metadata
    defects_xml = codebrim_dir / "metadata" / "defects.xml"
    file_labels: dict[str, list[str]] = {}  # filename → list of target classes

    if defects_xml.exists():
        tree = ET.parse(defects_xml)
        root = tree.getroot()
        for defect_el in root.findall("Defect"):
            filename = defect_el.get("name", "")
            if not filename:
                continue
            classes = []
            for xml_field, our_cls in _CODEBRIM_MAP.items():
                val = defect_el.findtext(xml_field, "0")
                if val == "1":
                    classes.append(our_cls)
            if classes:
                file_labels[filename] = classes
        logger.info("  Parsed %d defect annotations from XML", len(file_labels))
    else:
        logger.warning("  defects.xml not found at %s", defects_xml)

    # Process images from train/val/test splits
    multi_label_count = 0
    for split_src in ["train", "val", "test"]:
        # Defect images
        defects_dir = codebrim_dir / split_src / "defects"
        if defects_dir.exists():
            split_dst = "val" if split_src == "test" else split_src
            images = sorted(
                list(defects_dir.glob("*.png")) + list(defects_dir.glob("*.jpg"))
            )
            for img in images:
                target_classes = file_labels.get(img.name, ["crack"])
                if len(target_classes) > 1:
                    multi_label_count += 1
                    # Assign to the rarest class (by running count) to avoid
                    # label confusion. This gives minority classes clean signal.
                    cls = min(target_classes, key=lambda c: total_counts[c])
                else:
                    cls = target_classes[0]
                dst_dir = output_dir / split_dst / cls
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / f"codebrim_{cls}_{img.name}"
                if not dst.exists():
                    shutil.copy2(img, dst)
                total_counts[cls] += 1
            logger.info("  CODEBRIM/%s/defects: %d images", split_src, len(images))

        # Background images → no_defect
        bg_dir = codebrim_dir / split_src / "background"
        if bg_dir.exists():
            split_dst = "val" if split_src == "test" else split_src
            bg_images = sorted(
                list(bg_dir.glob("*.png")) + list(bg_dir.glob("*.jpg"))
            )
            dst_dir = output_dir / split_dst / "no_defect"
            dst_dir.mkdir(parents=True, exist_ok=True)
            for img in bg_images:
                dst = dst_dir / f"codebrim_bg_{img.name}"
                if not dst.exists():
                    shutil.copy2(img, dst)
                total_counts["no_defect"] += 1
            logger.info("  CODEBRIM/%s/background: %d images → no_defect", split_src, len(bg_images))

    logger.info("  CODEBRIM totals: %s (multi-label: %d → rarest-class)", dict(total_counts), multi_label_count)
    return dict(total_counts)


def prepare_sdnet(
    sdnet_dir: Path,
    output_dir: Path,
    train_ratio: float = 0.8,
    seed: int = 42,
) -> dict[str, int]:
    """Prepare SDNET2018 dataset (v1.1: includes uncracked as no_defect)."""
    logger.info("Processing SDNET2018 from %s", sdnet_dir)

    if not sdnet_dir.exists():
        logger.info("  SDNET2018 not found, skipping")
        return {}

    root_dir = sdnet_dir
    nested = sdnet_dir / "SDNET2018"
    if nested.exists():
        root_dir = nested

    total_counts: Counter = Counter()
    random.seed(seed)

    for surface in ["D", "P", "W"]:
        surface_dir = root_dir / surface
        if not surface_dir.exists():
            continue
        for subdir in sorted(surface_dir.iterdir()):
            if not subdir.is_dir():
                continue
            key = subdir.name
            target_cls = _SDNET_MAP.get(key)
            if target_cls is None:
                continue

            images = sorted(
                list(subdir.glob("*.jpg"))
                + list(subdir.glob("*.png"))
                + list(subdir.glob("*.bmp"))
            )
            random.shuffle(images)
            split_idx = int(len(images) * train_ratio)

            for split_name, split_imgs in [
                ("train", images[:split_idx]),
                ("val", images[split_idx:]),
            ]:
                dst_dir = output_dir / split_name / target_cls
                dst_dir.mkdir(parents=True, exist_ok=True)
                for img in split_imgs:
                    dst = dst_dir / f"sdnet_{surface}_{img.name}"
                    if not dst.exists():
                        shutil.copy2(img, dst)
                    total_counts[target_cls] += 1

    logger.info("  SDNET2018: %s", dict(total_counts))
    return dict(total_counts)


def prepare_dacl10k(
    dacl10k_dir: Path,
    output_dir: Path,
    train_ratio: float = 0.8,
    seed: int = 42,
    min_crop_size: int = 64,
    padding_ratio: float = 0.2,
) -> dict[str, int]:
    """Prepare dacl10k bridge inspection dataset (polygon segmentation → crops).

    Reads JSON polygon annotations from annotations/{split}/ and crops
    bounding-box regions from images/{split}/ for classification training.
    Each annotated polygon shape becomes one cropped classification image.
    """
    from PIL import Image

    logger.info("Processing dacl10k from %s", dacl10k_dir)

    if not dacl10k_dir.exists():
        logger.info("  dacl10k not found, skipping")
        return {}

    # Handle nested directory (e.g., dacl10k_v2_devphase/dacl10k_v2_devphase/)
    if (dacl10k_dir / "annotations").exists():
        root_dir = dacl10k_dir
    else:
        # Search one level deep for annotations/
        root_dir = dacl10k_dir
        for sub in dacl10k_dir.iterdir():
            if sub.is_dir() and (sub / "annotations").exists():
                root_dir = sub
                break

    total_counts: Counter = Counter()
    skipped = 0
    random.seed(seed)

    for split_src in ["train", "validation"]:
        ann_dir = root_dir / "annotations" / split_src
        img_dir = root_dir / "images" / split_src
        if not ann_dir.exists() or not img_dir.exists():
            continue
        split_dst = "val" if split_src == "validation" else "train"

        ann_files = sorted(ann_dir.glob("*.json"))
        logger.info("  dacl10k/%s: %d annotation files", split_src, len(ann_files))

        for ann_file in ann_files:
            try:
                ann = json.loads(ann_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            img_name = ann.get("imageName", "")
            img_path = img_dir / img_name
            if not img_path.exists():
                continue
            shapes = ann.get("shapes", [])
            if not shapes:
                continue

            img = None  # lazy-load only if we have valid shapes
            img_w = ann.get("imageWidth", 0)
            img_h = ann.get("imageHeight", 0)

            for si, shape in enumerate(shapes):
                label = shape.get("label", "")
                target_cls = _DACL10K_MAP.get(label)
                if target_cls is None:
                    continue
                points = shape.get("points", [])
                if len(points) < 3:
                    continue

                # Compute tight bbox from polygon points
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                x_min, x_max = min(xs), max(xs)
                y_min, y_max = min(ys), max(ys)
                w = x_max - x_min
                h = y_max - y_min
                if w < min_crop_size / 2 or h < min_crop_size / 2:
                    skipped += 1
                    continue

                # Add padding
                pad_x = w * padding_ratio
                pad_y = h * padding_ratio
                x_min = max(0, x_min - pad_x)
                y_min = max(0, y_min - pad_y)
                x_max = min(img_w, x_max + pad_x)
                y_max = min(img_h, y_max + pad_y)

                # Ensure minimum crop size
                crop_w = x_max - x_min
                crop_h = y_max - y_min
                if crop_w < min_crop_size or crop_h < min_crop_size:
                    skipped += 1
                    continue

                # Lazy-load image
                if img is None:
                    try:
                        img = Image.open(img_path).convert("RGB")
                        img_w, img_h = img.size
                    except Exception:
                        break

                # Crop and save
                crop = img.crop((int(x_min), int(y_min), int(x_max), int(y_max)))
                dst_dir = output_dir / split_dst / target_cls
                dst_dir.mkdir(parents=True, exist_ok=True)
                stem = ann_file.stem
                dst = dst_dir / f"dacl10k_{stem}_s{si}.jpg"
                if not dst.exists():
                    crop.save(dst, "JPEG", quality=95)
                total_counts[target_cls] += 1

    if skipped:
        logger.info("  dacl10k: skipped %d tiny crops (< %dpx)", skipped, min_crop_size)
    logger.info("  dacl10k: %s", dict(total_counts))
    return dict(total_counts)


def prepare_bd3(
    bd3_dir: Path,
    output_dir: Path,
    train_ratio: float = 0.8,
    seed: int = 42,
) -> dict[str, int]:
    """Prepare BD3 (Building Defects Detection Dataset)."""
    logger.info("Processing BD3 from %s", bd3_dir)

    if not bd3_dir.exists():
        logger.info("  BD3 not found, skipping")
        return {}

    total_counts: Counter = Counter()
    random.seed(seed)

    # BD3 may have ImageFolder at top level or nested under a subdirectory
    # Look for class directories matching BD3 class names
    search_dirs = [bd3_dir]
    for sub in bd3_dir.rglob("*"):
        if sub.is_dir() and sub.name.lower() in {k.lower() for k in _BD3_MAP}:
            search_dirs.append(sub.parent)
            break

    root = search_dirs[-1]
    for cls_dir in sorted(root.iterdir()):
        if not cls_dir.is_dir():
            continue
        target_cls = _BD3_MAP.get(cls_dir.name)
        if target_cls is None:
            continue
        images = sorted(
            list(cls_dir.glob("*.jpg")) + list(cls_dir.glob("*.png"))
            + list(cls_dir.glob("*.jpeg"))
        )
        if not images:
            continue
        random.shuffle(images)
        split_idx = int(len(images) * train_ratio)
        for split_name, split_imgs in [
            ("train", images[:split_idx]),
            ("val", images[split_idx:]),
        ]:
            dst_dir = output_dir / split_name / target_cls
            dst_dir.mkdir(parents=True, exist_ok=True)
            for img in split_imgs:
                dst = dst_dir / f"bd3_{img.name}"
                if not dst.exists():
                    shutil.copy2(img, dst)
                total_counts[target_cls] += 1

    logger.info("  BD3: %s", dict(total_counts))
    return dict(total_counts)


def prepare_s2ds(
    s2ds_dir: Path,
    output_dir: Path,
    train_ratio: float = 0.8,
    seed: int = 42,
) -> dict[str, int]:
    """Prepare S2DS (Structural Defects Dataset) from segmentation masks.

    S2DS uses flat numbered images (000.png) with corresponding label masks
    (000_lab.png). Each mask pixel is RGB-coded by defect class. We assign
    each image to the dominant (most-pixel) defect class, or no_defect if
    no defect pixels are present.
    """
    import numpy as np
    from PIL import Image

    logger.info("Processing S2DS from %s", s2ds_dir)

    if not s2ds_dir.exists():
        logger.info("  S2DS not found, skipping")
        return {}

    total_counts: Counter = Counter()
    random.seed(seed)
    skipped = 0

    for split_src in ["train", "val", "test"]:
        split_dir = s2ds_dir / split_src
        if not split_dir.exists():
            continue
        split_dst = "val" if split_src in ("val", "test") else "train"

        # Find all image files that have corresponding _lab.png masks
        label_files = sorted(split_dir.glob("*_lab.png"))
        logger.info("  S2DS/%s: %d label masks found", split_src, len(label_files))

        for lab_path in label_files:
            # Derive image path: 000_lab.png → 000.png
            stem = lab_path.name.replace("_lab.png", "")
            img_path = split_dir / f"{stem}.png"
            if not img_path.exists():
                img_path = split_dir / f"{stem}.jpg"
                if not img_path.exists():
                    skipped += 1
                    continue

            # Read label mask and count pixels per defect class
            try:
                lab = np.array(Image.open(lab_path).convert("RGB"))
            except Exception:
                skipped += 1
                continue

            # Count pixels for each defect color
            class_pixels: dict[str, int] = {}
            for color, cls_name in _S2DS_COLOR_MAP.items():
                mask = np.all(lab == color, axis=2)
                count = int(mask.sum())
                if count > 0:
                    class_pixels[cls_name] = class_pixels.get(cls_name, 0) + count

            # Assign to dominant defect class, or no_defect if no defect pixels
            if class_pixels:
                target_cls = max(class_pixels, key=lambda c: class_pixels[c])
            else:
                target_cls = "no_defect"

            dst_dir = output_dir / split_dst / target_cls
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / f"s2ds_{stem}.png"
            if not dst.exists():
                shutil.copy2(img_path, dst)
            total_counts[target_cls] += 1

    if skipped:
        logger.info("  S2DS: skipped %d images (missing image/mask)", skipped)
    logger.info("  S2DS: %s", dict(total_counts))
    return dict(total_counts)


def prepare_mbdd(
    mbdd_dir: Path,
    output_dir: Path,
    train_ratio: float = 0.8,
    seed: int = 42,
    min_crop_size: int = 64,
    padding_ratio: float = 0.2,
) -> dict[str, int]:
    """Prepare MBDD2025 (VOC XML object detection → crops).

    Reads VOC XML annotations from Annotations/ and crops bounding-box
    regions from JPEGImages/ for classification training.
    """
    import xml.etree.ElementTree as ET
    from PIL import Image

    logger.info("Processing MBDD2025 from %s", mbdd_dir)

    if not mbdd_dir.exists():
        logger.info("  MBDD2025 not found, skipping")
        return {}

    total_counts: Counter = Counter()
    skipped = 0
    random.seed(seed)

    ann_dir = mbdd_dir / "Annotations"
    img_dir = mbdd_dir / "JPEGImages"

    if not ann_dir.exists() or not img_dir.exists():
        logger.warning("  MBDD2025: expected Annotations/ and JPEGImages/ dirs")
        return {}

    ann_files = sorted(ann_dir.glob("*.xml"))
    random.shuffle(ann_files)
    split_idx = int(len(ann_files) * train_ratio)
    split_assignments = {}
    for i, f in enumerate(ann_files):
        split_assignments[f.name] = "train" if i < split_idx else "val"

    logger.info("  MBDD2025: %d annotation files", len(ann_files))

    for ann_file in ann_files:
        try:
            tree = ET.parse(ann_file)
            root = tree.getroot()
        except ET.ParseError:
            continue

        # Find corresponding image
        fn_el = root.find("filename")
        filename = fn_el.text if fn_el is not None else ann_file.stem + ".jpg"
        img_path = img_dir / filename
        if not img_path.exists():
            # MBDD2025: XML filename may lack prefix (e.g., "10001.jpg" vs "Hefei10001.jpg")
            stem = ann_file.stem  # e.g., "Hefei10001"
            img_path = img_dir / (stem + ".jpg")
            if not img_path.exists():
                img_path = img_dir / (stem + ".png")
                if not img_path.exists():
                    continue

        size_el = root.find("size")
        img_w = int(size_el.findtext("width", "0")) if size_el is not None else 0
        img_h = int(size_el.findtext("height", "0")) if size_el is not None else 0

        objects = root.findall("object")
        if not objects:
            continue

        img = None
        split_dst = split_assignments.get(ann_file.name, "train")

        for oi, obj in enumerate(objects):
            name_el = obj.find("name")
            if name_el is None:
                continue
            label = name_el.text or ""
            target_cls = _MBDD_MAP.get(label) or _MBDD_MAP.get(label.lower())
            if target_cls is None:
                continue

            bbox = obj.find("bndbox")
            if bbox is None:
                continue
            try:
                x_min = float(bbox.findtext("xmin", "0"))
                y_min = float(bbox.findtext("ymin", "0"))
                x_max = float(bbox.findtext("xmax", "0"))
                y_max = float(bbox.findtext("ymax", "0"))
            except (ValueError, TypeError):
                continue

            w = x_max - x_min
            h = y_max - y_min
            if w < min_crop_size / 2 or h < min_crop_size / 2:
                skipped += 1
                continue

            # Add padding
            pad_x = w * padding_ratio
            pad_y = h * padding_ratio
            x_min = max(0, x_min - pad_x)
            y_min = max(0, y_min - pad_y)
            x_max = min(img_w if img_w else 99999, x_max + pad_x)
            y_max = min(img_h if img_h else 99999, y_max + pad_y)

            if (x_max - x_min) < min_crop_size or (y_max - y_min) < min_crop_size:
                skipped += 1
                continue

            if img is None:
                try:
                    img = Image.open(img_path).convert("RGB")
                    img_w, img_h = img.size
                    # Re-clamp with actual size
                    x_max = min(img_w, x_max)
                    y_max = min(img_h, y_max)
                except Exception:
                    break

            crop = img.crop((int(x_min), int(y_min), int(x_max), int(y_max)))
            dst_dir = output_dir / split_dst / target_cls
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / f"mbdd_{ann_file.stem}_o{oi}.jpg"
            if not dst.exists():
                crop.save(dst, "JPEG", quality=95)
            total_counts[target_cls] += 1

    if skipped:
        logger.info("  MBDD2025: skipped %d tiny crops (< %dpx)", skipped, min_crop_size)
    logger.info("  MBDD2025: %s", dict(total_counts))
    return dict(total_counts)


def _prepare_binary_dataset(
    src_dir: Path,
    output_dir: Path,
    label_map: dict[str, str],
    prefix: str,
    train_ratio: float = 0.8,
    seed: int = 42,
) -> dict[str, int]:
    """Generic prepare function for binary (Positive/Negative) image datasets."""
    logger.info("Processing %s from %s", prefix, src_dir)

    if not src_dir.exists():
        logger.info("  %s not found, skipping", prefix)
        return {}

    total_counts: Counter = Counter()
    random.seed(seed)

    # Handle nested directory (e.g., Brickwork_cracks_dataset/Brickwork_cracks_dataset/)
    root = src_dir
    for sub in src_dir.iterdir():
        if sub.is_dir() and any((sub / k).is_dir() for k in label_map):
            root = sub
            break

    for cls_name, target_cls in label_map.items():
        cls_dir = root / cls_name
        if not cls_dir.exists():
            continue
        images = sorted(
            list(cls_dir.glob("*.jpg")) + list(cls_dir.glob("*.png"))
            + list(cls_dir.glob("*.jpeg"))
        )
        if not images:
            continue
        random.shuffle(images)
        split_idx = int(len(images) * train_ratio)
        for split_name, split_imgs in [
            ("train", images[:split_idx]),
            ("val", images[split_idx:]),
        ]:
            dst_dir = output_dir / split_name / target_cls
            dst_dir.mkdir(parents=True, exist_ok=True)
            for img in split_imgs:
                dst = dst_dir / f"{prefix}_{img.name}"
                if not dst.exists():
                    shutil.copy2(img, dst)
                total_counts[target_cls] += 1

    logger.info("  %s: %s", prefix, dict(total_counts))
    return dict(total_counts)


def prepare_brickwork(
    brickwork_dir: Path,
    output_dir: Path,
    train_ratio: float = 0.8,
    seed: int = 42,
) -> dict[str, int]:
    """Prepare Brickwork Cracks dataset (binary: Positive/Negative)."""
    return _prepare_binary_dataset(
        brickwork_dir, output_dir, _BRICKWORK_MAP, "brickwork", train_ratio, seed,
    )


def prepare_historical(
    historical_dir: Path,
    output_dir: Path,
    train_ratio: float = 0.8,
    seed: int = 42,
) -> dict[str, int]:
    """Prepare Historical Building Cracks dataset (binary: crack/non-crack)."""
    return _prepare_binary_dataset(
        historical_dir, output_dir, _HISTORICAL_MAP, "historical", train_ratio, seed,
    )


def _cap_class_images(data_dir: Path, max_per_class: int, seed: int = 42) -> dict[str, int]:
    """Randomly subsample classes exceeding max_per_class."""
    random.seed(seed)
    capped: dict[str, int] = {}
    for split in ["train", "val"]:
        split_dir = data_dir / split
        if not split_dir.exists():
            continue
        for cls_dir in sorted(split_dir.iterdir()):
            if not cls_dir.is_dir():
                continue
            images = sorted(cls_dir.iterdir())
            if len(images) > max_per_class:
                random.shuffle(images)
                removed = 0
                for img in images[max_per_class:]:
                    img.unlink()
                    removed += 1
                capped[f"{split}/{cls_dir.name}"] = removed
                logger.info("  Capped %s/%s: %d → %d (removed %d)",
                            split, cls_dir.name, len(images), max_per_class, removed)
    return capped


def prepare_combined_dataset(
    mendeley_dir: Path | None,
    codebrim_dir: Path | None,
    sdnet_dir: Path | None,
    output_dir: Path,
    train_ratio: float = 0.8,
    dacl10k_dir: Path | None = None,
    bd3_dir: Path | None = None,
    s2ds_dir: Path | None = None,
    mbdd_dir: Path | None = None,
    brickwork_dir: Path | None = None,
    historical_dir: Path | None = None,
    max_per_class: int = MAX_IMAGES_PER_CLASS,
) -> Path:
    """Prepare combined defect classification dataset (v1.1).

    Creates ImageFolder-compatible structure:
        output_dir/data/train/{class_name}/images...
        output_dir/data/val/{class_name}/images...
    """
    data_dir = output_dir / "data"

    # Ensure all class directories exist
    for split in ["train", "val"]:
        for cls in DEFECT_CLASS_NAMES:
            (data_dir / split / cls).mkdir(parents=True, exist_ok=True)

    all_counts: Counter = Counter()

    # Process each dataset
    if mendeley_dir and mendeley_dir.exists():
        counts = prepare_mendeley(mendeley_dir, data_dir, train_ratio)
        all_counts.update(counts)

    if codebrim_dir and codebrim_dir.exists():
        counts = prepare_codebrim(codebrim_dir, data_dir)
        all_counts.update(counts)

    if sdnet_dir and sdnet_dir.exists():
        counts = prepare_sdnet(sdnet_dir, data_dir, train_ratio)
        all_counts.update(counts)

    if dacl10k_dir and dacl10k_dir.exists():
        counts = prepare_dacl10k(dacl10k_dir, data_dir, train_ratio)
        all_counts.update(counts)

    if bd3_dir and bd3_dir.exists():
        counts = prepare_bd3(bd3_dir, data_dir, train_ratio)
        all_counts.update(counts)

    if s2ds_dir and s2ds_dir.exists():
        counts = prepare_s2ds(s2ds_dir, data_dir, train_ratio)
        all_counts.update(counts)

    if mbdd_dir and mbdd_dir.exists():
        counts = prepare_mbdd(mbdd_dir, data_dir, train_ratio)
        all_counts.update(counts)

    if brickwork_dir and brickwork_dir.exists():
        counts = prepare_brickwork(brickwork_dir, data_dir, train_ratio)
        all_counts.update(counts)

    if historical_dir and historical_dir.exists():
        counts = prepare_historical(historical_dir, data_dir, train_ratio)
        all_counts.update(counts)

    # Cap oversized classes
    _cap_class_images(data_dir, max_per_class)

    # Log class distribution
    logger.info("Combined dataset class distribution (v1.1):")
    for cls in DEFECT_CLASS_NAMES:
        count = all_counts.get(cls, 0)
        scarce = " (SCARCE)" if cls in SCARCE_CLASSES else ""
        structural = " [STRUCTURAL]" if cls in STRUCTURAL_CLASSES else ""
        logger.info("  %s: %d%s%s", cls, count, structural, scarce)

    # Count actual per-split (after capping)
    for split in ["train", "val"]:
        total = 0
        for cls_dir in sorted((data_dir / split).iterdir()):
            if cls_dir.is_dir():
                n = len(list(cls_dir.iterdir()))
                total += n
        logger.info("%s split: %d images", split, total)

    # Save stats
    datasets_used = []
    if mendeley_dir and mendeley_dir.exists():
        datasets_used.append("mendeley_cracks")
    if codebrim_dir and codebrim_dir.exists():
        datasets_used.append("codebrim")
    if sdnet_dir and sdnet_dir.exists():
        datasets_used.append("sdnet2018")
    if dacl10k_dir and dacl10k_dir.exists():
        datasets_used.append("dacl10k")
    if bd3_dir and bd3_dir.exists():
        datasets_used.append("bd3")
    if s2ds_dir and s2ds_dir.exists():
        datasets_used.append("s2ds")
    if mbdd_dir and mbdd_dir.exists():
        datasets_used.append("mbdd2025")
    if brickwork_dir and brickwork_dir.exists():
        datasets_used.append("brickwork")
    if historical_dir and historical_dir.exists():
        datasets_used.append("historical")

    stats = {
        "version": "1.1",
        "total_by_class": dict(all_counts),
        "datasets_used": datasets_used,
        "max_per_class": max_per_class,
        "num_classes": len(DEFECT_CLASS_NAMES),
        "class_names": DEFECT_CLASS_NAMES,
    }
    with open(output_dir / "dataset_stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    # Remove empty class directories (ImageFolder crashes on them)
    removed_classes: list[str] = []
    for split in ["train", "val"]:
        for cls in DEFECT_CLASS_NAMES:
            cls_dir = data_dir / split / cls
            if cls_dir.exists() and not any(cls_dir.iterdir()):
                cls_dir.rmdir()
                if cls not in removed_classes:
                    removed_classes.append(cls)
    if removed_classes:
        logger.warning(
            "Removed %d empty class dirs (no data): %s",
            len(removed_classes), removed_classes,
        )

    return data_dir


# ---------------------------------------------------------------------------
# FocalLoss with label smoothing
# ---------------------------------------------------------------------------


class FocalLoss:
    """Focal loss with label smoothing for noisy/imbalanced data.

    Combines:
    - Focal weighting: (1-p_t)^gamma down-weights easy examples
    - Label smoothing: prevents overconfidence on noisy labels
    - Class weights: corrects for class imbalance
    """

    def __init__(self, alpha: "torch.Tensor | None" = None, gamma: float = 2.0,
                 label_smoothing: float = 0.1):
        self.gamma = gamma
        self.label_smoothing = label_smoothing
        self.alpha = alpha

    def __call__(self, logits: "torch.Tensor", targets: "torch.Tensor") -> "torch.Tensor":
        import torch
        import torch.nn.functional as F

        num_classes = logits.size(1)
        log_probs = F.log_softmax(logits, dim=1)
        probs = torch.exp(log_probs)

        # Create smoothed one-hot targets
        with torch.no_grad():
            smooth_targets = torch.zeros_like(logits)
            smooth_targets.scatter_(1, targets.unsqueeze(1), 1.0)
            smooth_targets = (
                smooth_targets * (1 - self.label_smoothing)
                + self.label_smoothing / num_classes
            )

        # Focal weight
        focal_weight = (1 - probs) ** self.gamma
        loss = -smooth_targets * focal_weight * log_probs

        if self.alpha is not None:
            alpha_weight = self.alpha[targets].unsqueeze(1)
            loss = loss * alpha_weight

        return loss.sum(dim=1).mean()


# ---------------------------------------------------------------------------
# Class-aware dataset wrapper
# ---------------------------------------------------------------------------


class ClassAwareDataset:  # type: ignore[type-arg]  # duck-typed Dataset
    """Wraps ImageFolder to apply different transforms per class."""

    def __init__(self, base_dataset, scarce_indices: set[int],
                 scarce_transform, normal_transform):
        self.base = base_dataset
        self.scarce_indices = scarce_indices
        self.scarce_transform = scarce_transform
        self.normal_transform = normal_transform
        self.targets = base_dataset.targets
        self.classes = base_dataset.classes
        self.class_to_idx = base_dataset.class_to_idx

    def __len__(self):
        return len(self.base)

    def __getitem__(self, idx):
        from PIL import Image
        path, label = self.base.samples[idx]
        img = Image.open(path).convert("RGB")
        if label in self.scarce_indices:
            img = self.scarce_transform(img)
        else:
            img = self.normal_transform(img)
        return img, label


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train_defect_classifier(
    data_dir: Path,
    output_dir: Path,
    epochs: int = 50,
    batch_size: int = 32,
    learning_rate: float = 5e-5,
    weight_decay: float = 0.05,
    warmup_epochs: int = 3,
    patience: int = 10,
    freeze_layers: int = 6,
    device: str = "auto",
    use_mixup: bool = True,
) -> dict:
    """Fine-tune ViT-B/16 for construction defect classification (v1.1).

    Key changes from v1.0:
    - FocalLoss(gamma=2.0) + label_smoothing(0.1)
    - Mixup/CutMix augmentation
    - Class-specific transforms for scarce classes
    - 6 frozen blocks (was 8)
    - Early stopping on macro_f1 only
    - Gradient clipping (max_norm=1.0)
    """
    import torch
    import torch.nn.functional as F
    from torch.cuda.amp import GradScaler, autocast
    from torch.utils.data import DataLoader, WeightedRandomSampler
    from torchvision import datasets, transforms

    import timm

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    dev = torch.device(device)
    logger.info("Training on device: %s", dev)

    use_amp = dev.type == "cuda"
    scaler = GradScaler(enabled=use_amp)

    # --- Transforms ---
    normal_transform = transforms.Compose([
        transforms.Resize((256, 256)),
        transforms.RandomCrop(224),
        transforms.RandomHorizontalFlip(p=0.5),
        transforms.RandomVerticalFlip(p=0.2),
        transforms.RandomRotation(30),
        transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.2, hue=0.05),
        transforms.RandomGrayscale(p=0.1),
        transforms.GaussianBlur(kernel_size=3, sigma=(0.1, 2.0)),
        transforms.RandomAffine(degrees=0, translate=(0.1, 0.1), scale=(0.8, 1.2)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        transforms.RandomErasing(p=0.1),
    ])

    scarce_transform = transforms.Compose([
        transforms.Resize((256, 256)),
        transforms.RandomCrop(224),
        transforms.RandomHorizontalFlip(p=0.5),
        transforms.RandomVerticalFlip(p=0.3),
        transforms.RandomRotation(45),
        transforms.ColorJitter(brightness=0.4, contrast=0.4, saturation=0.3, hue=0.1),
        transforms.RandomGrayscale(p=0.2),
        transforms.GaussianBlur(kernel_size=3, sigma=(0.1, 2.0)),
        transforms.RandomPerspective(distortion_scale=0.3, p=0.3),
        transforms.RandomAffine(degrees=0, translate=(0.15, 0.15), scale=(0.7, 1.3)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        transforms.RandomErasing(p=0.2),
    ])

    val_transform = transforms.Compose([
        transforms.Resize((256, 256)),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    # --- Datasets ---
    train_dir = data_dir / "train"
    val_dir = data_dir / "val"

    base_train_dataset = datasets.ImageFolder(str(train_dir))
    val_dataset = datasets.ImageFolder(str(val_dir), transform=val_transform)

    num_classes = len(base_train_dataset.classes)
    if num_classes < len(DEFECT_CLASS_NAMES):
        missing = set(DEFECT_CLASS_NAMES) - set(base_train_dataset.classes)
        logger.warning(
            "Missing classes (no training data): %s — training with %d/%d classes",
            missing, num_classes, len(DEFECT_CLASS_NAMES),
        )
    logger.info("Classes (%d): %s", num_classes, base_train_dataset.classes)

    # Identify scarce class indices for class-aware augmentation
    scarce_indices = {
        base_train_dataset.class_to_idx[c]
        for c in SCARCE_CLASSES
        if c in base_train_dataset.class_to_idx
    }

    train_dataset = ClassAwareDataset(
        base_train_dataset, scarce_indices, scarce_transform, normal_transform,
    )

    # --- Weighted sampling for class imbalance ---
    class_counts = Counter(base_train_dataset.targets)
    total_samples = len(base_train_dataset.targets)
    class_weights = {
        cls: total_samples / (num_classes * count)
        for cls, count in class_counts.items()
    }
    sample_weights = [class_weights[t] for t in base_train_dataset.targets]
    sampler = WeightedRandomSampler(sample_weights, len(sample_weights))

    train_loader = DataLoader(
        train_dataset, batch_size=batch_size, sampler=sampler,
        num_workers=4, pin_memory=True, drop_last=True,
    )
    val_loader = DataLoader(
        val_dataset, batch_size=batch_size, shuffle=False,
        num_workers=4, pin_memory=True,
    )

    # --- Model ---
    model = timm.create_model("vit_base_patch16_224", pretrained=True, num_classes=num_classes)
    model = model.to(dev)

    # Freeze first N transformer blocks
    for name, param in model.named_parameters():
        if "blocks." in name:
            block_idx = int(name.split("blocks.")[1].split(".")[0])
            if block_idx < freeze_layers:
                param.requires_grad = False
        elif "head" not in name and "norm" not in name:
            param.requires_grad = False

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total_params = sum(p.numel() for p in model.parameters())
    logger.info("Parameters: %d total, %d trainable (%.1f%%)",
                total_params, trainable, 100 * trainable / total_params)

    # --- FocalLoss with label smoothing ---
    weight_tensor = torch.tensor(
        [class_weights.get(i, 1.0) for i in range(num_classes)],
        dtype=torch.float32,
    ).to(dev)
    criterion = FocalLoss(alpha=weight_tensor, gamma=2.0, label_smoothing=0.1)

    # Standard CE for validation (no focal/smoothing needed for eval)
    val_criterion = torch.nn.CrossEntropyLoss()

    # --- Mixup/CutMix ---
    mixup_fn = None
    if use_mixup:
        try:
            from timm.data import Mixup
            mixup_fn = Mixup(
                mixup_alpha=0.8, cutmix_alpha=1.0, cutmix_minmax=None,
                prob=0.5, switch_prob=0.5, mode="batch",
                label_smoothing=0.1, num_classes=num_classes,
            )
            logger.info("Mixup/CutMix enabled (alpha=0.8/1.0)")
        except ImportError:
            logger.warning("timm.data.Mixup not available, skipping")

    # --- Optimizer ---
    optimizer = torch.optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=learning_rate, weight_decay=weight_decay,
    )

    # --- Scheduler: cosine with linear warmup ---
    total_steps = epochs * len(train_loader)
    warmup_steps = warmup_epochs * len(train_loader)

    def lr_lambda(step: int) -> float:
        if step < warmup_steps:
            return step / max(warmup_steps, 1)
        progress = (step - warmup_steps) / max(total_steps - warmup_steps, 1)
        return 0.5 * (1.0 + math.cos(math.pi * progress))

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)

    # --- Training loop ---
    output_dir.mkdir(parents=True, exist_ok=True)
    best_val_f1 = 0.0
    best_val_acc = 0.0
    epochs_without_improvement = 0
    history: list[dict] = []

    for epoch in range(epochs):
        # --- Train ---
        model.train()
        train_loss = 0.0
        train_correct = 0
        train_total = 0

        for images, labels in train_loader:
            images, labels = images.to(dev), labels.to(dev)
            optimizer.zero_grad()

            # Apply Mixup/CutMix
            mixed_labels = None
            if mixup_fn is not None:
                images, mixed_labels = mixup_fn(images, labels)

            with autocast(enabled=use_amp):
                outputs = model(images)
                if mixed_labels is not None:
                    # Mixup produces soft labels — use timm's soft target CE
                    log_probs = F.log_softmax(outputs, dim=1)
                    loss = -(mixed_labels * log_probs).sum(dim=1).mean()
                else:
                    loss = criterion(outputs, labels)

            scaler.scale(loss).backward()
            # Gradient clipping
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            scaler.step(optimizer)
            scaler.update()
            scheduler.step()

            train_loss += loss.item() * images.size(0)
            _, predicted = torch.max(outputs, 1)
            if mixed_labels is None:
                train_total += labels.size(0)
                train_correct += (predicted == labels).sum().item()
            else:
                # For mixup, compare to original label (argmax of mixed_labels)
                orig_labels = mixed_labels.argmax(dim=1)
                train_total += orig_labels.size(0)
                train_correct += (predicted == orig_labels).sum().item()

        train_acc = train_correct / max(train_total, 1)
        avg_train_loss = train_loss / max(train_total, 1)

        # --- Validate ---
        model.eval()
        val_loss = 0.0
        val_correct = 0
        val_total = 0
        all_preds: list[int] = []
        all_labels: list[int] = []

        with torch.no_grad():
            for images, labels in val_loader:
                images, labels = images.to(dev), labels.to(dev)
                with autocast(enabled=use_amp):
                    outputs = model(images)
                    loss = val_criterion(outputs, labels)
                val_loss += loss.item() * images.size(0)
                _, predicted = torch.max(outputs, 1)
                val_total += labels.size(0)
                val_correct += (predicted == labels).sum().item()
                all_preds.extend(predicted.cpu().tolist())
                all_labels.extend(labels.cpu().tolist())

        val_acc = val_correct / max(val_total, 1)
        avg_val_loss = val_loss / max(val_total, 1)

        per_class_f1 = _compute_per_class_f1(all_labels, all_preds, num_classes)
        macro_f1 = sum(per_class_f1.values()) / max(len(per_class_f1), 1)

        epoch_info = {
            "epoch": epoch + 1,
            "train_loss": round(avg_train_loss, 4),
            "train_acc": round(train_acc, 4),
            "val_loss": round(avg_val_loss, 4),
            "val_acc": round(val_acc, 4),
            "macro_f1": round(macro_f1, 4),
            "lr": optimizer.param_groups[0]["lr"],
        }
        history.append(epoch_info)

        logger.info(
            "Epoch %d/%d — loss=%.4f acc=%.4f val_loss=%.4f val_acc=%.4f F1=%.4f lr=%.6f",
            epoch + 1, epochs, avg_train_loss, train_acc,
            avg_val_loss, val_acc, macro_f1, optimizer.param_groups[0]["lr"],
        )

        # Save best model (macro_f1 only — balanced across all classes)
        if macro_f1 > best_val_f1:
            best_val_f1 = macro_f1
            best_val_acc = val_acc
            epochs_without_improvement = 0
            torch.save(model.state_dict(), output_dir / "best_model.pth")
            logger.info("  Best model saved (F1=%.4f, acc=%.4f)", macro_f1, val_acc)
        else:
            epochs_without_improvement += 1

        if epochs_without_improvement >= patience:
            logger.info("Early stopping at epoch %d (patience=%d)", epoch + 1, patience)
            break

    # Save class mapping
    with open(output_dir / "class_mapping.txt", "w") as f:
        for idx, cls_name in enumerate(base_train_dataset.classes):
            f.write(f"{idx}\t{cls_name}\n")

    # Save training history
    with open(output_dir / "training_history.json", "w") as f:
        json.dump(history, f, indent=2)

    return {
        "best_val_acc": best_val_acc,
        "best_macro_f1": best_val_f1,
        "epochs_trained": len(history),
        "num_classes": num_classes,
        "class_names": list(base_train_dataset.classes),
        "model_path": str(output_dir / "best_model.pth"),
        "history": history,
    }


def _compute_per_class_f1(
    labels: list[int], preds: list[int], num_classes: int,
) -> dict[int, float]:
    """Compute per-class F1 scores."""
    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()

    for true, pred in zip(labels, preds):
        if true == pred:
            tp[true] += 1
        else:
            fp[pred] += 1
            fn[true] += 1

    f1_scores: dict[int, float] = {}
    for cls in range(num_classes):
        precision = tp[cls] / max(tp[cls] + fp[cls], 1)
        recall = tp[cls] / max(tp[cls] + fn[cls], 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-8)
        f1_scores[cls] = round(f1, 4)

    return f1_scores


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_model(
    model_path: Path,
    data_dir: Path,
    device: str = "auto",
) -> dict:
    """Evaluate the trained model on the validation set.

    Returns accuracy, per-class F1, confusion matrix, per-source accuracy.
    """
    import torch
    from torch.utils.data import DataLoader
    from torchvision import datasets, transforms

    import timm

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    dev = torch.device(device)

    val_transform = transforms.Compose([
        transforms.Resize((256, 256)),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    val_dir = data_dir / "val"
    val_dataset = datasets.ImageFolder(str(val_dir), transform=val_transform)
    num_classes = len(val_dataset.classes)

    val_loader = DataLoader(
        val_dataset, batch_size=64, shuffle=False, num_workers=4, pin_memory=True,
    )

    model = timm.create_model("vit_base_patch16_224", pretrained=False, num_classes=num_classes)
    state = torch.load(str(model_path), map_location=dev, weights_only=True)
    model.load_state_dict(state)
    model = model.to(dev)
    model.eval()

    all_preds: list[int] = []
    all_labels: list[int] = []
    all_probs: list[list[float]] = []
    correct = 0
    total = 0
    top2_correct = 0

    with torch.no_grad():
        for images, labels in val_loader:
            images, labels = images.to(dev), labels.to(dev)
            outputs = model(images)
            probs = torch.softmax(outputs, dim=1)
            _, predicted = torch.max(outputs, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()

            # Top-2 accuracy
            _, top2 = torch.topk(outputs, 2, dim=1)
            for i in range(labels.size(0)):
                if labels[i] in top2[i]:
                    top2_correct += 1

            all_preds.extend(predicted.cpu().tolist())
            all_labels.extend(labels.cpu().tolist())
            all_probs.extend(probs.cpu().tolist())

    accuracy = correct / max(total, 1)
    top2_accuracy = top2_correct / max(total, 1)
    per_class_f1 = _compute_per_class_f1(all_labels, all_preds, num_classes)

    # Per-class precision and recall
    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()
    for true, pred in zip(all_labels, all_preds):
        if true == pred:
            tp[true] += 1
        else:
            fp[pred] += 1
            fn[true] += 1

    per_class: list[dict] = []
    for cls_idx in range(num_classes):
        cls_name = val_dataset.classes[cls_idx]
        precision = tp[cls_idx] / max(tp[cls_idx] + fp[cls_idx], 1)
        recall = tp[cls_idx] / max(tp[cls_idx] + fn[cls_idx], 1)
        per_class.append({
            "class": cls_name,
            "class_id": cls_idx,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": per_class_f1.get(cls_idx, 0.0),
            "support": tp[cls_idx] + fn[cls_idx],
        })

    macro_f1 = sum(per_class_f1.values()) / max(len(per_class_f1), 1)

    # Confusion matrix
    confusion = [[0] * num_classes for _ in range(num_classes)]
    for true, pred in zip(all_labels, all_preds):
        confusion[true][pred] += 1

    # Per-source accuracy (parse filename prefixes)
    source_correct: Counter = Counter()
    source_total: Counter = Counter()
    for idx, (path, label) in enumerate(val_dataset.samples):
        fname = Path(path).name
        pred = all_preds[idx]
        source = fname.split("_")[0] if "_" in fname else "unknown"
        source_total[source] += 1
        if pred == label:
            source_correct[source] += 1
    per_source = {
        src: round(source_correct[src] / max(source_total[src], 1), 4)
        for src in sorted(source_total)
    }

    # Check targets
    targets = {
        "accuracy_target": 0.70,
        "accuracy_met": accuracy > 0.70,
        "macro_f1_target": 0.65,
        "macro_f1_met": macro_f1 > 0.65,
        "structural_f1_target": 0.70,
    }
    for cls in per_class:
        if cls["class"] in STRUCTURAL_CLASSES:
            targets[f"{cls['class']}_f1_met"] = cls["f1"] > 0.70

    results = {
        "accuracy": round(accuracy, 4),
        "top2_accuracy": round(top2_accuracy, 4),
        "macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "confusion_matrix": confusion,
        "per_source_accuracy": per_source,
        "targets": targets,
        "total_evaluated": total,
    }

    logger.info("=== Evaluation Results (v1.1) ===")
    logger.info("Accuracy: %.4f (target > 0.70)", accuracy)
    logger.info("Top-2 Accuracy: %.4f", top2_accuracy)
    logger.info("Macro F1: %.4f (target > 0.65)", macro_f1)
    for cls in per_class:
        structural = " [STRUCTURAL]" if cls["class"] in STRUCTURAL_CLASSES else ""
        logger.info("  %s: P=%.3f R=%.3f F1=%.3f (n=%d)%s",
                    cls["class"], cls["precision"], cls["recall"],
                    cls["f1"], cls["support"], structural)
    logger.info("Per-source accuracy: %s", per_source)

    return results


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_model(
    model_path: Path,
    output_dir: Path,
    num_classes: int = 8,
    export_onnx: bool = True,
) -> dict:
    """Export model to PyTorch and ONNX formats."""
    import torch

    import timm

    exports: dict[str, str] = {}

    # Auto-detect num_classes from checkpoint
    state = torch.load(str(model_path), map_location="cpu", weights_only=True)
    if "head.weight" in state:
        num_classes = state["head.weight"].shape[0]

    model = timm.create_model("vit_base_patch16_224", pretrained=False, num_classes=num_classes)
    model.load_state_dict(state)
    model.eval()

    pt_path = output_dir / "defect_vit_b16.pth"
    torch.save(model.state_dict(), pt_path)
    exports["pytorch"] = str(pt_path)
    logger.info("PyTorch model saved: %s", pt_path)

    if export_onnx:
        try:
            onnx_path = output_dir / "defect_vit_b16.onnx"
            dummy_input = torch.randn(1, 3, 224, 224)
            torch.onnx.export(
                model, dummy_input, str(onnx_path),
                input_names=["image"], output_names=["logits"],
                dynamic_axes={"image": {0: "batch_size"}, "logits": {0: "batch_size"}},
                opset_version=17,
            )
            exports["onnx"] = str(onnx_path)
            logger.info("ONNX model exported: %s", onnx_path)
        except Exception as exc:
            logger.warning("ONNX export failed: %s", exc)
            exports["onnx"] = "failed"

    return {"status": "completed", "exports": exports}


# ---------------------------------------------------------------------------
# Save to model registry
# ---------------------------------------------------------------------------


def save_to_registry(
    model_path: Path,
    eval_results: dict,
    registry_dir: Path,
    class_names: list[str] | None = None,
) -> Path:
    """Copy model and metadata to the model registry."""
    registry_dir.mkdir(parents=True, exist_ok=True)

    dest = registry_dir / "best_model.pth"
    if model_path.exists() and model_path.resolve() != dest.resolve():
        shutil.copy2(model_path, dest)

    if class_names is None:
        class_names = DEFECT_CLASS_NAMES

    metadata = {
        "model_name": "constructai_defect_vit",
        "version": "1.1",
        "base_model": "vit_base_patch16_224",
        "input_size": 224,
        "num_classes": len(class_names),
        "class_names": class_names,
        "evaluation": eval_results,
        "training_datasets": [
            "mendeley_cracks", "codebrim", "sdnet2018",
            "dacl10k", "bd3", "s2ds", "mbdd2025",
        ],
    }

    with open(registry_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    with open(registry_dir / "class_mapping.txt", "w") as f:
        for i, name in enumerate(class_names):
            f.write(f"{i}\t{name}\n")

    logger.info("Model registry saved to %s", registry_dir)
    return registry_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="ViT defect classifier training pipeline (v1.1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Existing dataset dirs
    parser.add_argument("--mendeley-dir", type=Path,
                        default=Path.home() / "constructai-data/cv-training/mendeley-cracks")
    parser.add_argument("--codebrim-dir", type=Path,
                        default=Path.home() / "constructai-data/cv-training/codebrim")
    parser.add_argument("--sdnet-dir", type=Path,
                        default=Path.home() / "constructai-data/cv-training/sdnet2018")
    # New dataset dirs (v1.1)
    parser.add_argument("--dacl10k-dir", type=Path, default=None,
                        help="dacl10k bridge inspection dataset")
    parser.add_argument("--bd3-dir", type=Path, default=None,
                        help="BD3 building defects dataset")
    parser.add_argument("--s2ds-dir", type=Path, default=None,
                        help="S2DS structural defects dataset")
    parser.add_argument("--mbdd-dir", type=Path, default=None,
                        help="MBDD2025 multi-scene building defect dataset")
    parser.add_argument("--brickwork-dir", type=Path, default=None,
                        help="Brickwork cracks dataset (binary)")
    parser.add_argument("--historical-dir", type=Path, default=None,
                        help="Historical Building Cracks dataset")

    parser.add_argument("--output-dir", type=Path, default=Path("models/defect_vit_v1.1"))
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--lr", type=float, default=5e-5)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--freeze-layers", type=int, default=6,
                        help="Number of ViT blocks to freeze (out of 12)")
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--max-per-class", type=int, default=MAX_IMAGES_PER_CLASS)
    parser.add_argument("--no-mixup", action="store_true", help="Disable Mixup/CutMix")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--skip-prepare", action="store_true")
    parser.add_argument("--skip-export", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Step 1: Data preparation
    data_dir = args.output_dir / "data"

    if not args.skip_prepare:
        logger.info("=" * 60)
        logger.info("STEP 1: Dataset Preparation (v1.1)")
        logger.info("=" * 60)
        data_dir = prepare_combined_dataset(
            mendeley_dir=args.mendeley_dir if args.mendeley_dir and args.mendeley_dir.exists() else None,
            codebrim_dir=args.codebrim_dir if args.codebrim_dir and args.codebrim_dir.exists() else None,
            sdnet_dir=args.sdnet_dir if args.sdnet_dir and args.sdnet_dir.exists() else None,
            output_dir=args.output_dir,
            dacl10k_dir=args.dacl10k_dir if args.dacl10k_dir and args.dacl10k_dir.exists() else None,
            bd3_dir=args.bd3_dir if args.bd3_dir and args.bd3_dir.exists() else None,
            s2ds_dir=args.s2ds_dir if args.s2ds_dir and args.s2ds_dir.exists() else None,
            mbdd_dir=args.mbdd_dir if args.mbdd_dir and args.mbdd_dir.exists() else None,
            brickwork_dir=args.brickwork_dir if args.brickwork_dir and args.brickwork_dir.exists() else None,
            historical_dir=args.historical_dir if args.historical_dir and args.historical_dir.exists() else None,
            max_per_class=args.max_per_class,
        )

    if args.prepare_only:
        logger.info("Data preparation complete. Exiting (--prepare-only).")
        return

    if not (data_dir / "train").exists():
        logger.error("Training data not found at %s. Run without --skip-prepare.", data_dir)
        return

    # Step 2: Training
    logger.info("=" * 60)
    logger.info("STEP 2: Training ViT-B/16 Defect Classifier (v1.1)")
    logger.info("=" * 60)
    train_results = train_defect_classifier(
        data_dir=data_dir,
        output_dir=args.output_dir,
        epochs=args.epochs,
        batch_size=args.batch,
        learning_rate=args.lr,
        patience=args.patience,
        freeze_layers=args.freeze_layers,
        device=args.device,
        use_mixup=not args.no_mixup,
    )
    logger.info("Training: best_f1=%.4f", train_results["best_macro_f1"])

    best_model = args.output_dir / "best_model.pth"
    if not best_model.exists():
        logger.error("Best model not found")
        return

    # Step 3: Evaluation
    logger.info("=" * 60)
    logger.info("STEP 3: Evaluation")
    logger.info("=" * 60)
    eval_results = evaluate_model(best_model, data_dir, device=args.device)

    # Step 4: Export
    if not args.skip_export:
        logger.info("=" * 60)
        logger.info("STEP 4: Exporting Models")
        logger.info("=" * 60)
        export_model(best_model, args.output_dir)

    # Step 5: Save to registry
    logger.info("=" * 60)
    logger.info("STEP 5: Saving to Model Registry")
    logger.info("=" * 60)
    save_to_registry(
        best_model, eval_results, args.output_dir,
        class_names=train_results.get("class_names"),
    )

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE (v1.1)")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
