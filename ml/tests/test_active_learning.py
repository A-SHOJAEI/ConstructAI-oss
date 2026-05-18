"""Tests for the active learning pipeline."""
from __future__ import annotations

from ml.training.active_learning import (
    compute_uncertainty_scores,
    select_samples_for_annotation,
)


class TestActiveLearning:
    def test_uncertainty_no_detections(self):
        preds = [{"image_path": "test.jpg", "detections": []}]
        scored = compute_uncertainty_scores(preds)
        assert scored[0]["uncertainty"] == 1.0

    def test_uncertainty_high_confidence(self):
        preds = [
            {
                "image_path": "test.jpg",
                "detections": [{"confidence": 0.99, "class_name": "person"}],
            }
        ]
        scored = compute_uncertainty_scores(preds)
        assert scored[0]["uncertainty"] < 0.5

    def test_uncertainty_low_confidence(self):
        preds = [
            {
                "image_path": "test.jpg",
                "detections": [{"confidence": 0.51, "class_name": "person"}],
            }
        ]
        scored = compute_uncertainty_scores(preds)
        assert scored[0]["uncertainty"] > 0.4

    def test_select_uncertainty_strategy(self):
        preds = [
            {"image_path": f"img{i}.jpg", "detections": [{"confidence": i * 0.1, "class_name": "person"}]}
            for i in range(1, 11)
        ]
        selected = select_samples_for_annotation(preds, budget=3, strategy="uncertainty")
        assert len(selected) == 3
        # Most uncertain should be first
        assert selected[0]["uncertainty"] >= selected[1]["uncertainty"]

    def test_select_random_strategy(self):
        preds = [
            {"image_path": f"img{i}.jpg", "detections": []}
            for i in range(20)
        ]
        selected = select_samples_for_annotation(preds, budget=5, strategy="random")
        assert len(selected) == 5

    def test_budget_exceeds_samples(self):
        preds = [{"image_path": "test.jpg", "detections": []}]
        selected = select_samples_for_annotation(preds, budget=100, strategy="uncertainty")
        assert len(selected) == 1
