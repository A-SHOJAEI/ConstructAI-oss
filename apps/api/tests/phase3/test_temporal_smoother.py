from __future__ import annotations

from app.services.vision.temporal_smoother import TemporalSmoother


class TestTemporalSmoother:
    def test_single_frame_not_confirmed(self):
        smoother = TemporalSmoother(window_size=10, threshold_pct=0.70)
        assert smoother.update("track1", "missing_hardhat", True) is False

    def test_sustained_violation_confirmed(self):
        smoother = TemporalSmoother(window_size=10, threshold_pct=0.70)
        for _ in range(9):
            smoother.update("track1", "missing_hardhat", True)
        # 10/10 = 100% >= 70% (window now full)
        assert smoother.update("track1", "missing_hardhat", True) is True

    def test_intermittent_detection_not_confirmed(self):
        smoother = TemporalSmoother(window_size=10, threshold_pct=0.70)
        for i in range(10):
            smoother.update("track1", "missing_hardhat", i % 2 == 0)
        # 5/10 = 50% < 70%
        result = smoother.update("track1", "missing_hardhat", False)
        assert result is False

    def test_window_slides(self):
        smoother = TemporalSmoother(window_size=5, threshold_pct=0.60)
        for _ in range(5):
            smoother.update("track1", "violation", True)
        assert smoother.update("track1", "violation", True) is True
        # Now add False entries to push positives out
        for _ in range(5):
            smoother.update("track1", "violation", False)
        assert smoother.update("track1", "violation", False) is False

    def test_different_violations_independent(self):
        smoother = TemporalSmoother(window_size=3, threshold_pct=0.60)
        for _ in range(3):
            smoother.update("track1", "missing_hardhat", True)
        assert smoother.update("track1", "missing_hardhat", True) is True
        # Different violation type should not be confirmed
        assert smoother.update("track1", "missing_vest", True) is False

    def test_clear_stale(self):
        smoother = TemporalSmoother(window_size=5, threshold_pct=0.50)
        smoother.update("track1", "violation", True)
        assert len(smoother.windows) == 1
        smoother.clear_stale(max_age_seconds=0)
        assert len(smoother.windows) == 0
