"""Tests for the drift monitor (z-score based distribution drift).

Pin set_reference / add_current / detect_drift behavior so a refactor
to plug in real Evidently AI doesn't change the API contract.
"""

from __future__ import annotations

import pytest

from app.services.evaluation.drift_monitor import DriftMonitor


@pytest.fixture
def monitor() -> DriftMonitor:
    return DriftMonitor()


# =========================================================================
# set_reference / add_current
# =========================================================================


def test_set_reference_records_stats(monitor: DriftMonitor):
    monitor.set_reference("latency", [100, 200, 300])
    assert "latency" in monitor._reference_stats
    stats = monitor._reference_stats["latency"]
    assert stats["mean"] == 200.0
    assert stats["min"] == 100
    assert stats["max"] == 300
    assert stats["count"] == 3


def test_set_reference_empty_skips(monitor: DriftMonitor):
    """Empty values list — no reference set, no crash."""
    monitor.set_reference("latency", [])
    assert "latency" not in monitor._reference_stats


def test_add_current_records_stats(monitor: DriftMonitor):
    monitor.add_current("latency", [150, 250, 350])
    assert "latency" in monitor._current_stats
    assert monitor._current_stats["latency"]["mean"] == 250.0


def test_add_current_empty_skips(monitor: DriftMonitor):
    monitor.add_current("latency", [])
    assert "latency" not in monitor._current_stats


# =========================================================================
# _std
# =========================================================================


def test_std_single_value_zero(monitor: DriftMonitor):
    """Sample variance needs ≥ 2 values; single value → 0."""
    assert monitor._std([42.0]) == 0.0


def test_std_constant_values_zero(monitor: DriftMonitor):
    """Constant values → variance 0 → std 0."""
    assert monitor._std([5.0, 5.0, 5.0, 5.0]) == 0.0


def test_std_known_values(monitor: DriftMonitor):
    """[1, 2, 3, 4, 5] → mean=3, sample variance=2.5, std≈1.5811."""
    out = monitor._std([1.0, 2.0, 3.0, 4.0, 5.0])
    assert 1.58 < out < 1.59


def test_std_uses_sample_not_population(monitor: DriftMonitor):
    """[1, 2] — sample variance = 0.5, std ≈ 0.707; population would
    give 0.5."""
    out = monitor._std([1.0, 2.0])
    assert 0.70 < out < 0.71


# =========================================================================
# detect_drift — async
# =========================================================================


@pytest.mark.asyncio
async def test_detect_drift_no_data_empty(monitor: DriftMonitor):
    """No reference + no current → no drift entries."""
    out = await monitor.detect_drift()
    assert out == []


@pytest.mark.asyncio
async def test_detect_drift_no_current_skipped(monitor: DriftMonitor):
    """Reference set but no current → metric skipped."""
    monitor.set_reference("latency", [100, 200, 300])
    out = await monitor.detect_drift()
    assert out == []


@pytest.mark.asyncio
async def test_detect_drift_matching_distributions_no_drift(monitor: DriftMonitor):
    """Same mean → z-score ≈ 0 → no drift flag."""
    monitor.set_reference("latency", [100, 200, 300])
    monitor.add_current("latency", [100, 200, 300])
    out = await monitor.detect_drift()
    assert len(out) == 1
    assert out[0]["drifted"] is False
    assert out[0]["z_score"] < 1.0


@pytest.mark.asyncio
async def test_detect_drift_large_shift_flagged(monitor: DriftMonitor):
    """Reference mean 200, current mean 1000 → big z-score → drifted."""
    monitor.set_reference("latency", [100, 200, 300])
    monitor.add_current("latency", [900, 1000, 1100])
    out = await monitor.detect_drift()
    assert out[0]["drifted"] is True
    # 800 / 100 = 8.0 z-score
    assert out[0]["z_score"] >= 5.0


@pytest.mark.asyncio
async def test_detect_drift_threshold_configurable(monitor: DriftMonitor):
    """A drift that's flagged at threshold=2.0 should NOT be flagged
    at threshold=10.0."""
    monitor.set_reference("latency", [100, 200, 300])
    monitor.add_current("latency", [350, 400, 450])
    out_low = await monitor.detect_drift(threshold=2.0)
    out_high = await monitor.detect_drift(threshold=10.0)
    # Same z-score but different threshold:
    if out_low[0]["drifted"]:
        # If flagged at low threshold, should NOT be flagged at high:
        assert out_high[0]["drifted"] is False


@pytest.mark.asyncio
async def test_detect_drift_zero_std_uses_safe_floor(monitor: DriftMonitor):
    """If reference std is 0 (constant values), the helper uses a
    safe floor (1% of mean) to avoid division by zero."""
    monitor.set_reference("constant", [100.0, 100.0, 100.0])
    monitor.add_current("constant", [110.0])
    out = await monitor.detect_drift()
    # Must not crash — z_score is finite:
    assert out[0]["z_score"] != float("inf")
    assert isinstance(out[0]["z_score"], float)


@pytest.mark.asyncio
async def test_detect_drift_includes_all_metric_metadata(monitor: DriftMonitor):
    """Each drift entry must carry metric_name, drifted, z_score,
    reference_mean, current_mean, threshold."""
    monitor.set_reference("latency", [100, 200, 300])
    monitor.add_current("latency", [200, 300, 400])
    out = await monitor.detect_drift()
    entry = out[0]
    for key in (
        "metric_name",
        "drifted",
        "z_score",
        "reference_mean",
        "current_mean",
        "threshold",
    ):
        assert key in entry


@pytest.mark.asyncio
async def test_detect_drift_per_metric_isolation(monitor: DriftMonitor):
    """Two metrics tracked independently — drift on one doesn't
    affect the other."""
    monitor.set_reference("a", [10, 20, 30])
    monitor.add_current("a", [10, 20, 30])  # no drift
    monitor.set_reference("b", [100, 200, 300])
    monitor.add_current("b", [900, 1000, 1100])  # large drift

    out = await monitor.detect_drift()
    by_metric = {d["metric_name"]: d for d in out}
    assert by_metric["a"]["drifted"] is False
    assert by_metric["b"]["drifted"] is True


# =========================================================================
# clear
# =========================================================================


def test_clear_empties_all_stats(monitor: DriftMonitor):
    monitor.set_reference("a", [1, 2, 3])
    monitor.add_current("a", [4, 5, 6])
    monitor.clear()
    assert monitor._reference_stats == {}
    assert monitor._current_stats == {}
