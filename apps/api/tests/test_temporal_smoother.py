"""Tests for the TemporalSmoother false-positive reducer.

Sustained-detection smoothing: a detection only "confirms" once enough
consecutive frames in the sliding window agree. Pin the threshold,
the per-(track, violation) isolation, and the stale-cleanup behaviour.
"""

from __future__ import annotations

from app.services.vision.temporal_smoother import TemporalSmoother


def test_initial_window_below_size_returns_false():
    """The smoother needs at least ``window_size`` samples before
    reporting confirmation — partial windows always return False."""
    s = TemporalSmoother(window_size=5, threshold_pct=0.7)
    for _ in range(4):
        confirmed = s.update("track-1", "missing_hardhat", detected=True)
        assert confirmed is False


def test_full_window_above_threshold_confirms():
    """Once the window fills with mostly-True samples, the confirmation
    fires."""
    s = TemporalSmoother(window_size=5, threshold_pct=0.6)
    for _ in range(5):
        confirmed = s.update("track-1", "missing_hardhat", detected=True)
    # 5/5 detected → 100% ≥ 60%, confirmed
    assert confirmed is True


def test_full_window_below_threshold_does_not_confirm():
    """A few flicker frames in a 5-frame window keep it under the 70%
    threshold — false-positive suppressed."""
    s = TemporalSmoother(window_size=5, threshold_pct=0.7)
    pattern = [True, False, True, False, True]  # 3/5 = 60%, < 70%
    for d in pattern:
        confirmed = s.update("track-1", "missing_hardhat", detected=d)
    assert confirmed is False


def test_threshold_inclusive_boundary():
    """The threshold check is ≥, so exactly at 70% confirms."""
    s = TemporalSmoother(window_size=10, threshold_pct=0.7)
    pattern = [True] * 7 + [False] * 3
    for d in pattern:
        confirmed = s.update("track-1", "missing_hardhat", detected=d)
    assert confirmed is True


def test_track_violation_pairs_isolated():
    """Different (track, violation) pairs don't share a window."""
    s = TemporalSmoother(window_size=5, threshold_pct=0.5)

    # Track-A confirmed for missing_hardhat:
    for _ in range(5):
        s.update("track-A", "missing_hardhat", detected=True)

    # Track-B and missing_vest — separate window — first sample
    # alone is not enough.
    confirmed_b = s.update("track-B", "missing_vest", detected=True)
    assert confirmed_b is False  # window not full yet


def test_sliding_window_evicts_oldest():
    """When a 6th sample arrives in a 5-slot window, the oldest is
    evicted — proves the deque(maxlen) semantics."""
    s = TemporalSmoother(window_size=3, threshold_pct=0.66)

    # Fill with True:
    for _ in range(3):
        s.update("track-1", "x", detected=True)
    assert s.windows["track-1:x"][0] is True

    # New False sample evicts oldest True:
    s.update("track-1", "x", detected=False)
    # Window now has [True, True, False] → 2/3 = 66%, just at threshold.
    confirmed = s.update("track-1", "x", detected=False)
    # Window is now [True, False, False] → 1/3 = 33% < 66% → not confirmed.
    assert confirmed is False


def test_clear_stale_removes_old_keys():
    """Stale windows (no updates within max_age_seconds) are pruned —
    protects against unbounded memory growth as track IDs come and go."""
    import time as _time

    s = TemporalSmoother(window_size=3, threshold_pct=0.5)
    s.update("track-1", "x", detected=True)
    # Manually rewind the timestamp:
    s._last_update["track-1:x"] = _time.monotonic() - 60
    s.update("track-2", "x", detected=True)  # fresh

    s.clear_stale(max_age_seconds=30.0)
    assert "track-1:x" not in s.windows
    assert "track-2:x" in s.windows


def test_clear_stale_with_no_old_keys_is_noop():
    s = TemporalSmoother()
    s.update("track-1", "x", detected=True)
    s.clear_stale(max_age_seconds=30.0)
    assert "track-1:x" in s.windows


def test_reset_drops_all_state():
    s = TemporalSmoother(window_size=3, threshold_pct=0.5)
    for _ in range(3):
        s.update("track-1", "x", detected=True)
    s.reset()
    assert s.windows == {}
    assert s._last_update == {}


def test_default_window_and_threshold():
    """Pin the documented defaults (window=10, threshold=0.70) so a
    refactor doesn't quietly weaken the smoothing — affects
    false-positive rate of every safety alert."""
    s = TemporalSmoother()
    assert s.window_size == 10
    assert s.threshold_pct == 0.70


def test_alternating_pattern_below_threshold_never_confirms():
    """A 50/50 alternating pattern (sensor flicker) must NOT confirm
    at the default 70% threshold — that's the whole point of the
    smoother."""
    s = TemporalSmoother()
    confirmed = False
    for i in range(50):
        confirmed = s.update("track-1", "x", detected=(i % 2 == 0))
    assert confirmed is False


def test_recovery_from_partial_negatives():
    """If detection comes back to consistently True after some flicker,
    the smoother eventually confirms."""
    s = TemporalSmoother(window_size=10, threshold_pct=0.7)
    # Some flickers:
    for d in [True, False, True, False, True]:
        s.update("track-1", "x", detected=d)
    # Then sustained True:
    for _ in range(10):
        confirmed = s.update("track-1", "x", detected=True)
    # Last-10 window now contains mostly Trues — confirmed.
    assert confirmed is True
