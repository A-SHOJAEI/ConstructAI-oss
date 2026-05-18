"""Tests for drift detection."""

from __future__ import annotations

from app.services.evaluation.drift_monitor import (
    DriftMonitor,
)


class TestDriftMonitor:
    async def test_no_drift(self):
        monitor = DriftMonitor()
        monitor.set_reference(
            "accuracy",
            [0.88, 0.90, 0.87, 0.91, 0.89],
        )
        monitor.add_current(
            "accuracy",
            [0.89, 0.88, 0.90, 0.87, 0.91],
        )
        drifts = await monitor.detect_drift()
        assert len(drifts) == 1
        assert drifts[0]["drifted"] is False

    async def test_drift_detected(self):
        monitor = DriftMonitor()
        monitor.set_reference(
            "accuracy",
            [0.90, 0.90, 0.90, 0.90, 0.90],
        )
        monitor.add_current(
            "accuracy",
            [0.50, 0.52, 0.48, 0.51, 0.49],
        )
        drifts = await monitor.detect_drift()
        assert len(drifts) == 1
        assert drifts[0]["drifted"] is True

    async def test_threshold_parameter(self):
        monitor = DriftMonitor()
        monitor.set_reference("m1", [1.0, 1.0, 1.0])
        monitor.add_current("m1", [0.8, 0.8, 0.8])
        # Very high threshold = no drift detected
        drifts = await monitor.detect_drift(threshold=100.0)
        assert drifts[0]["drifted"] is False

    async def test_multiple_metrics(self):
        monitor = DriftMonitor()
        monitor.set_reference("m1", [0.9] * 5)
        monitor.set_reference("m2", [0.8] * 5)
        monitor.add_current("m1", [0.9] * 5)
        monitor.add_current("m2", [0.3] * 5)
        drifts = await monitor.detect_drift()
        assert len(drifts) == 2
        m1_drift = next(d for d in drifts if d["metric_name"] == "m1")
        m2_drift = next(d for d in drifts if d["metric_name"] == "m2")
        assert m1_drift["drifted"] is False
        assert m2_drift["drifted"] is True

    async def test_clear(self):
        monitor = DriftMonitor()
        monitor.set_reference("m1", [1.0])
        monitor.clear()
        drifts = await monitor.detect_drift()
        assert len(drifts) == 0
