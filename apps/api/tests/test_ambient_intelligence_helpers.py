"""Tests for the pure helpers in services/field/ambient_intelligence.

Pin the worker-hours pairing, equipment utilization aggregator, and
the GPS site-zone clustering. The full ingest functions are
DB-backed; these helpers are pure compute and run without fixtures.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.field.ambient_intelligence import (
    DEFAULT_MISSING_CHECKOUT_HOURS,
    _compute_equipment_utilization,
    _compute_worker_hours,
    _detect_site_zones,
)

# =========================================================================
# Helpers
# =========================================================================


def _ts(hour: int, minute: int = 0) -> datetime:
    """Build a datetime on a fixed reference day."""
    return datetime(2026, 4, 25, hour, minute, tzinfo=UTC)


# =========================================================================
# _compute_worker_hours
# =========================================================================


def test_worker_hours_simple_pair():
    """Single check_in / check_out pair → exact delta in hours."""
    events = [
        {"worker_id": "w1", "event_type": "check_in", "timestamp": _ts(7), "trade": "mason"},
        {"worker_id": "w1", "event_type": "check_out", "timestamp": _ts(15), "trade": "mason"},
    ]
    out = _compute_worker_hours(events)
    assert out["w1"]["hours"] == 8.0
    assert out["w1"]["trade"] == "mason"
    assert out["w1"]["in"] == _ts(7)
    assert out["w1"]["out"] == _ts(15)


def test_worker_hours_break_deducted():
    """Lunch break (1 hour) must be subtracted from worked hours."""
    events = [
        {"worker_id": "w1", "event_type": "check_in", "timestamp": _ts(7)},
        {"worker_id": "w1", "event_type": "break_start", "timestamp": _ts(12)},
        {"worker_id": "w1", "event_type": "break_end", "timestamp": _ts(13)},
        {"worker_id": "w1", "event_type": "check_out", "timestamp": _ts(15)},
    ]
    out = _compute_worker_hours(events)
    # 8 hours raw - 1 hour break = 7 hours worked
    assert out["w1"]["hours"] == 7.0


def test_worker_hours_multiple_breaks_deducted():
    events = [
        {"worker_id": "w1", "event_type": "check_in", "timestamp": _ts(7)},
        {"worker_id": "w1", "event_type": "break_start", "timestamp": _ts(10)},
        {"worker_id": "w1", "event_type": "break_end", "timestamp": _ts(10, 15)},  # 0.25h
        {"worker_id": "w1", "event_type": "break_start", "timestamp": _ts(12)},
        {"worker_id": "w1", "event_type": "break_end", "timestamp": _ts(13)},  # 1h
        {"worker_id": "w1", "event_type": "check_out", "timestamp": _ts(15)},
    ]
    out = _compute_worker_hours(events)
    # 8 - (0.25 + 1) = 6.75
    assert out["w1"]["hours"] == 6.75


def test_worker_hours_missing_checkout_uses_default():
    """If a worker checks in but never checks out, assume the default
    8-hour shift (with break deductions still honored)."""
    events = [
        {"worker_id": "w1", "event_type": "check_in", "timestamp": _ts(7)},
    ]
    out = _compute_worker_hours(events)
    assert out["w1"]["hours"] == DEFAULT_MISSING_CHECKOUT_HOURS
    assert out["w1"]["out"] is None  # explicit "no check_out"


def test_worker_hours_multiple_workers_independent():
    events = [
        {"worker_id": "w1", "event_type": "check_in", "timestamp": _ts(7)},
        {"worker_id": "w2", "event_type": "check_in", "timestamp": _ts(8)},
        {"worker_id": "w1", "event_type": "check_out", "timestamp": _ts(15)},
        {"worker_id": "w2", "event_type": "check_out", "timestamp": _ts(17)},
    ]
    out = _compute_worker_hours(events)
    assert out["w1"]["hours"] == 8.0
    assert out["w2"]["hours"] == 9.0


def test_worker_hours_events_sorted_by_timestamp():
    """Events arriving out of order must be re-sorted before pairing."""
    events = [
        {"worker_id": "w1", "event_type": "check_out", "timestamp": _ts(15)},
        {"worker_id": "w1", "event_type": "check_in", "timestamp": _ts(7)},
    ]
    out = _compute_worker_hours(events)
    assert out["w1"]["hours"] == 8.0


def test_worker_hours_negative_clamped_to_zero():
    """If breaks exceed worked time (data error from rapid break
    pairs), clamp to 0 — never return negative. With breaks ordered
    BEFORE check_out, the total can exceed the work delta."""
    events = [
        {"worker_id": "w1", "event_type": "check_in", "timestamp": _ts(7)},
        # First break: 7:00:30 - 7:30 = 29.5 min
        {"worker_id": "w1", "event_type": "break_start", "timestamp": _ts(7, 0)},
        {"worker_id": "w1", "event_type": "break_end", "timestamp": _ts(7, 30)},
        # Second break: 7:30 - 7:55 = 25 min → total 54.5 min
        {"worker_id": "w1", "event_type": "break_start", "timestamp": _ts(7, 30)},
        {"worker_id": "w1", "event_type": "break_end", "timestamp": _ts(7, 55)},
        # Check out at 7:45 → delta = 45 min, but total_break_hours after
        # first break is 0.5h. Clamp keeps hours ≥ 0.
        {"worker_id": "w1", "event_type": "check_out", "timestamp": _ts(7, 30)},
    ]
    out = _compute_worker_hours(events)
    # Whatever the sort + pairing produces, hours must be ≥ 0.
    assert out["w1"]["hours"] >= 0.0


def test_worker_hours_empty_events():
    assert _compute_worker_hours([]) == {}


def test_worker_hours_break_without_start_ignored():
    """A bare break_end with no preceding break_start should not crash —
    just gets ignored."""
    events = [
        {"worker_id": "w1", "event_type": "check_in", "timestamp": _ts(7)},
        {"worker_id": "w1", "event_type": "break_end", "timestamp": _ts(10)},  # orphan
        {"worker_id": "w1", "event_type": "check_out", "timestamp": _ts(15)},
    ]
    out = _compute_worker_hours(events)
    assert out["w1"]["hours"] == 8.0  # no break deducted


# =========================================================================
# _compute_equipment_utilization
# =========================================================================


def test_equipment_utilization_single_reading_returns_zero():
    """One reading is not enough to compute deltas → utilization 0."""
    telemetry = [
        {
            "equipment_id": "ex-1",
            "equipment_type": "excavator",
            "timestamp": _ts(8),
            "status": "running",
        }
    ]
    out = _compute_equipment_utilization(telemetry)
    assert len(out) == 1
    assert out[0]["utilization_pct"] == 0.0
    assert out[0]["readings_count"] == 1


def test_equipment_utilization_running_full_window():
    """All-running readings → 100% utilization."""
    telemetry = [
        {"equipment_id": "ex-1", "timestamp": _ts(8), "status": "running"},
        {"equipment_id": "ex-1", "timestamp": _ts(9), "status": "running"},
        {"equipment_id": "ex-1", "timestamp": _ts(10), "status": "running"},
    ]
    out = _compute_equipment_utilization(telemetry)
    # 2 hour deltas, both running → 100%
    assert out[0]["utilization_pct"] == 100.0
    assert out[0]["running_hours"] == 2.0


def test_equipment_utilization_mixed_running_idle():
    telemetry = [
        {"equipment_id": "ex-1", "timestamp": _ts(8), "status": "running"},
        {"equipment_id": "ex-1", "timestamp": _ts(9), "status": "idle"},
        {"equipment_id": "ex-1", "timestamp": _ts(10), "status": "running"},
        {"equipment_id": "ex-1", "timestamp": _ts(11), "status": "idle"},
    ]
    out = _compute_equipment_utilization(telemetry)
    # 3 deltas, status[i] applied to each:
    # 8→9: running (1h), 9→10: idle (1h), 10→11: running (1h)
    # Total running: 2h, idle: 1h → 2/3 ≈ 66.7%
    assert out[0]["utilization_pct"] == 66.7
    assert out[0]["running_hours"] == 2.0
    assert out[0]["idle_hours"] == 1.0


def test_equipment_utilization_overnight_gap_clamped():
    """A reading at 8:00 followed by one at 18:00 next day produces a
    huge delta — must be clamped to 2h to avoid counting overnight gaps."""
    telemetry = [
        {"equipment_id": "ex-1", "timestamp": _ts(8), "status": "running"},
        # 26h later — way beyond the 2h clamp:
        {"equipment_id": "ex-1", "timestamp": _ts(8) + timedelta(hours=26), "status": "running"},
    ]
    out = _compute_equipment_utilization(telemetry)
    # Delta clamped to 2h, all running:
    assert out[0]["total_hours"] == 2.0


def test_equipment_utilization_fuel_consumed():
    telemetry = [
        {
            "equipment_id": "ex-1",
            "timestamp": _ts(8),
            "status": "running",
            "fuel_level_pct": 80.0,
        },
        {
            "equipment_id": "ex-1",
            "timestamp": _ts(15),
            "status": "running",
            "fuel_level_pct": 35.0,
        },
    ]
    out = _compute_equipment_utilization(telemetry)
    # Fuel consumed: 80 - 35 = 45 (rounded to 1dp)
    assert out[0]["fuel_consumed_pct"] == 45.0


def test_equipment_utilization_fuel_increasing_returns_none():
    """If fuel_level increased (e.g. mid-day refill), the consumption
    metric is meaningless → emit None instead of a negative number."""
    telemetry = [
        {
            "equipment_id": "ex-1",
            "timestamp": _ts(8),
            "status": "running",
            "fuel_level_pct": 30.0,
        },
        {
            "equipment_id": "ex-1",
            "timestamp": _ts(15),
            "status": "running",
            "fuel_level_pct": 90.0,
        },
    ]
    out = _compute_equipment_utilization(telemetry)
    assert out[0]["fuel_consumed_pct"] is None


def test_equipment_utilization_per_equipment_independent():
    """Two different machines → two separate result entries, computed
    independently."""
    telemetry = [
        {"equipment_id": "ex-1", "timestamp": _ts(8), "status": "running"},
        {"equipment_id": "ex-1", "timestamp": _ts(9), "status": "running"},
        {"equipment_id": "ld-2", "timestamp": _ts(8), "status": "idle"},
        {"equipment_id": "ld-2", "timestamp": _ts(9), "status": "idle"},
    ]
    out = _compute_equipment_utilization(telemetry)
    assert len(out) == 2
    by_id = {r["equipment_id"]: r for r in out}
    assert by_id["ex-1"]["utilization_pct"] == 100.0
    assert by_id["ld-2"]["utilization_pct"] == 0.0


def test_equipment_utilization_empty_telemetry():
    assert _compute_equipment_utilization([]) == []


# =========================================================================
# _detect_site_zones
# =========================================================================


def test_detect_zones_empty_returns_empty():
    assert _detect_site_zones([]) == []


def test_detect_zones_under_threshold_filtered():
    """Zones need at least 3 pings — fewer must not appear in output."""
    pings = [
        {"worker_id": "w1", "latitude": 40.0, "longitude": -74.0},
        {"worker_id": "w2", "latitude": 40.0, "longitude": -74.0},  # 2 < 3
    ]
    assert _detect_site_zones(pings) == []


def test_detect_zones_three_pings_in_one_cell_emits_zone():
    pings = [
        {"worker_id": "w1", "latitude": 40.0, "longitude": -74.0, "trade": "concrete"},
        {"worker_id": "w2", "latitude": 40.0, "longitude": -74.0, "trade": "concrete"},
        {"worker_id": "w3", "latitude": 40.0, "longitude": -74.0, "trade": "rebar"},
    ]
    zones = _detect_site_zones(pings)
    assert len(zones) == 1
    z = zones[0]
    assert z["worker_count"] == 3
    assert z["ping_count"] == 3
    # trades sorted alphabetically:
    assert z["trades"] == ["concrete", "rebar"]


def test_detect_zones_distant_pings_form_separate_zones():
    """Pings hundreds of meters apart → different grid cells → multiple
    zones."""
    pings = [
        # Cluster A:
        *[{"worker_id": f"a{i}", "latitude": 40.0, "longitude": -74.0} for i in range(3)],
        # Cluster B (≈1km north):
        *[{"worker_id": f"b{i}", "latitude": 40.01, "longitude": -74.0} for i in range(3)],
    ]
    zones = _detect_site_zones(pings, cluster_radius_m=50.0)
    assert len(zones) == 2


def test_detect_zones_unique_workers_counted_once():
    """5 pings from 2 workers → worker_count = 2, ping_count = 5."""
    pings = [
        {"worker_id": "w1", "latitude": 40.0, "longitude": -74.0},
        {"worker_id": "w1", "latitude": 40.0, "longitude": -74.0},
        {"worker_id": "w1", "latitude": 40.0, "longitude": -74.0},
        {"worker_id": "w2", "latitude": 40.0, "longitude": -74.0},
        {"worker_id": "w2", "latitude": 40.0, "longitude": -74.0},
    ]
    zones = _detect_site_zones(pings)
    assert len(zones) == 1
    assert zones[0]["worker_count"] == 2
    assert zones[0]["ping_count"] == 5


def test_detect_zones_center_is_average_of_cluster():
    """Zone center should be the centroid of the pings in the cell."""
    pings = [
        {"worker_id": f"w{i}", "latitude": 40.001 + i * 0.0001, "longitude": -74.0}
        for i in range(3)
    ]
    zones = _detect_site_zones(pings, cluster_radius_m=200.0)
    assert len(zones) == 1
    # Centroid lat ≈ 40.0011 (average of 40.001, 40.0011, 40.0012)
    assert 40.001 < zones[0]["center_lat"] < 40.002


def test_detect_zones_handles_missing_trade():
    """Pings without trade info shouldn't crash — trades list is just
    excluded for those entries."""
    pings = [
        {"worker_id": "w1", "latitude": 40.0, "longitude": -74.0},
        {"worker_id": "w2", "latitude": 40.0, "longitude": -74.0, "trade": "mason"},
        {"worker_id": "w3", "latitude": 40.0, "longitude": -74.0},
    ]
    zones = _detect_site_zones(pings)
    assert len(zones) == 1
    assert zones[0]["trades"] == ["mason"]
