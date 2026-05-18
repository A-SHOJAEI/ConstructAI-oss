"""Active learning pipeline for iterative model improvement."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def compute_uncertainty_scores(predictions: list[dict]) -> list[dict]:
    """Rank predictions by model uncertainty for annotation prioritization.

    Uses entropy-based uncertainty: images where the model is least confident
    provide the most learning value when labeled.

    Parameters
    ----------
    predictions:
        List of per-image prediction dicts with 'image_path', 'detections'.

    Returns
    -------
    Predictions sorted by uncertainty (highest first).
    """
    scored = []
    for pred in predictions:
        detections = pred.get("detections", [])
        if not detections:
            # No detections = high uncertainty (model missed objects)
            scored.append({**pred, "uncertainty": 1.0})
            continue

        confidences = [d.get("confidence", 0.5) for d in detections]
        avg_conf = sum(confidences) / len(confidences)

        # Entropy-based: maximize uncertainty near 0.5 confidence
        import math
        entropy_scores = []
        for c in confidences:
            c = max(0.001, min(0.999, c))
            entropy = -(c * math.log2(c) + (1 - c) * math.log2(1 - c))
            entropy_scores.append(entropy)

        avg_entropy = sum(entropy_scores) / len(entropy_scores)

        # Combine: low average confidence + high entropy = most uncertain
        uncertainty = (1 - avg_conf) * 0.5 + avg_entropy * 0.5

        scored.append({**pred, "uncertainty": round(uncertainty, 4)})

    return sorted(scored, key=lambda x: x["uncertainty"], reverse=True)


def select_samples_for_annotation(
    predictions: list[dict],
    budget: int = 100,
    strategy: str = "uncertainty",
) -> list[dict]:
    """Select the most informative samples for human annotation.

    Parameters
    ----------
    predictions:
        Per-image predictions from the current model.
    budget:
        Maximum number of images to select.
    strategy:
        Selection strategy: "uncertainty", "random", or "diverse".

    Returns
    -------
    Selected samples for annotation.
    """
    if strategy == "uncertainty":
        scored = compute_uncertainty_scores(predictions)
        return scored[:budget]

    elif strategy == "random":
        import random
        return random.sample(predictions, min(budget, len(predictions)))

    elif strategy == "diverse":
        # Select samples with diverse detection categories
        scored = compute_uncertainty_scores(predictions)
        selected = []
        seen_classes: set[str] = set()

        for pred in scored:
            det_classes = {d.get("class_name", "") for d in pred.get("detections", [])}
            new_classes = det_classes - seen_classes

            if new_classes or len(selected) < budget // 2:
                selected.append(pred)
                seen_classes.update(det_classes)

            if len(selected) >= budget:
                break

        return selected

    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def generate_annotation_batch(
    selected_samples: list[dict],
    output_dir: str,
    annotation_format: str = "coco",
) -> str:
    """Generate annotation batch for labeling tools (CVAT, Label Studio).

    Parameters
    ----------
    selected_samples:
        Images selected for annotation.
    output_dir:
        Directory to save the annotation batch.
    annotation_format:
        Output format: "coco" or "yolo".

    Returns
    -------
    Path to the generated batch manifest.
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    manifest = {
        "batch_id": f"batch_{len(selected_samples)}",
        "total_images": len(selected_samples),
        "format": annotation_format,
        "images": [],
    }

    for sample in selected_samples:
        manifest["images"].append({
            "image_path": sample.get("image_path", ""),
            "uncertainty": sample.get("uncertainty", 0.0),
            "pre_annotations": sample.get("detections", []),
        })

    manifest_path = out_path / "annotation_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    logger.info(
        "Generated annotation batch: %d images -> %s",
        len(selected_samples),
        manifest_path,
    )
    return str(manifest_path)


def main():
    parser = argparse.ArgumentParser(description="Active Learning Pipeline")
    parser.add_argument("--predictions", required=True, help="Predictions JSON path")
    parser.add_argument("--budget", type=int, default=100, help="Annotation budget")
    parser.add_argument("--strategy", default="uncertainty",
                        choices=["uncertainty", "random", "diverse"])
    parser.add_argument("--output-dir", default="./annotation_batch")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    with open(args.predictions) as f:
        predictions = json.load(f)

    selected = select_samples_for_annotation(
        predictions, budget=args.budget, strategy=args.strategy,
    )

    manifest_path = generate_annotation_batch(selected, args.output_dir)
    logger.info("Annotation batch ready: %s", manifest_path)


if __name__ == "__main__":
    main()
