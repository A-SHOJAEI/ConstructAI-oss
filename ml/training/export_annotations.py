"""Export and convert annotations between COCO, YOLO, and VOC formats."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def coco_to_yolo(
    coco_json_path: str,
    output_dir: str,
    image_width: int = 640,
    image_height: int = 640,
) -> int:
    """Convert COCO format annotations to YOLO format.

    Parameters
    ----------
    coco_json_path:
        Path to COCO annotation JSON file.
    output_dir:
        Directory to save YOLO .txt files.
    image_width:
        Default image width for normalization.
    image_height:
        Default image height for normalization.

    Returns
    -------
    Number of annotations converted.
    """
    with open(coco_json_path) as f:
        coco = json.load(f)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Build image lookup
    images = {img["id"]: img for img in coco.get("images", [])}

    count = 0
    # Group annotations by image
    ann_by_image: dict[int, list] = {}
    for ann in coco.get("annotations", []):
        img_id = ann["image_id"]
        ann_by_image.setdefault(img_id, []).append(ann)

    for img_id, anns in ann_by_image.items():
        img_info = images.get(img_id, {})
        w = img_info.get("width", image_width)
        h = img_info.get("height", image_height)
        filename = img_info.get("file_name", f"{img_id}.jpg")
        stem = Path(filename).stem

        lines = []
        for ann in anns:
            bbox = ann["bbox"]  # COCO: [x, y, width, height]
            class_id = ann["category_id"]

            # Convert to YOLO: center_x, center_y, width, height (normalized)
            cx = (bbox[0] + bbox[2] / 2) / w
            cy = (bbox[1] + bbox[3] / 2) / h
            bw = bbox[2] / w
            bh = bbox[3] / h

            lines.append(f"{class_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            count += 1

        label_file = out_path / f"{stem}.txt"
        label_file.write_text("\n".join(lines))

    logger.info("Converted %d annotations to YOLO format in %s", count, output_dir)
    return count


def yolo_to_coco(
    labels_dir: str,
    images_dir: str,
    output_path: str,
    class_names: list[str] | None = None,
) -> dict:
    """Convert YOLO format to COCO JSON.

    Parameters
    ----------
    labels_dir:
        Directory with YOLO .txt label files.
    images_dir:
        Directory with corresponding images.
    output_path:
        Path to save the COCO JSON.
    class_names:
        Optional list of class names.

    Returns
    -------
    COCO annotation dict.
    """
    from PIL import Image

    labels_path = Path(labels_dir)
    images_path = Path(images_dir)

    if class_names is None:
        class_names = [
            "person", "hardhat", "no_hardhat", "safety_vest", "no_safety_vest",
            "truck", "excavator", "crane", "forklift", "scaffolding",
            "ladder", "guardrail", "barricade",
        ]

    coco = {
        "images": [],
        "annotations": [],
        "categories": [
            {"id": i, "name": name} for i, name in enumerate(class_names)
        ],
    }

    ann_id = 1
    for img_id, label_file in enumerate(sorted(labels_path.glob("*.txt")), 1):
        stem = label_file.stem

        # Find corresponding image
        img_file = None
        for ext in (".jpg", ".jpeg", ".png"):
            candidate = images_path / f"{stem}{ext}"
            if candidate.exists():
                img_file = candidate
                break

        if img_file is None:
            continue

        img = Image.open(img_file)
        w, h = img.size

        coco["images"].append({
            "id": img_id,
            "file_name": img_file.name,
            "width": w,
            "height": h,
        })

        for line in label_file.read_text().strip().splitlines():
            parts = line.strip().split()
            if len(parts) != 5:
                continue

            class_id = int(parts[0])
            cx, cy, bw, bh = map(float, parts[1:])

            # Convert YOLO normalized to COCO pixel [x, y, width, height]
            bbox_w = bw * w
            bbox_h = bh * h
            bbox_x = cx * w - bbox_w / 2
            bbox_y = cy * h - bbox_h / 2

            coco["annotations"].append({
                "id": ann_id,
                "image_id": img_id,
                "category_id": class_id,
                "bbox": [round(bbox_x, 1), round(bbox_y, 1), round(bbox_w, 1), round(bbox_h, 1)],
                "area": round(bbox_w * bbox_h, 1),
                "iscrowd": 0,
            })
            ann_id += 1

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(coco, f, indent=2)

    logger.info(
        "Converted to COCO: %d images, %d annotations",
        len(coco["images"]),
        len(coco["annotations"]),
    )
    return coco


def main():
    parser = argparse.ArgumentParser(description="Convert annotations")
    sub = parser.add_subparsers(dest="command")

    c2y = sub.add_parser("coco-to-yolo")
    c2y.add_argument("--input", required=True, help="COCO JSON path")
    c2y.add_argument("--output-dir", required=True, help="Output directory")

    y2c = sub.add_parser("yolo-to-coco")
    y2c.add_argument("--labels-dir", required=True, help="YOLO labels dir")
    y2c.add_argument("--images-dir", required=True, help="Images dir")
    y2c.add_argument("--output", required=True, help="Output COCO JSON path")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    if args.command == "coco-to-yolo":
        coco_to_yolo(args.input, args.output_dir)
    elif args.command == "yolo-to-coco":
        yolo_to_coco(args.labels_dir, args.images_dir, args.output)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
