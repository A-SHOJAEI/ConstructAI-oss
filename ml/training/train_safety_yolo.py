"""YOLO safety detection model training pipeline.

Combines SODA and Roboflow Safety datasets into a unified 13-class
taxonomy, performs stratified train/val/test splitting, trains YOLOv8-L
at 1280px, evaluates per-class performance, and exports ONNX + TensorRT.

Datasets:
    - SODA (Site Object Detection dAtaset) — ~/constructai-data/cv-training/soda/
    - Roboflow Safety — ~/constructai-data/cv-training/roboflow-safety/

Targets:
    - mAP@0.5 > 0.75 overall
    - person AP > 0.85
    - hard_hat / no_hard_hat AP > 0.70
    - safety_vest / no_vest AP > 0.70
    - Recall for no_hard_hat and no_vest > 0.80

Usage:
    # Full pipeline: prepare + train + evaluate + export
    python -m ml.training.train_safety_yolo \\
        --soda-dir ~/constructai-data/cv-training/soda/ \\
        --roboflow-dir ~/constructai-data/cv-training/roboflow-safety/ \\
        --output-dir constructai_safety

    # Data preparation only (no GPU needed)
    python -m ml.training.train_safety_yolo \\
        --soda-dir ~/constructai-data/cv-training/soda/ \\
        --roboflow-dir ~/constructai-data/cv-training/roboflow-safety/ \\
        --output-dir constructai_safety --prepare-only

    # Train on 2x3090 system (multi-GPU)
    python -m ml.training.train_safety_yolo \\
        --output-dir constructai_safety --device 0,1 --batch 16

    # Resume from checkpoint
    python -m ml.training.train_safety_yolo \\
        --output-dir constructai_safety --resume runs/detect/train/weights/last.pt
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import shutil
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unified safety class taxonomy
# ---------------------------------------------------------------------------

SAFETY_CLASSES: dict[int, str] = {
    0: "person",
    1: "hard_hat",
    2: "no_hard_hat",
    3: "safety_vest",
    4: "no_vest",
    5: "excavator",
    6: "crane",
    7: "loader",
    8: "dump_truck",
    9: "concrete_mixer",
    10: "scaffolding",
    11: "safety_cone",
    12: "safety_barrier",
}

SAFETY_CLASS_NAMES: list[str] = [SAFETY_CLASSES[i] for i in range(13)]

# Reverse lookup
_NAME_TO_ID: dict[str, int] = {v: k for k, v in SAFETY_CLASSES.items()}

# ---------------------------------------------------------------------------
# Dataset class mapping: source class name → unified class id (or None=drop)
# ---------------------------------------------------------------------------

# SODA dataset classes — actual VOC XML labels found in the dataset:
#   person, helmet, vest, scaffold, hook, wood, board, fence, ebox,
#   hopper, rebar, brick, cutter, handcart, slogan
SODA_CLASS_MAP: dict[str, int | None] = {
    # People
    "person": 0, "worker": 0, "people": 0, "pedestrian": 0,
    # PPE — SODA uses "helmet" (worn) and "vest" (worn)
    "helmet": 1, "hardhat": 1, "hard_hat": 1, "hard hat": 1,
    "no_hardhat": 2, "no_hard_hat": 2, "no hard hat": 2, "no_helmet": 2,
    "vest": 3, "safety_vest": 3, "safety vest": 3, "hi-vis vest": 3,
    "no_safety_vest": 4, "no_vest": 4, "no vest": 4,
    # Equipment
    "excavator": 5, "backhoe": 5,
    "crane": 6, "tower crane": 6, "tower_crane": 6, "mobile crane": 6,
    "loader": 7, "wheel loader": 7, "wheel_loader": 7, "front loader": 7,
    "dump truck": 8, "dump_truck": 8, "truck": 8,
    "concrete mixer": 9, "concrete_mixer": 9, "mixer truck": 9, "mixer": 9,
    "scaffold": 10, "scaffolding": 10,
    "safety cone": 11, "safety_cone": 11, "cone": 11, "traffic cone": 11,
    "safety barrier": 12, "safety_barrier": 12, "barrier": 12,
    "barricade": 12, "guardrail": 12, "fence": 12,
    # Dropped classes (not in safety taxonomy)
    "hook": None, "wood": None, "board": None, "ebox": None,
    "hopper": None, "rebar": None, "brick": None, "cutter": None,
    "handcart": None, "slogan": None,
    "materials": None, "pipes": None, "pipe": None,
    "lumber": None, "container": None, "dumpster": None,
    "forklift": None, "ladder": None, "vehicle": None, "car": None,
}

# Roboflow Safety dataset classes — actual class names from ppe_data.yaml:
#   0:Hardhat, 1:Mask, 2:NO-Hardhat, 3:NO-Mask, 4:NO-Safety Vest,
#   5:Person, 6:Safety Cone, 7:Safety Vest, 8:machinery, 9:vehicle
ROBOFLOW_CLASS_MAP: dict[str, int | None] = {
    # Actual Roboflow class names (case-sensitive as in yaml)
    "Hardhat": 1, "hardhat": 1, "hard-hat": 1,
    "Mask": None, "mask": None,              # Not in our taxonomy
    "NO-Hardhat": 2, "no-hardhat": 2, "no_hardhat": 2,
    "NO-Mask": None, "no-mask": None,        # Not in our taxonomy
    "NO-Safety Vest": 4, "no-safety-vest": 4, "no_vest": 4, "NO-Vest": 4,
    "Person": 0, "person": 0, "worker": 0, "Worker": 0,
    "Safety Cone": 11, "safety-cone": 11, "cone": 11, "safety_cone": 11,
    "Safety Vest": 3, "safety-vest": 3, "safety_vest": 3, "Vest": 3, "vest": 3,
    "machinery": 5, "Machinery": 5,          # Map to excavator (closest equipment class)
    "vehicle": 8, "Vehicle": 8,              # Map to dump_truck (closest vehicle class)
    # Extra aliases
    "helmet": 1, "Helmet": 1,
    "NO-Helmet": 2, "no-helmet": 2,
    "excavator": 5, "Excavator": 5,
    "crane": 6, "Crane": 6,
    "scaffolding": 10, "Scaffolding": 10,
    "barrier": 12, "barricade": 12, "Barricade": 12,
    "guardrail": 12, "Guardrail": 12,
    "Goggles": None, "Gloves": None, "Boots": None,
}


# ---------------------------------------------------------------------------
# Dataset discovery and conversion
# ---------------------------------------------------------------------------


def _discover_yolo_dataset(dataset_dir: Path) -> dict:
    """Discover the structure of a YOLO-format dataset.

    Returns dict with 'images_dir', 'labels_dir', 'class_names',
    'num_images', 'format' ('yolo' or 'coco').
    """
    info: dict = {"root": str(dataset_dir), "format": "unknown"}

    # Check for data.yaml or *.yaml (Ultralytics format)
    yaml_files = list(dataset_dir.glob("*.yaml")) + list(dataset_dir.glob("*.yml"))
    if yaml_files:
        import yaml

        with open(yaml_files[0]) as f:
            cfg = yaml.safe_load(f)
        info["yaml_config"] = str(yaml_files[0])
        if "names" in cfg:
            if isinstance(cfg["names"], dict):
                info["class_names"] = cfg["names"]
            elif isinstance(cfg["names"], list):
                info["class_names"] = {i: n for i, n in enumerate(cfg["names"])}

    # Check for YOLO label files (*.txt alongside images)
    for subdir in ["train", "valid", "val", "test", ""]:
        img_dir = dataset_dir / "images" / subdir if subdir else dataset_dir / "images"
        lbl_dir = dataset_dir / "labels" / subdir if subdir else dataset_dir / "labels"
        if img_dir.exists() and lbl_dir.exists():
            info["format"] = "yolo"
            break

    # Check for COCO JSON annotations
    ann_dir = dataset_dir / "annotations"
    if ann_dir.exists():
        coco_files = list(ann_dir.glob("*.json"))
        if coco_files:
            info["format"] = "coco"
            info["annotation_files"] = [str(f) for f in coco_files]

    return info


def _read_class_names_from_yaml(yaml_path: Path) -> dict[int, str]:
    """Read class names from a YOLO dataset YAML."""
    import yaml

    with open(yaml_path) as f:
        cfg = yaml.safe_load(f)

    names = cfg.get("names", {})
    if isinstance(names, list):
        return {i: n for i, n in enumerate(names)}
    return names


def _convert_coco_to_yolo(
    coco_json_path: Path,
    images_dir: Path,
    output_images_dir: Path,
    output_labels_dir: Path,
    class_map: dict[str, int | None],
) -> dict[str, int]:
    """Convert COCO JSON annotations to YOLO format with class remapping.

    Returns count of images and annotations converted.
    """
    with open(coco_json_path) as f:
        coco = json.load(f)

    # Build COCO category id → name lookup
    cat_id_to_name: dict[int, str] = {}
    for cat in coco.get("categories", []):
        cat_id_to_name[cat["id"]] = cat["name"]

    # Build image id → info lookup
    img_id_to_info: dict[int, dict] = {}
    for img in coco.get("images", []):
        img_id_to_info[img["id"]] = img

    # Group annotations by image
    img_annotations: dict[int, list] = defaultdict(list)
    for ann in coco.get("annotations", []):
        img_annotations[ann["image_id"]].append(ann)

    output_images_dir.mkdir(parents=True, exist_ok=True)
    output_labels_dir.mkdir(parents=True, exist_ok=True)

    stats = {"images": 0, "annotations": 0, "dropped": 0}

    for img_id, img_info in img_id_to_info.items():
        anns = img_annotations.get(img_id, [])
        if not anns:
            continue

        w, h = img_info["width"], img_info["height"]
        filename = img_info["file_name"]
        stem = Path(filename).stem

        # Convert annotations
        yolo_lines: list[str] = []
        for ann in anns:
            cat_name = cat_id_to_name.get(ann["category_id"], "")
            unified_id = class_map.get(cat_name.lower())
            if unified_id is None:
                # Also try exact match
                unified_id = class_map.get(cat_name)
            if unified_id is None:
                stats["dropped"] += 1
                continue

            # COCO bbox: [x, y, width, height] → YOLO: [cx, cy, w, h] normalized
            bx, by, bw, bh = ann["bbox"]
            cx = (bx + bw / 2) / w
            cy = (by + bh / 2) / h
            nw = bw / w
            nh = bh / h

            # Clamp to [0, 1]
            cx = max(0.0, min(1.0, cx))
            cy = max(0.0, min(1.0, cy))
            nw = max(0.001, min(1.0, nw))
            nh = max(0.001, min(1.0, nh))

            yolo_lines.append(f"{unified_id} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
            stats["annotations"] += 1

        if not yolo_lines:
            continue

        # Copy image
        src_img = images_dir / filename
        if not src_img.exists():
            # Try nested paths
            for candidate in images_dir.rglob(filename):
                src_img = candidate
                break

        if src_img.exists():
            dst_img = output_images_dir / f"{stem}{src_img.suffix}"
            if not dst_img.exists():
                shutil.copy2(src_img, dst_img)

        # Write label file
        label_path = output_labels_dir / f"{stem}.txt"
        with open(label_path, "w") as f:
            f.write("\n".join(yolo_lines) + "\n")

        stats["images"] += 1

    return stats


def _remap_yolo_labels(
    src_images_dir: Path,
    src_labels_dir: Path,
    dst_images_dir: Path,
    dst_labels_dir: Path,
    src_class_names: dict[int, str],
    class_map: dict[str, int | None],
    prefix: str = "",
) -> dict[str, int]:
    """Remap YOLO label files from source class IDs to unified taxonomy.

    Returns conversion stats.
    """
    dst_images_dir.mkdir(parents=True, exist_ok=True)
    dst_labels_dir.mkdir(parents=True, exist_ok=True)

    stats = {"images": 0, "annotations": 0, "dropped": 0}

    label_files = sorted(src_labels_dir.glob("*.txt"))
    for label_file in label_files:
        stem = label_file.stem

        # Find corresponding image
        src_img = None
        for ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
            candidate = src_images_dir / f"{stem}{ext}"
            if candidate.exists():
                src_img = candidate
                break

        if src_img is None:
            continue

        # Remap labels
        yolo_lines: list[str] = []
        with open(label_file) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 5:
                    continue

                src_cls_id = int(parts[0])
                src_cls_name = src_class_names.get(src_cls_id, "")

                # Try lowercase lookup
                unified_id = class_map.get(src_cls_name.lower())
                if unified_id is None:
                    unified_id = class_map.get(src_cls_name)
                if unified_id is None:
                    stats["dropped"] += 1
                    continue

                yolo_lines.append(f"{unified_id} {' '.join(parts[1:])}")
                stats["annotations"] += 1

        if not yolo_lines:
            continue

        # Copy image with prefix to avoid name collisions
        dst_name = f"{prefix}{stem}" if prefix else stem
        dst_img = dst_images_dir / f"{dst_name}{src_img.suffix}"
        if not dst_img.exists():
            shutil.copy2(src_img, dst_img)

        # Write remapped label
        dst_label = dst_labels_dir / f"{dst_name}.txt"
        with open(dst_label, "w") as f:
            f.write("\n".join(yolo_lines) + "\n")

        stats["images"] += 1

    return stats


def _convert_voc_to_yolo(
    annotations_dir: Path,
    images_dir: Path,
    output_images_dir: Path,
    output_labels_dir: Path,
    class_map: dict[str, int | None],
    image_list: list[str] | None = None,
    prefix: str = "",
) -> dict[str, int]:
    """Convert Pascal VOC XML annotations to YOLO format with class remapping.

    Parameters
    ----------
    annotations_dir : Directory containing .xml annotation files
    images_dir : Directory containing source images
    output_images_dir : Where to copy images
    output_labels_dir : Where to write YOLO .txt labels
    class_map : Source class name → unified class ID (None = drop)
    image_list : Optional list of image stems to process (from ImageSets)
    prefix : Prefix for output filenames to avoid collisions

    Returns count of images and annotations converted.
    """
    output_images_dir.mkdir(parents=True, exist_ok=True)
    output_labels_dir.mkdir(parents=True, exist_ok=True)

    stats = {"images": 0, "annotations": 0, "dropped": 0}

    if image_list is not None:
        xml_files = [annotations_dir / f"{stem}.xml" for stem in image_list]
        xml_files = [f for f in xml_files if f.exists()]
    else:
        xml_files = sorted(annotations_dir.glob("*.xml"))

    for xml_path in xml_files:
        if xml_path.name == "README.md":
            continue
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError:
            logger.warning("Failed to parse XML: %s", xml_path)
            continue
        root = tree.getroot()

        # Get image dimensions
        size_el = root.find("size")
        if size_el is None:
            continue
        w = int(size_el.findtext("width", "0"))
        h = int(size_el.findtext("height", "0"))
        if w == 0 or h == 0:
            continue

        # Get filename
        filename = root.findtext("filename", xml_path.stem + ".jpg")
        stem = xml_path.stem

        # Convert each object
        yolo_lines: list[str] = []
        for obj in root.findall("object"):
            name = obj.findtext("name", "").strip()
            unified_id = class_map.get(name.lower())
            if unified_id is None:
                unified_id = class_map.get(name)
            if unified_id is None:
                stats["dropped"] += 1
                continue

            bndbox = obj.find("bndbox")
            if bndbox is None:
                continue
            xmin = float(bndbox.findtext("xmin", "0"))
            ymin = float(bndbox.findtext("ymin", "0"))
            xmax = float(bndbox.findtext("xmax", "0"))
            ymax = float(bndbox.findtext("ymax", "0"))

            # VOC → YOLO normalized center format
            cx = ((xmin + xmax) / 2) / w
            cy = ((ymin + ymax) / 2) / h
            bw = (xmax - xmin) / w
            bh = (ymax - ymin) / h

            # Clamp
            cx = max(0.0, min(1.0, cx))
            cy = max(0.0, min(1.0, cy))
            bw = max(0.001, min(1.0, bw))
            bh = max(0.001, min(1.0, bh))

            yolo_lines.append(f"{unified_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            stats["annotations"] += 1

        if not yolo_lines:
            continue

        # Find and copy image
        src_img = images_dir / filename
        if not src_img.exists():
            # Try common extensions
            for ext in [".jpg", ".jpeg", ".png", ".bmp"]:
                candidate = images_dir / f"{stem}{ext}"
                if candidate.exists():
                    src_img = candidate
                    break

        if src_img.exists():
            dst_name = f"{prefix}{stem}" if prefix else stem
            dst_img = output_images_dir / f"{dst_name}{src_img.suffix}"
            if not dst_img.exists():
                shutil.copy2(src_img, dst_img)

            # Write YOLO label
            dst_label = output_labels_dir / f"{dst_name}.txt"
            with open(dst_label, "w") as f:
                f.write("\n".join(yolo_lines) + "\n")

            stats["images"] += 1

    return stats


# ---------------------------------------------------------------------------
# Stratified splitting
# ---------------------------------------------------------------------------


def _get_image_class_distribution(labels_dir: Path) -> dict[str, set[int]]:
    """Get the set of class IDs present in each image's label file.

    Returns {stem: {class_ids}}.
    """
    dist: dict[str, set[int]] = {}
    for label_file in sorted(labels_dir.glob("*.txt")):
        classes: set[int] = set()
        with open(label_file) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 5:
                    classes.add(int(parts[0]))
        if classes:
            dist[label_file.stem] = classes
    return dist


def stratified_split(
    combined_images_dir: Path,
    combined_labels_dir: Path,
    output_dir: Path,
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    test_ratio: float = 0.1,
    seed: int = 42,
) -> dict[str, int]:
    """Stratified split ensuring class balance across train/val/test.

    Uses the primary (most frequent) class per image for stratification.
    Returns split counts.
    """
    random.seed(seed)

    # Get class distribution per image
    img_classes = _get_image_class_distribution(combined_labels_dir)
    if not img_classes:
        raise ValueError(f"No label files found in {combined_labels_dir}")

    # M-54: Stratify on the *rarest* class actually present in each image,
    # not `min(classes)` (which was picking the smallest class ID — usually
    # the majority class like `person`, which defeats the purpose of
    # stratification). Rarest class ensures minority classes
    # (no_hard_hat, dump_truck, safety_cone) land in every split.
    # Falls back to smallest ID when classes are equally rare.
    global_counts: dict[int, int] = defaultdict(int)
    for classes in img_classes.values():
        for c in classes:
            global_counts[c] += 1

    class_groups: dict[int, list[str]] = defaultdict(list)
    for stem, classes in img_classes.items():
        # pick the class with the smallest global count; ties broken by
        # smaller class ID.
        primary = min(classes, key=lambda c: (global_counts[c], c))
        class_groups[primary].append(stem)

    # Create output directories
    splits = ["train", "val", "test"]
    for split in splits:
        (output_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (output_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    counts = {"train": 0, "val": 0, "test": 0}

    for cls_id, stems in class_groups.items():
        random.shuffle(stems)
        n = len(stems)
        n_train = max(1, int(n * train_ratio))
        n_val = max(1, int(n * val_ratio)) if n > 2 else 0
        # Rest goes to test
        train_stems = stems[:n_train]
        val_stems = stems[n_train : n_train + n_val]
        test_stems = stems[n_train + n_val :]

        for split_name, split_stems in [
            ("train", train_stems),
            ("val", val_stems),
            ("test", test_stems),
        ]:
            for stem in split_stems:
                # Find and copy image
                for ext in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
                    src_img = combined_images_dir / f"{stem}{ext}"
                    if src_img.exists():
                        dst_img = output_dir / "images" / split_name / src_img.name
                        if not dst_img.exists():
                            shutil.copy2(src_img, dst_img)
                        break

                # Copy label
                src_label = combined_labels_dir / f"{stem}.txt"
                if src_label.exists():
                    dst_label = output_dir / "labels" / split_name / f"{stem}.txt"
                    if not dst_label.exists():
                        shutil.copy2(src_label, dst_label)

                counts[split_name] += 1

    logger.info(
        "Stratified split: train=%d, val=%d, test=%d",
        counts["train"], counts["val"], counts["test"],
    )
    return counts


# ---------------------------------------------------------------------------
# Dataset statistics
# ---------------------------------------------------------------------------


def compute_dataset_stats(labels_dir: Path) -> dict:
    """Compute class distribution statistics for a label directory."""
    class_counts: Counter = Counter()
    total_boxes = 0
    total_images = 0

    for label_file in sorted(labels_dir.glob("*.txt")):
        total_images += 1
        with open(label_file) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 5:
                    cls_id = int(parts[0])
                    class_counts[cls_id] += 1
                    total_boxes += 1

    return {
        "total_images": total_images,
        "total_boxes": total_boxes,
        "class_counts": {
            SAFETY_CLASSES.get(k, f"class_{k}"): v
            for k, v in sorted(class_counts.items())
        },
        "avg_boxes_per_image": round(total_boxes / max(total_images, 1), 1),
    }


# ---------------------------------------------------------------------------
# YAML config generation
# ---------------------------------------------------------------------------


def generate_dataset_yaml(
    output_dir: Path,
    yaml_name: str = "constructai_safety.yaml",
) -> Path:
    """Generate Ultralytics dataset YAML config."""
    import yaml

    config = {
        "path": str(output_dir.resolve()),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "nc": 13,
        "names": SAFETY_CLASS_NAMES,
    }

    yaml_path = output_dir / yaml_name
    with open(yaml_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    logger.info("Dataset YAML written to %s", yaml_path)
    return yaml_path


# ---------------------------------------------------------------------------
# Full data preparation pipeline
# ---------------------------------------------------------------------------


def prepare_combined_dataset(
    soda_dir: Path | None,
    roboflow_dir: Path | None,
    output_dir: Path,
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    test_ratio: float = 0.1,
) -> Path:
    """Prepare the combined, harmonized, and split dataset.

    Steps:
    1. Discover dataset formats (YOLO or COCO)
    2. Convert/remap to unified taxonomy
    3. Combine into a staging directory
    4. Stratified split into train/val/test
    5. Generate dataset YAML

    Returns path to dataset YAML config.
    """
    staging_dir = output_dir / "_staging"
    staging_images = staging_dir / "images"
    staging_labels = staging_dir / "labels"
    staging_images.mkdir(parents=True, exist_ok=True)
    staging_labels.mkdir(parents=True, exist_ok=True)

    total_stats: dict[str, int] = {"images": 0, "annotations": 0, "dropped": 0}

    # --- Process SODA dataset ---
    if soda_dir and soda_dir.exists():
        logger.info("Processing SODA dataset from %s", soda_dir)

        # Check for Pascal VOC format (Annotations/ + JPEGImages/)
        ann_dir = soda_dir / "Annotations"
        img_dir = soda_dir / "JPEGImages"
        imagesets_dir = soda_dir / "ImageSets" / "Main"

        if ann_dir.exists() and img_dir.exists():
            logger.info("SODA format: Pascal VOC (XML)")

            # Use ImageSets split files if available
            image_list = None
            for split_file in ["trainval.txt", "train.txt"]:
                split_path = imagesets_dir / split_file
                if split_path.exists():
                    with open(split_path) as f:
                        image_list = [line.strip() for line in f if line.strip()]
                    logger.info("  Using %s (%d images)", split_file, len(image_list))
                    break

            # Also include test set
            test_path = imagesets_dir / "test.txt"
            if test_path.exists():
                with open(test_path) as f:
                    test_stems = [line.strip() for line in f if line.strip()]
                if image_list:
                    image_list.extend(test_stems)
                else:
                    image_list = test_stems
                logger.info("  Including test set (%d images)", len(test_stems))

            stats = _convert_voc_to_yolo(
                ann_dir, img_dir,
                staging_images, staging_labels,
                SODA_CLASS_MAP,
                image_list=image_list,
                prefix="soda_",
            )
            for k in total_stats:
                total_stats[k] += stats.get(k, 0)
            logger.info("  SODA VOC: %s", stats)
        else:
            # Fallback: try YOLO or COCO formats
            soda_info = _discover_yolo_dataset(soda_dir)
            logger.info("SODA format: %s", soda_info.get("format"))

            if soda_info["format"] == "yolo":
                src_class_names = soda_info.get("class_names", {})
                if not src_class_names and soda_info.get("yaml_config"):
                    src_class_names = _read_class_names_from_yaml(
                        Path(soda_info["yaml_config"])
                    )
                for subdir in ["train", "valid", "val", "test", ""]:
                    img_d = soda_dir / "images" / subdir if subdir else soda_dir / "images"
                    lbl_d = soda_dir / "labels" / subdir if subdir else soda_dir / "labels"
                    if img_d.exists() and lbl_d.exists():
                        stats = _remap_yolo_labels(
                            img_d, lbl_d,
                            staging_images, staging_labels,
                            src_class_names, SODA_CLASS_MAP,
                            prefix="soda_",
                        )
                        for k in total_stats:
                            total_stats[k] += stats.get(k, 0)
                        logger.info("  SODA/%s: %s", subdir or "root", stats)

            elif soda_info["format"] == "coco":
                for ann_file in soda_info.get("annotation_files", []):
                    ann_path = Path(ann_file)
                    img_d = soda_dir / "images"
                    stats = _convert_coco_to_yolo(
                        ann_path, img_d,
                        staging_images, staging_labels,
                        SODA_CLASS_MAP,
                    )
                    for k in total_stats:
                        total_stats[k] += stats.get(k, 0)
                    logger.info("  SODA COCO: %s", stats)
    else:
        logger.info("No SODA dataset directory provided or found")

    # --- Process Roboflow Safety dataset ---
    if roboflow_dir and roboflow_dir.exists():
        logger.info("Processing Roboflow Safety dataset from %s", roboflow_dir)

        # Roboflow dataset may be nested under css-data/ or similar subdirectory
        rf_data_dir = roboflow_dir
        for subdir_name in ["css-data", "data", "dataset"]:
            candidate = roboflow_dir / subdir_name
            if candidate.exists() and (candidate / "train").exists():
                rf_data_dir = candidate
                logger.info("  Found nested Roboflow data in %s/", subdir_name)
                break

        # Find class names from yaml (may be in parent or results dir)
        src_class_names: dict[int, str] = {}
        for yaml_candidate in [
            rf_data_dir / "data.yaml",
            roboflow_dir / "data.yaml",
            *roboflow_dir.rglob("ppe_data.yaml"),
            *roboflow_dir.rglob("*.yaml"),
        ]:
            if yaml_candidate.exists():
                try:
                    src_class_names = _read_class_names_from_yaml(yaml_candidate)
                    logger.info("  Class names from %s: %s", yaml_candidate.name, src_class_names)
                    break
                except Exception:
                    continue

        if not src_class_names:
            # Fallback: use known Roboflow Safety class order
            src_class_names = {
                0: "Hardhat", 1: "Mask", 2: "NO-Hardhat", 3: "NO-Mask",
                4: "NO-Safety Vest", 5: "Person", 6: "Safety Cone",
                7: "Safety Vest", 8: "machinery", 9: "vehicle",
            }
            logger.info("  Using default Roboflow class names")

        # Process each split (Roboflow uses images/labels directly under split dirs)
        for subdir in ["train", "valid", "val", "test"]:
            split_dir = rf_data_dir / subdir
            if not split_dir.exists():
                continue

            img_d = split_dir / "images"
            lbl_d = split_dir / "labels"
            if img_d.exists() and lbl_d.exists():
                stats = _remap_yolo_labels(
                    img_d, lbl_d,
                    staging_images, staging_labels,
                    src_class_names, ROBOFLOW_CLASS_MAP,
                    prefix="rf_",
                )
                for k in total_stats:
                    total_stats[k] += stats.get(k, 0)
                logger.info("  Roboflow/%s: %s", subdir, stats)
    else:
        logger.info("No Roboflow dataset directory provided or found")

    logger.info("Combined staging: %s", total_stats)

    # --- Compute staging stats ---
    staging_stats = compute_dataset_stats(staging_labels)
    logger.info("Staging dataset stats:")
    logger.info("  Images: %d", staging_stats["total_images"])
    logger.info("  Total boxes: %d", staging_stats["total_boxes"])
    logger.info("  Avg boxes/image: %.1f", staging_stats["avg_boxes_per_image"])
    for cls_name, count in staging_stats["class_counts"].items():
        logger.info("  %s: %d", cls_name, count)

    # --- Stratified split ---
    split_counts = stratified_split(
        staging_images, staging_labels, output_dir,
        train_ratio=train_ratio,
        val_ratio=val_ratio,
        test_ratio=test_ratio,
    )

    # Log per-split stats
    for split in ["train", "val", "test"]:
        split_stats = compute_dataset_stats(output_dir / "labels" / split)
        logger.info("%s split: %d images, %d boxes", split, split_stats["total_images"], split_stats["total_boxes"])

    # --- Generate YAML ---
    yaml_path = generate_dataset_yaml(output_dir)

    # Clean up staging
    shutil.rmtree(staging_dir, ignore_errors=True)

    # Save stats for report
    all_stats = {
        "combined": total_stats,
        "staging": staging_stats,
        "splits": split_counts,
    }
    with open(output_dir / "dataset_stats.json", "w") as f:
        json.dump(all_stats, f, indent=2)

    return yaml_path


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train_safety_model(
    data_yaml: str | Path,
    output_dir: str = "constructai_safety",
    epochs: int = 100,
    patience: int = 15,
    imgsz: int = 1280,
    batch: int = 8,
    device: str = "0",
    resume: str | None = None,
    model_weights: str = "yolov8l.pt",
) -> dict:
    """Train YOLOv8-L on the construction safety dataset.

    Parameters
    ----------
    data_yaml : Path to the dataset YAML config
    output_dir : Project directory for Ultralytics outputs
    epochs : Max training epochs (early stopping via patience)
    patience : Early stopping patience (epochs without improvement)
    imgsz : Input image size (1280 for PPE detection at distance)
    batch : Batch size (8 for 0490, 16 for 2x3090)
    device : GPU device(s) — "0" for single, "0,1" for multi-GPU
    resume : Path to checkpoint to resume from
    model_weights : Base model weights to start from

    Returns
    -------
    Training results dict with metrics.
    """
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error("ultralytics not installed: pip install ultralytics")
        return {"status": "failed", "error": "ultralytics not available"}

    try:
        if resume:
            logger.info("Resuming training from %s", resume)
            model = YOLO(resume)
        else:
            logger.info("Starting training from %s", model_weights)
            model = YOLO(model_weights)

        # H-25: class-loss weights prevent majority classes (person, hard_hat
        # at 80k+ samples) from drowning out minority classes (no_hard_hat,
        # dump_truck) during training. Ultralytics exposes `cls` as a scalar
        # multiplier on the class-loss head; combined with the default BCE
        # across classes that multiplier produces "weight rare mistakes
        # more heavily" without requiring a custom loss module.
        #
        # `box` and `dfl` kept at defaults; only class-loss is boosted.
        results = model.train(
            data=str(data_yaml),
            epochs=epochs,
            patience=patience,
            imgsz=imgsz,
            batch=batch,
            optimizer="SGD",
            lr0=0.01,
            lrf=0.01,
            cls=1.0,           # H-25: raise from 0.5 default to up-weight minority classes
            box=7.5,
            dfl=1.5,
            mosaic=1.0,
            mixup=0.1,
            flipud=0.0,        # Don't flip vertically
            fliplr=0.5,
            hsv_h=0.015,
            hsv_s=0.7,
            hsv_v=0.4,
            degrees=5.0,       # Slight rotation
            perspective=0.0005,
            project=output_dir,
            name="yolov8l_construction",
            save=True,
            save_period=10,    # Checkpoint every 10 epochs
            plots=True,
            device=device,
            exist_ok=True,
            resume=bool(resume),
        )

        return {
            "status": "completed",
            "results": str(results),
            "output_dir": output_dir,
        }

    except Exception as exc:
        logger.error("Training failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_model(
    model_path: str | Path,
    data_yaml: str | Path,
) -> dict:
    """Evaluate trained model on the test set.

    Returns per-class AP@0.5, mAP@0.5, mAP@0.5:0.95, per-class recall.
    """
    try:
        from ultralytics import YOLO
    except ImportError:
        return {"status": "failed", "error": "ultralytics not available"}

    try:
        model = YOLO(str(model_path))
        metrics = model.val(data=str(data_yaml), split="test")

        results = {
            "mAP50": float(metrics.box.map50),
            "mAP50_95": float(metrics.box.map),
            "precision": float(metrics.box.mp),
            "recall": float(metrics.box.mr),
        }

        # Per-class metrics
        per_class: list[dict] = []
        for i, name in enumerate(SAFETY_CLASS_NAMES):
            cls_metrics = {
                "class": name,
                "class_id": i,
                "AP50": float(metrics.box.ap50[i]) if i < len(metrics.box.ap50) else 0.0,
                "AP50_95": float(metrics.box.ap[i]) if i < len(metrics.box.ap) else 0.0,
            }
            per_class.append(cls_metrics)

        results["per_class"] = per_class

        # Check targets
        targets = {
            "mAP50_target": 0.75,
            "mAP50_met": results["mAP50"] > 0.75,
            "person_AP50_target": 0.85,
            "hard_hat_AP50_target": 0.70,
            "no_hard_hat_AP50_target": 0.70,
            "safety_vest_AP50_target": 0.70,
            "no_vest_AP50_target": 0.70,
        }

        # Check per-class targets
        for cls in per_class:
            if cls["class"] == "person":
                targets["person_AP50_met"] = cls["AP50"] > 0.85
            elif cls["class"] == "hard_hat":
                targets["hard_hat_AP50_met"] = cls["AP50"] > 0.70
            elif cls["class"] == "no_hard_hat":
                targets["no_hard_hat_AP50_met"] = cls["AP50"] > 0.70
            elif cls["class"] == "safety_vest":
                targets["safety_vest_AP50_met"] = cls["AP50"] > 0.70
            elif cls["class"] == "no_vest":
                targets["no_vest_AP50_met"] = cls["AP50"] > 0.70

        results["targets"] = targets

        # Log results
        logger.info("=== Test Set Evaluation ===")
        logger.info("mAP@0.5: %.4f (target > 0.75)", results["mAP50"])
        logger.info("mAP@0.5:0.95: %.4f", results["mAP50_95"])
        logger.info("Precision: %.4f", results["precision"])
        logger.info("Recall: %.4f", results["recall"])
        logger.info("Per-class AP@0.5:")
        for cls in per_class:
            logger.info("  %s: %.3f", cls["class"], cls["AP50"])

        return results

    except Exception as exc:
        logger.error("Evaluation failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_model(
    model_path: str | Path,
    imgsz: int = 1280,
    export_onnx: bool = True,
    export_tensorrt: bool = True,
) -> dict:
    """Export trained model to ONNX and/or TensorRT formats.

    Returns dict with paths to exported models.
    """
    try:
        from ultralytics import YOLO
    except ImportError:
        return {"status": "failed", "error": "ultralytics not available"}

    exports: dict[str, str] = {}

    try:
        model = YOLO(str(model_path))

        if export_onnx:
            logger.info("Exporting ONNX (imgsz=%d)...", imgsz)
            onnx_path = model.export(format="onnx", imgsz=imgsz)
            exports["onnx"] = str(onnx_path)
            logger.info("ONNX exported to: %s", onnx_path)

        if export_tensorrt:
            try:
                logger.info("Exporting TensorRT engine (imgsz=%d)...", imgsz)
                engine_path = model.export(format="engine", imgsz=imgsz)
                exports["tensorrt"] = str(engine_path)
                logger.info("TensorRT exported to: %s", engine_path)
            except Exception as exc:
                logger.warning("TensorRT export failed (NVIDIA GPU required): %s", exc)
                exports["tensorrt"] = None

        return {"status": "completed", "exports": exports}

    except Exception as exc:
        logger.error("Export failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# Save to model registry
# ---------------------------------------------------------------------------


def save_to_registry(
    model_path: str | Path,
    eval_results: dict,
    registry_dir: str | Path = "models/safety_yolo_v1.0",
) -> Path:
    """Copy trained model and metadata to the model registry directory."""
    registry = Path(registry_dir)
    registry.mkdir(parents=True, exist_ok=True)

    model_path = Path(model_path)

    # Copy best weights
    if model_path.exists():
        shutil.copy2(model_path, registry / "best.pt")
        logger.info("Model saved to %s", registry / "best.pt")

    # Save metadata
    metadata = {
        "model_name": "constructai_safety_yolo",
        "version": "1.0",
        "base_model": "yolov8l",
        "imgsz": 1280,
        "num_classes": 13,
        "class_names": SAFETY_CLASS_NAMES,
        "evaluation": eval_results,
        "training_datasets": ["SODA", "Roboflow-Safety"],
    }

    with open(registry / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    # Save class names
    with open(registry / "classes.txt", "w") as f:
        for i, name in enumerate(SAFETY_CLASS_NAMES):
            f.write(f"{i}\t{name}\n")

    logger.info("Model registry saved to %s", registry)
    return registry


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="YOLO safety detection training pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--soda-dir", type=Path, default=Path.home() / "constructai-data/cv-training/soda",
        help="SODA dataset directory",
    )
    parser.add_argument(
        "--roboflow-dir", type=Path, default=Path.home() / "constructai-data/cv-training/roboflow-safety",
        help="Roboflow Safety dataset directory",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("constructai_safety"),
        help="Output directory for combined dataset and training results",
    )
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--batch", type=int, default=8, help="Batch size (8 for 0490, 16 for 2x3090)")
    parser.add_argument("--device", type=str, default="0", help="GPU device(s): '0' or '0,1'")
    parser.add_argument("--resume", type=str, default=None, help="Resume from checkpoint")
    parser.add_argument("--model", type=str, default="yolov8l.pt", help="Base model weights")
    parser.add_argument("--prepare-only", action="store_true", help="Only prepare dataset, skip training")
    parser.add_argument("--skip-prepare", action="store_true", help="Skip data prep, use existing dataset")
    parser.add_argument("--skip-export", action="store_true", help="Skip ONNX/TensorRT export")
    parser.add_argument(
        "--registry-dir", type=Path, default=Path("models/safety_yolo_v1.0"),
        help="Model registry output directory",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Step 1: Data preparation
    yaml_path = args.output_dir / "constructai_safety.yaml"

    if not args.skip_prepare:
        logger.info("=" * 60)
        logger.info("STEP 1: Dataset Preparation")
        logger.info("=" * 60)
        yaml_path = prepare_combined_dataset(
            soda_dir=args.soda_dir if args.soda_dir.exists() else None,
            roboflow_dir=args.roboflow_dir if args.roboflow_dir.exists() else None,
            output_dir=args.output_dir,
        )

    if args.prepare_only:
        logger.info("Data preparation complete. Exiting (--prepare-only).")
        return

    if not yaml_path.exists():
        logger.error("Dataset YAML not found at %s. Run without --skip-prepare.", yaml_path)
        return

    # Step 2: Training
    logger.info("=" * 60)
    logger.info("STEP 2: Training YOLOv8-L")
    logger.info("=" * 60)
    train_result = train_safety_model(
        data_yaml=yaml_path,
        output_dir=str(args.output_dir),
        epochs=args.epochs,
        patience=args.patience,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        resume=args.resume,
        model_weights=args.model,
    )
    logger.info("Training result: %s", train_result.get("status"))

    if train_result.get("status") != "completed":
        logger.error("Training failed: %s", train_result.get("error"))
        return

    # Find best weights
    best_weights = (
        args.output_dir / "yolov8l_construction" / "weights" / "best.pt"
    )
    if not best_weights.exists():
        # Try alternative path
        for candidate in Path(args.output_dir).rglob("best.pt"):
            best_weights = candidate
            break

    if not best_weights.exists():
        logger.error("Cannot find best.pt weights")
        return

    # Step 3: Evaluation
    logger.info("=" * 60)
    logger.info("STEP 3: Evaluating on Test Set")
    logger.info("=" * 60)
    eval_results = evaluate_model(best_weights, yaml_path)

    # Step 4: Export
    if not args.skip_export:
        logger.info("=" * 60)
        logger.info("STEP 4: Exporting Models")
        logger.info("=" * 60)
        export_result = export_model(
            best_weights,
            imgsz=args.imgsz,
            export_onnx=True,
            export_tensorrt=True,
        )
        logger.info("Export result: %s", export_result)

    # Step 5: Save to registry
    logger.info("=" * 60)
    logger.info("STEP 5: Saving to Model Registry")
    logger.info("=" * 60)
    save_to_registry(best_weights, eval_results, registry_dir=args.registry_dir)

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
