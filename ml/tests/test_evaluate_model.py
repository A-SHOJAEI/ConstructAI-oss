"""Tests for model evaluation functions."""
from __future__ import annotations

from ml.training.evaluate_model import _compute_iou, compute_metrics


class TestEvaluateModel:
    def test_iou_perfect_overlap(self):
        box = [0, 0, 100, 100]
        assert _compute_iou(box, box) == 1.0

    def test_iou_no_overlap(self):
        box1 = [0, 0, 50, 50]
        box2 = [100, 100, 200, 200]
        assert _compute_iou(box1, box2) == 0.0

    def test_iou_partial_overlap(self):
        box1 = [0, 0, 100, 100]
        box2 = [50, 50, 150, 150]
        iou = _compute_iou(box1, box2)
        assert 0.0 < iou < 1.0
        # Intersection: 50x50 = 2500, Union: 10000 + 10000 - 2500 = 17500
        assert abs(iou - 2500 / 17500) < 0.01

    def test_compute_metrics_empty(self):
        result = compute_metrics([], [])
        assert result["mAP50"] == 0.0

    def test_compute_metrics_perfect(self):
        gt = [{"bbox": [0, 0, 100, 100], "class_id": 0}]
        pred = [{"bbox": [0, 0, 100, 100], "class_id": 0, "confidence": 0.99}]
        result = compute_metrics(pred, gt)
        assert result["precision"] == 1.0
        assert result["recall"] == 1.0

    def test_compute_metrics_missed(self):
        gt = [{"bbox": [0, 0, 100, 100], "class_id": 0}]
        pred = []
        result = compute_metrics(pred, gt)
        assert result["recall"] == 0.0
        assert result["false_negatives"] == 1
