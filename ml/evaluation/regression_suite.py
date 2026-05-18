"""Regression test suite for ConstructAI CV models.

Runs inference on curated test images and compares metrics against
established baselines. Used in CI/CD to catch model regressions before
deployment.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

BASELINES = {
    "safety_yolo": {
        "mAP_50": 0.75,
        "person_ap": 0.85,
        "ppe_ap": 0.70,
        "violation_recall": 0.80,
        "max_inference_ms": 50,
    },
    "defect_vit": {
        "accuracy": 0.70,
        "macro_f1": 0.65,
        "structural_f1": 0.70,
        "max_inference_ms": 30,
    },
}


@dataclass
class RegressionResult:
    model_name: str
    passed: bool
    metrics: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


def _load_test_images(data_dir: Path, model_name: str) -> list[dict]:
    """Load curated test images and ground truth annotations."""
    model_dir = data_dir / model_name
    if not model_dir.exists():
        logger.warning("No test data directory: %s", model_dir)
        return []

    manifest_path = model_dir / "manifest.json"
    if not manifest_path.exists():
        logger.warning("No manifest.json in %s", model_dir)
        return []

    with open(manifest_path) as f:
        return json.load(f)


def _compute_detection_metrics(
    predictions: list[dict],
    ground_truth: list[dict],
    iou_threshold: float = 0.5,
) -> dict[str, float]:
    """Compute object detection metrics (mAP, per-class AP, recall)."""
    if not ground_truth:
        return {"mAP_50": 0.0, "person_ap": 0.0, "ppe_ap": 0.0, "violation_recall": 0.0}

    total_tp = 0
    total_fp = 0
    total_fn = 0
    class_tp: dict[str, int] = {}
    class_fp: dict[str, int] = {}
    class_fn: dict[str, int] = {}

    ppe_classes = {"hard_hat", "safety_vest", "no_hard_hat", "no_vest"}
    violation_classes = {"no_hard_hat", "no_vest"}

    for pred, gt in zip(predictions, ground_truth):
        pred_boxes = pred.get("detections", [])
        gt_boxes = gt.get("annotations", [])
        matched_gt = set()

        for pb in pred_boxes:
            cls = pb.get("class_name", "unknown")
            best_iou = 0.0
            best_idx = -1

            for i, gb in enumerate(gt_boxes):
                if i in matched_gt or gb.get("class_name") != cls:
                    continue
                iou = _compute_iou(pb.get("bbox", []), gb.get("bbox", []))
                if iou > best_iou:
                    best_iou = iou
                    best_idx = i

            if best_iou >= iou_threshold and best_idx >= 0:
                total_tp += 1
                class_tp[cls] = class_tp.get(cls, 0) + 1
                matched_gt.add(best_idx)
            else:
                total_fp += 1
                class_fp[cls] = class_fp.get(cls, 0) + 1

        for i, gb in enumerate(gt_boxes):
            if i not in matched_gt:
                cls = gb.get("class_name", "unknown")
                total_fn += 1
                class_fn[cls] = class_fn.get(cls, 0) + 1

    def _ap(cls_names: set[str]) -> float:
        tp = sum(class_tp.get(c, 0) for c in cls_names)
        fp = sum(class_fp.get(c, 0) for c in cls_names)
        fn = sum(class_fn.get(c, 0) for c in cls_names)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        return 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    all_classes = set(class_tp) | set(class_fp) | set(class_fn)
    per_class_ap = {c: _ap({c}) for c in all_classes}
    map_50 = sum(per_class_ap.values()) / len(per_class_ap) if per_class_ap else 0.0

    violation_tp = sum(class_tp.get(c, 0) for c in violation_classes)
    violation_fn = sum(class_fn.get(c, 0) for c in violation_classes)
    violation_recall = (
        violation_tp / (violation_tp + violation_fn)
        if (violation_tp + violation_fn) > 0
        else 0.0
    )

    return {
        "mAP_50": round(map_50, 4),
        "person_ap": round(per_class_ap.get("person", 0.0), 4),
        "ppe_ap": round(_ap(ppe_classes), 4),
        "violation_recall": round(violation_recall, 4),
    }


def _compute_classification_metrics(
    predictions: list[dict],
    ground_truth: list[dict],
) -> dict[str, float]:
    """Compute classification metrics (accuracy, macro F1)."""
    if not ground_truth:
        return {"accuracy": 0.0, "macro_f1": 0.0, "structural_f1": 0.0}

    structural_classes = {
        "crack", "spalling", "corrosion", "efflorescence",
        "exposed_rebar", "surface_deterioration",
    }

    correct = 0
    total = 0
    class_tp: dict[str, int] = {}
    class_fp: dict[str, int] = {}
    class_fn: dict[str, int] = {}

    for pred, gt in zip(predictions, ground_truth):
        pred_cls = pred.get("predicted_class", "")
        gt_cls = gt.get("true_class", "")
        total += 1
        if pred_cls == gt_cls:
            correct += 1
            class_tp[gt_cls] = class_tp.get(gt_cls, 0) + 1
        else:
            class_fp[pred_cls] = class_fp.get(pred_cls, 0) + 1
            class_fn[gt_cls] = class_fn.get(gt_cls, 0) + 1

    accuracy = correct / total if total > 0 else 0.0

    all_classes = set(class_tp) | set(class_fp) | set(class_fn)
    f1_scores = {}
    for cls in all_classes:
        tp = class_tp.get(cls, 0)
        fp = class_fp.get(cls, 0)
        fn = class_fn.get(cls, 0)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1_scores[cls] = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

    macro_f1 = sum(f1_scores.values()) / len(f1_scores) if f1_scores else 0.0

    structural_f1s = [f1_scores.get(c, 0.0) for c in structural_classes if c in f1_scores]
    structural_f1 = sum(structural_f1s) / len(structural_f1s) if structural_f1s else 0.0

    return {
        "accuracy": round(accuracy, 4),
        "macro_f1": round(macro_f1, 4),
        "structural_f1": round(structural_f1, 4),
    }


def _compute_iou(box1: list[float], box2: list[float]) -> float:
    """Compute IoU between two bounding boxes [x1, y1, x2, y2]."""
    if len(box1) < 4 or len(box2) < 4:
        return 0.0
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union = area1 + area2 - intersection
    return intersection / union if union > 0 else 0.0


def run_regression_test(
    model_name: str,
    model_path: str,
    data_dir: str = "ml/evaluation/regression_data",
    baselines: dict | None = None,
) -> RegressionResult:
    """Run regression tests for a specific model.

    Parameters
    ----------
    model_name:
        Model identifier ("safety_yolo" or "defect_vit").
    model_path:
        Path to the model weights.
    data_dir:
        Directory containing curated test images per model.
    baselines:
        Override default baselines.

    Returns
    -------
    RegressionResult with pass/fail and metrics.
    """
    start = time.time()
    baseline = (baselines or BASELINES).get(model_name, {})
    data_path = Path(data_dir)
    test_data = _load_test_images(data_path, model_name)
    failures: list[str] = []

    if not test_data:
        return RegressionResult(
            model_name=model_name,
            passed=False,
            failures=[f"No test data found in {data_path / model_name}"],
            duration_seconds=time.time() - start,
        )

    # Run inference
    predictions = _run_inference(model_name, model_path, test_data)

    # Compute metrics
    if model_name == "safety_yolo":
        metrics = _compute_detection_metrics(predictions, test_data)
    elif model_name == "defect_vit":
        metrics = _compute_classification_metrics(predictions, test_data)
    else:
        metrics = {}
        failures.append(f"Unknown model type: {model_name}")

    # Check inference latency
    if predictions:
        latencies = [p.get("inference_ms", 0) for p in predictions]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        metrics["avg_inference_ms"] = round(avg_latency, 1)

        max_allowed = baseline.get("max_inference_ms", float("inf"))
        if avg_latency > max_allowed:
            failures.append(
                f"Latency regression: {avg_latency:.1f}ms > {max_allowed}ms"
            )

    # Check against baselines
    for metric_name, threshold in baseline.items():
        if metric_name == "max_inference_ms":
            continue
        actual = metrics.get(metric_name, 0.0)
        if actual < threshold:
            failures.append(
                f"{metric_name}: {actual:.4f} < baseline {threshold:.4f}"
            )

    duration = time.time() - start
    passed = len(failures) == 0

    result = RegressionResult(
        model_name=model_name,
        passed=passed,
        metrics=metrics,
        failures=failures,
        duration_seconds=round(duration, 2),
    )

    status = "PASSED" if passed else "FAILED"
    logger.info(
        "Regression test %s for %s: %s (%.1fs)",
        status,
        model_name,
        json.dumps(metrics),
        duration,
    )

    return result


def _run_inference(
    model_name: str,
    model_path: str,
    test_data: list[dict],
) -> list[dict]:
    """Run model inference on test images.

    Attempts to load the actual model; falls back to returning empty
    predictions if dependencies aren't available.
    """
    predictions = []
    model_file = Path(model_path)

    if not model_file.exists():
        logger.warning("Model not found at %s, using empty predictions", model_path)
        return [{"detections": [], "inference_ms": 0} for _ in test_data]

    if model_name == "safety_yolo":
        try:
            from ultralytics import YOLO

            model = YOLO(str(model_file))
            for item in test_data:
                img_path = item.get("image_path", "")
                if not Path(img_path).exists():
                    predictions.append({"detections": [], "inference_ms": 0})
                    continue
                t0 = time.time()
                results = model(img_path, verbose=False)
                inference_ms = (time.time() - t0) * 1000
                detections = []
                for r in results:
                    for box in r.boxes:
                        detections.append({
                            "class_name": r.names[int(box.cls)],
                            "confidence": float(box.conf),
                            "bbox": box.xyxy[0].tolist(),
                        })
                predictions.append({
                    "detections": detections,
                    "inference_ms": round(inference_ms, 1),
                })
        except ImportError:
            logger.warning("ultralytics not available, returning empty predictions")
            return [{"detections": [], "inference_ms": 0} for _ in test_data]

    elif model_name == "defect_vit":
        try:
            import timm
            import torch
            from PIL import Image
            from torchvision import transforms

            device = "cuda" if torch.cuda.is_available() else "cpu"
            model = timm.create_model("vit_base_patch16_224", pretrained=False, num_classes=8)
            state = torch.load(str(model_file), map_location=device, weights_only=True)
            model.load_state_dict(state)
            model.eval().to(device)

            class_names = [
                "crack", "spalling", "corrosion", "efflorescence",
                "exposed_rebar", "surface_deterioration", "biological_growth", "no_defect",
            ]

            transform = transforms.Compose([
                transforms.Resize(256),
                transforms.CenterCrop(224),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])

            for item in test_data:
                img_path = item.get("image_path", "")
                if not Path(img_path).exists():
                    predictions.append({"predicted_class": "", "inference_ms": 0})
                    continue
                img = Image.open(img_path).convert("RGB")
                tensor = transform(img).unsqueeze(0).to(device)
                t0 = time.time()
                with torch.no_grad():
                    output = model(tensor)
                inference_ms = (time.time() - t0) * 1000
                pred_idx = output.argmax(dim=1).item()
                predictions.append({
                    "predicted_class": class_names[pred_idx],
                    "confidence": float(output.softmax(dim=1).max()),
                    "inference_ms": round(inference_ms, 1),
                })
        except ImportError:
            logger.warning("timm/torch not available, returning empty predictions")
            return [{"predicted_class": "", "inference_ms": 0} for _ in test_data]

    return predictions


def run_all_regression_tests(
    models_dir: str = "models",
    data_dir: str = "ml/evaluation/regression_data",
) -> dict[str, RegressionResult]:
    """Run regression tests for all registered models."""
    results = {}

    model_configs = {
        "safety_yolo": f"{models_dir}/safety_yolo_v1.0/best.pt",
        "defect_vit": f"{models_dir}/defect_vit_v1.1/best_model.pth",
    }

    for model_name, model_path in model_configs.items():
        results[model_name] = run_regression_test(
            model_name=model_name,
            model_path=model_path,
            data_dir=data_dir,
        )

    all_passed = all(r.passed for r in results.values())
    logger.info(
        "Regression suite: %s (%d/%d models passed)",
        "ALL PASSED" if all_passed else "FAILURES DETECTED",
        sum(1 for r in results.values() if r.passed),
        len(results),
    )

    return results


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="CV Model Regression Tests")
    parser.add_argument("--models-dir", default="models")
    parser.add_argument("--data-dir", default="ml/evaluation/regression_data")
    parser.add_argument("--model", choices=["safety_yolo", "defect_vit"])
    args = parser.parse_args()

    if args.model:
        configs = {
            "safety_yolo": f"{args.models_dir}/safety_yolo_v1.0/best.pt",
            "defect_vit": f"{args.models_dir}/defect_vit_v1.1/best_model.pth",
        }
        result = run_regression_test(
            model_name=args.model,
            model_path=configs[args.model],
            data_dir=args.data_dir,
        )
        print(json.dumps({"passed": result.passed, "metrics": result.metrics, "failures": result.failures}, indent=2))
    else:
        results = run_all_regression_tests(args.models_dir, args.data_dir)
        summary = {
            name: {"passed": r.passed, "metrics": r.metrics, "failures": r.failures}
            for name, r in results.items()
        }
        print(json.dumps(summary, indent=2))
