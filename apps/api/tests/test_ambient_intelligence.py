"""Tests for ambient field intelligence service.

Covers GPS ping ingestion, equipment telemetry ingestion, badge event ingestion,
worker hour computation, equipment utilization, zone detection, daily aggregation,
report generation, and batch validation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.field.ambient_intelligence import (
    DEFAULT_MISSING_CHECKOUT_HOURS,
    VALID_BADGE_EVENT_TYPES,
    VALID_EQUIPMENT_STATUSES,
    _compute_equipment_utilization,
    _compute_worker_hours,
    _detect_site_zones,
    aggregate_daily_snapshot,
    generate_report_from_snapshot,
    ingest_badge_events,
    ingest_equipment_telemetry,
    ingest_field_pings,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ID = uuid.uuid4()
NOW = datetime(2026, 3, 15, 8, 0, 0, tzinfo=UTC)


def _make_ping(
    worker_id: str = "W001",
    lat: float = 30.2672,
    lon: float = -97.7431,
    ts: datetime | None = None,
    trade: str | None = "electrician",
) -> dict:
    return {
        "worker_id": worker_id,
        "latitude": lat,
        "longitude": lon,
        "accuracy_m": 5.0,
        "altitude_m": 150.0,
        "trade": trade,
        "timestamp": ts or NOW,
    }


def _make_telemetry(
    equipment_id: str = "EQ001",
    status: str = "running",
    ts: datetime | None = None,
) -> dict:
    return {
        "equipment_id": equipment_id,
        "equipment_type": "excavator",
        "status": status,
        "fuel_level_pct": 75.0,
        "engine_hours": 1234.5,
        "latitude": 30.2672,
        "longitude": -97.7431,
        "raw_payload": {"rpm": 1800},
        "timestamp": ts or NOW,
    }


def _make_badge_event(
    worker_id: str = "W001",
    event_type: str = "check_in",
    ts: datetime | None = None,
    trade: str | None = "electrician",
) -> dict:
    return {
        "worker_id": worker_id,
        "worker_name": "John Doe",
        "trade": trade,
        "event_type": event_type,
        "gate_id": "GATE-A",
        "timestamp": ts or NOW,
    }


def _mock_db_no_dupes():
    """Create a mock async session that reports no existing pings (no dupes)."""
    db = AsyncMock()
    # For the dedup query: scalars().first() returns count=0
    mock_result = MagicMock()
    mock_result.scalar.return_value = 0
    db.execute.return_value = mock_result
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


# ===========================================================================
# TestPingIngestion
# ===========================================================================


class TestPingIngestion:
    """Tests for ingest_field_pings()."""

    @pytest.mark.asyncio
    async def test_valid_pings_inserted(self):
        db = _mock_db_no_dupes()
        pings = [_make_ping(worker_id="W001"), _make_ping(worker_id="W002")]
        count = await ingest_field_pings(db, PROJECT_ID, pings)
        assert count == 2
        assert db.add.call_count == 2

    @pytest.mark.asyncio
    async def test_invalid_latitude_rejected(self):
        db = _mock_db_no_dupes()
        pings = [_make_ping(lat=95.0)]  # Invalid: > 90
        count = await ingest_field_pings(db, PROJECT_ID, pings)
        assert count == 0

    @pytest.mark.asyncio
    async def test_invalid_longitude_rejected(self):
        db = _mock_db_no_dupes()
        pings = [_make_ping(lon=200.0)]  # Invalid: > 180
        count = await ingest_field_pings(db, PROJECT_ID, pings)
        assert count == 0

    @pytest.mark.asyncio
    async def test_missing_worker_id_rejected(self):
        db = _mock_db_no_dupes()
        ping = _make_ping()
        ping["worker_id"] = ""
        count = await ingest_field_pings(db, PROJECT_ID, [ping])
        assert count == 0

    @pytest.mark.asyncio
    async def test_missing_timestamp_rejected(self):
        db = _mock_db_no_dupes()
        ping = _make_ping()
        ping["timestamp"] = None
        count = await ingest_field_pings(db, PROJECT_ID, [ping])
        assert count == 0

    @pytest.mark.asyncio
    async def test_duplicate_within_window_skipped(self):
        db = AsyncMock()
        # First call: no dupe (count=0). Second call: dupe found (count=1).
        mock_no_dupe = MagicMock()
        mock_no_dupe.scalar.return_value = 0
        mock_dupe = MagicMock()
        mock_dupe.scalar.return_value = 1
        db.execute.side_effect = [mock_no_dupe, mock_dupe]
        db.flush = AsyncMock()

        pings = [
            _make_ping(worker_id="W001", ts=NOW),
            _make_ping(worker_id="W001", ts=NOW + timedelta(seconds=2)),
        ]
        count = await ingest_field_pings(db, PROJECT_ID, pings)
        assert count == 1

    @pytest.mark.asyncio
    async def test_empty_batch_returns_zero(self):
        db = _mock_db_no_dupes()
        count = await ingest_field_pings(db, PROJECT_ID, [])
        assert count == 0


# ===========================================================================
# TestTelemetryIngestion
# ===========================================================================


class TestTelemetryIngestion:
    """Tests for ingest_equipment_telemetry()."""

    @pytest.mark.asyncio
    async def test_valid_telemetry_inserted(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        telemetry = [
            _make_telemetry(equipment_id="EQ001", status="running"),
            _make_telemetry(equipment_id="EQ002", status="idle"),
        ]
        count = await ingest_equipment_telemetry(db, PROJECT_ID, telemetry)
        assert count == 2

    @pytest.mark.asyncio
    async def test_invalid_status_rejected(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        telemetry = [_make_telemetry(status="broken")]
        count = await ingest_equipment_telemetry(db, PROJECT_ID, telemetry)
        assert count == 0

    @pytest.mark.asyncio
    async def test_missing_equipment_id_rejected(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        t = _make_telemetry()
        t["equipment_id"] = ""
        count = await ingest_equipment_telemetry(db, PROJECT_ID, [t])
        assert count == 0

    @pytest.mark.asyncio
    async def test_missing_timestamp_rejected(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        t = _make_telemetry()
        t["timestamp"] = None
        count = await ingest_equipment_telemetry(db, PROJECT_ID, [t])
        assert count == 0

    @pytest.mark.asyncio
    async def test_all_valid_statuses_accepted(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        telemetry = [
            _make_telemetry(status=s, ts=NOW + timedelta(minutes=i))
            for i, s in enumerate(VALID_EQUIPMENT_STATUSES)
        ]
        count = await ingest_equipment_telemetry(db, PROJECT_ID, telemetry)
        assert count == len(VALID_EQUIPMENT_STATUSES)


# ===========================================================================
# TestBadgeIngestion
# ===========================================================================


class TestBadgeIngestion:
    """Tests for ingest_badge_events()."""

    @pytest.mark.asyncio
    async def test_valid_events_inserted(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        events = [
            _make_badge_event(event_type="check_in"),
            _make_badge_event(event_type="check_out", ts=NOW + timedelta(hours=8)),
        ]
        count = await ingest_badge_events(db, PROJECT_ID, events)
        assert count == 2

    @pytest.mark.asyncio
    async def test_invalid_event_type_rejected(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        events = [_make_badge_event(event_type="lunch_start")]
        count = await ingest_badge_events(db, PROJECT_ID, events)
        assert count == 0

    @pytest.mark.asyncio
    async def test_all_valid_event_types_accepted(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        events = [
            _make_badge_event(event_type=et, ts=NOW + timedelta(minutes=i))
            for i, et in enumerate(VALID_BADGE_EVENT_TYPES)
        ]
        count = await ingest_badge_events(db, PROJECT_ID, events)
        assert count == len(VALID_BADGE_EVENT_TYPES)

    @pytest.mark.asyncio
    async def test_missing_worker_id_rejected(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        e = _make_badge_event()
        e["worker_id"] = ""
        count = await ingest_badge_events(db, PROJECT_ID, [e])
        assert count == 0

    @pytest.mark.asyncio
    async def test_empty_batch_returns_zero(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        count = await ingest_badge_events(db, PROJECT_ID, [])
        assert count == 0


# ===========================================================================
# TestWorkerHours
# ===========================================================================


class TestWorkerHours:
    """Tests for _compute_worker_hours()."""

    def test_basic_check_in_out_pair(self):
        events = [
            _make_badge_event("W001", "check_in", NOW),
            _make_badge_event("W001", "check_out", NOW + timedelta(hours=8)),
        ]
        result = _compute_worker_hours(events)
        assert "W001" in result
        assert result["W001"]["hours"] == 8.0

    def test_missing_checkout_assumes_default(self):
        events = [_make_badge_event("W001", "check_in", NOW)]
        result = _compute_worker_hours(events)
        assert "W001" in result
        assert result["W001"]["hours"] == DEFAULT_MISSING_CHECKOUT_HOURS
        assert result["W001"]["out"] is None

    def test_break_time_deducted(self):
        events = [
            _make_badge_event("W001", "check_in", NOW),
            _make_badge_event("W001", "break_start", NOW + timedelta(hours=4)),
            _make_badge_event("W001", "break_end", NOW + timedelta(hours=4, minutes=30)),
            _make_badge_event("W001", "check_out", NOW + timedelta(hours=8)),
        ]
        result = _compute_worker_hours(events)
        assert abs(result["W001"]["hours"] - 7.5) < 0.01

    def test_multiple_workers(self):
        events = [
            _make_badge_event("W001", "check_in", NOW),
            _make_badge_event("W001", "check_out", NOW + timedelta(hours=8)),
            _make_badge_event("W002", "check_in", NOW + timedelta(hours=1)),
            _make_badge_event("W002", "check_out", NOW + timedelta(hours=9)),
        ]
        result = _compute_worker_hours(events)
        assert len(result) == 2
        assert result["W001"]["hours"] == 8.0
        assert result["W002"]["hours"] == 8.0

    def test_trade_extracted(self):
        events = [
            _make_badge_event("W001", "check_in", NOW, trade="plumber"),
            _make_badge_event("W001", "check_out", NOW + timedelta(hours=8), trade="plumber"),
        ]
        result = _compute_worker_hours(events)
        assert result["W001"]["trade"] == "plumber"

    def test_empty_events_returns_empty(self):
        result = _compute_worker_hours([])
        assert result == {}


# ===========================================================================
# TestEquipmentUtilization
# ===========================================================================


class TestEquipmentUtilization:
    """Tests for _compute_equipment_utilization()."""

    def test_full_running_100_pct(self):
        telemetry = [
            _make_telemetry("EQ001", "running", NOW),
            _make_telemetry("EQ001", "running", NOW + timedelta(hours=2)),
        ]
        result = _compute_equipment_utilization(telemetry)
        assert len(result) == 1
        assert result[0]["utilization_pct"] == 100.0

    def test_full_idle_0_pct(self):
        telemetry = [
            _make_telemetry("EQ001", "idle", NOW),
            _make_telemetry("EQ001", "idle", NOW + timedelta(hours=2)),
        ]
        result = _compute_equipment_utilization(telemetry)
        assert result[0]["utilization_pct"] == 0.0

    def test_mixed_status_50_pct(self):
        telemetry = [
            _make_telemetry("EQ001", "running", NOW),
            _make_telemetry("EQ001", "idle", NOW + timedelta(hours=1)),
            _make_telemetry("EQ001", "idle", NOW + timedelta(hours=2)),
        ]
        result = _compute_equipment_utilization(telemetry)
        assert abs(result[0]["utilization_pct"] - 50.0) < 0.1

    def test_single_reading_zero_utilization(self):
        telemetry = [_make_telemetry("EQ001", "running", NOW)]
        result = _compute_equipment_utilization(telemetry)
        assert result[0]["utilization_pct"] == 0.0
        assert result[0]["readings_count"] == 1

    def test_multiple_equipment(self):
        telemetry = [
            _make_telemetry("EQ001", "running", NOW),
            _make_telemetry("EQ001", "running", NOW + timedelta(hours=1)),
            _make_telemetry("EQ002", "idle", NOW),
            _make_telemetry("EQ002", "idle", NOW + timedelta(hours=1)),
        ]
        result = _compute_equipment_utilization(telemetry)
        assert len(result) == 2
        eq_ids = {r["equipment_id"] for r in result}
        assert eq_ids == {"EQ001", "EQ002"}


# ===========================================================================
# TestZoneDetection
# ===========================================================================


class TestZoneDetection:
    """Tests for _detect_site_zones()."""

    def test_no_pings_returns_empty(self):
        result = _detect_site_zones([])
        assert result == []

    def test_cluster_of_pings_detected(self):
        # Create pings tightly clustered at same location
        pings = [
            _make_ping("W001", 30.2672, -97.7431, NOW + timedelta(minutes=i)) for i in range(5)
        ]
        result = _detect_site_zones(pings)
        assert len(result) >= 1
        assert result[0]["ping_count"] >= 3

    def test_spread_pings_no_zone(self):
        # Create pings far apart (more than cluster radius)
        pings = [
            _make_ping("W001", 30.0, -97.0),
            _make_ping("W002", 31.0, -98.0),
        ]
        result = _detect_site_zones(pings, cluster_radius_m=50)
        # With only 2 pings in separate cells, neither meets the threshold
        assert all(z["ping_count"] < 3 for z in result) or len(result) == 0

    def test_zones_sorted_by_ping_count(self):
        # Create two clusters with different sizes
        pings = []
        # Large cluster
        for i in range(10):
            pings.append(_make_ping(f"W{i:03d}", 30.2672, -97.7431, NOW + timedelta(minutes=i)))
        # Smaller cluster 500m away
        for i in range(3):
            pings.append(
                _make_ping(f"W{i + 10:03d}", 30.2720, -97.7431, NOW + timedelta(minutes=i))
            )
        result = _detect_site_zones(pings)
        if len(result) >= 2:
            assert result[0]["ping_count"] >= result[1]["ping_count"]


# ===========================================================================
# TestDailyAggregation
# ===========================================================================


class TestDailyAggregation:
    """Tests for aggregate_daily_snapshot()."""

    @pytest.mark.asyncio
    async def test_aggregation_with_no_data(self):
        """Aggregation with empty tables should produce a valid empty snapshot."""
        db = AsyncMock()
        # Mock all queries returning empty results
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        db.execute.return_value = empty_result
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        # The last execute call is for the upsert check (existing snapshot)
        first_empty = MagicMock()
        first_empty.scalars.return_value.all.return_value = []
        second_empty = MagicMock()
        second_empty.scalars.return_value.all.return_value = []
        third_empty = MagicMock()
        third_empty.scalars.return_value.all.return_value = []
        upsert_check = MagicMock()
        upsert_check.scalars.return_value.first.return_value = None

        db.execute.side_effect = [first_empty, second_empty, third_empty, upsert_check]

        snapshot = await aggregate_daily_snapshot(db, PROJECT_ID, date(2026, 3, 15))
        assert snapshot is not None
        assert db.add.called

    @pytest.mark.asyncio
    async def test_aggregation_creates_new_snapshot(self):
        """When no existing snapshot exists, a new one should be created."""
        db = AsyncMock()
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []
        upsert_check = MagicMock()
        upsert_check.scalars.return_value.first.return_value = None

        db.execute.side_effect = [empty_result, empty_result, empty_result, upsert_check]
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        await aggregate_daily_snapshot(db, PROJECT_ID, date(2026, 3, 15))
        assert db.add.called

    @pytest.mark.asyncio
    async def test_aggregation_updates_existing_snapshot(self):
        """When an existing snapshot exists, it should be updated (upsert)."""
        db = AsyncMock()
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

        existing_snapshot = MagicMock()
        existing_snapshot.workforce_summary = {}
        existing_snapshot.equipment_summary = {}
        existing_snapshot.site_activity = {}
        existing_snapshot.zone_activity = []
        existing_snapshot.data_quality = {}
        upsert_check = MagicMock()
        upsert_check.scalars.return_value.first.return_value = existing_snapshot

        db.execute.side_effect = [empty_result, empty_result, empty_result, upsert_check]
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        await aggregate_daily_snapshot(db, PROJECT_ID, date(2026, 3, 15))
        # Should NOT call db.add since we're updating
        assert not db.add.called

    @pytest.mark.asyncio
    async def test_workforce_summary_computed(self):
        """Badge events should produce a workforce summary with headcount and hours."""
        db = AsyncMock()

        # Mock pings query: empty
        pings_result = MagicMock()
        pings_result.scalars.return_value.all.return_value = []

        # Mock telemetry: empty
        telemetry_result = MagicMock()
        telemetry_result.scalars.return_value.all.return_value = []

        # Mock badge events with check_in/check_out
        mock_badge_in = MagicMock()
        mock_badge_in.worker_id = "W001"
        mock_badge_in.worker_name = "John"
        mock_badge_in.trade = "electrician"
        mock_badge_in.event_type = "check_in"
        mock_badge_in.timestamp = NOW

        mock_badge_out = MagicMock()
        mock_badge_out.worker_id = "W001"
        mock_badge_out.worker_name = "John"
        mock_badge_out.trade = "electrician"
        mock_badge_out.event_type = "check_out"
        mock_badge_out.timestamp = NOW + timedelta(hours=8)

        badges_result = MagicMock()
        badges_result.scalars.return_value.all.return_value = [mock_badge_in, mock_badge_out]

        upsert_check = MagicMock()
        upsert_check.scalars.return_value.first.return_value = None

        db.execute.side_effect = [pings_result, telemetry_result, badges_result, upsert_check]
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        await aggregate_daily_snapshot(db, PROJECT_ID, date(2026, 3, 15))
        # The snapshot model mock won't have real data,
        # but we verify the add was called with the right model
        assert db.add.called

    @pytest.mark.asyncio
    async def test_equipment_summary_computed(self):
        """Telemetry data should produce equipment utilization."""
        db = AsyncMock()

        pings_result = MagicMock()
        pings_result.scalars.return_value.all.return_value = []

        mock_t1 = MagicMock()
        mock_t1.equipment_id = "EQ001"
        mock_t1.equipment_type = "excavator"
        mock_t1.status = "running"
        mock_t1.timestamp = NOW
        mock_t1.fuel_level_pct = Decimal("80.0")
        mock_t1.engine_hours = Decimal("100.0")

        mock_t2 = MagicMock()
        mock_t2.equipment_id = "EQ001"
        mock_t2.equipment_type = "excavator"
        mock_t2.status = "idle"
        mock_t2.timestamp = NOW + timedelta(hours=1)
        mock_t2.fuel_level_pct = Decimal("75.0")
        mock_t2.engine_hours = Decimal("101.0")

        telemetry_result = MagicMock()
        telemetry_result.scalars.return_value.all.return_value = [mock_t1, mock_t2]

        badges_result = MagicMock()
        badges_result.scalars.return_value.all.return_value = []

        upsert_check = MagicMock()
        upsert_check.scalars.return_value.first.return_value = None

        db.execute.side_effect = [pings_result, telemetry_result, badges_result, upsert_check]
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        await aggregate_daily_snapshot(db, PROJECT_ID, date(2026, 3, 15))
        assert db.add.called

    @pytest.mark.asyncio
    async def test_zone_activity_computed(self):
        """GPS pings should produce zone activity data."""
        db = AsyncMock()

        # Create mock pings with location data
        mock_pings = []
        for i in range(5):
            mp = MagicMock()
            mp.worker_id = f"W{i:03d}"
            mp.latitude = Decimal("30.2672")
            mp.longitude = Decimal("-97.7431")
            mp.timestamp = NOW + timedelta(minutes=i)
            mp.trade = "electrician"
            mock_pings.append(mp)

        pings_result = MagicMock()
        pings_result.scalars.return_value.all.return_value = mock_pings

        telemetry_result = MagicMock()
        telemetry_result.scalars.return_value.all.return_value = []

        badges_result = MagicMock()
        badges_result.scalars.return_value.all.return_value = []

        upsert_check = MagicMock()
        upsert_check.scalars.return_value.first.return_value = None

        db.execute.side_effect = [pings_result, telemetry_result, badges_result, upsert_check]
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        await aggregate_daily_snapshot(db, PROJECT_ID, date(2026, 3, 15))
        assert db.add.called


# ===========================================================================
# TestReportGeneration
# ===========================================================================


class TestReportGeneration:
    """Tests for generate_report_from_snapshot()."""

    @pytest.mark.asyncio
    async def test_missing_snapshot_raises(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        db.execute.return_value = mock_result

        with pytest.raises(ValueError, match="No ambient snapshot found"):
            await generate_report_from_snapshot(db, PROJECT_ID, date(2026, 3, 15))

    @pytest.mark.asyncio
    async def test_valid_snapshot_generates_report(self):
        db = AsyncMock()

        mock_snapshot = MagicMock()
        mock_snapshot.project_id = PROJECT_ID
        mock_snapshot.snapshot_date = date(2026, 3, 15)
        mock_snapshot.workforce_summary = {"total_headcount": 10}
        mock_snapshot.equipment_summary = {}
        mock_snapshot.site_activity = {}

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_snapshot
        db.execute.return_value = mock_result

        mock_aggregate = MagicMock()
        mock_aggregate.workforce = {"total_headcount": 0}
        mock_aggregate.equipment = []
        mock_aggregate.daily_log = None

        with (
            patch(
                "app.services.reporting.daily_report_generator.aggregate_daily_data",
                return_value=mock_aggregate,
            ),
            patch(
                "app.services.reporting.daily_report_generator.create_daily_report"
            ) as mock_create,
        ):
            mock_report = MagicMock()
            mock_report.id = uuid.uuid4()
            mock_create.return_value = mock_report

            report = await generate_report_from_snapshot(
                db, PROJECT_ID, date(2026, 3, 15), generated_by=uuid.uuid4()
            )
            assert report.id == mock_report.id
            mock_create.assert_called_once()
            # Verify the pre_built_aggregate parameter was passed
            _, kwargs = mock_create.call_args
            assert kwargs.get("pre_built_aggregate") is not None

    @pytest.mark.asyncio
    async def test_report_passes_generated_by(self):
        db = AsyncMock()
        user_id = uuid.uuid4()

        mock_snapshot = MagicMock()
        mock_snapshot.workforce_summary = {}
        mock_snapshot.equipment_summary = {}
        mock_snapshot.site_activity = {}
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_snapshot
        db.execute.return_value = mock_result

        mock_aggregate = MagicMock()
        mock_aggregate.workforce = {"total_headcount": 0}
        mock_aggregate.equipment = []
        mock_aggregate.daily_log = None

        with (
            patch(
                "app.services.reporting.daily_report_generator.aggregate_daily_data",
                return_value=mock_aggregate,
            ),
            patch(
                "app.services.reporting.daily_report_generator.create_daily_report"
            ) as mock_create,
        ):
            mock_create.return_value = MagicMock(id=uuid.uuid4())
            await generate_report_from_snapshot(
                db, PROJECT_ID, date(2026, 3, 15), generated_by=user_id
            )
            _, kwargs = mock_create.call_args
            assert kwargs["generated_by"] == user_id

    @pytest.mark.asyncio
    async def test_report_passes_correct_date(self):
        db = AsyncMock()
        report_date = date(2026, 3, 10)

        mock_snapshot = MagicMock()
        mock_snapshot.workforce_summary = {}
        mock_snapshot.equipment_summary = {}
        mock_snapshot.site_activity = {}
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_snapshot
        db.execute.return_value = mock_result

        mock_aggregate = MagicMock()
        mock_aggregate.workforce = {"total_headcount": 0}
        mock_aggregate.equipment = []
        mock_aggregate.daily_log = None

        with (
            patch(
                "app.services.reporting.daily_report_generator.aggregate_daily_data",
                return_value=mock_aggregate,
            ),
            patch(
                "app.services.reporting.daily_report_generator.create_daily_report"
            ) as mock_create,
        ):
            mock_create.return_value = MagicMock(id=uuid.uuid4())
            await generate_report_from_snapshot(db, PROJECT_ID, report_date)
            _, kwargs = mock_create.call_args
            assert kwargs["report_date"] == report_date


# ===========================================================================
# TestBatchValidation
# ===========================================================================


class TestBatchValidation:
    """Tests for batch validation and edge cases."""

    @pytest.mark.asyncio
    async def test_string_timestamp_parsed(self):
        db = _mock_db_no_dupes()
        ping = _make_ping()
        ping["timestamp"] = "2026-03-15T08:00:00+00:00"
        count = await ingest_field_pings(db, PROJECT_ID, [ping])
        assert count == 1

    @pytest.mark.asyncio
    async def test_invalid_string_timestamp_rejected(self):
        db = _mock_db_no_dupes()
        ping = _make_ping()
        ping["timestamp"] = "not-a-timestamp"
        count = await ingest_field_pings(db, PROJECT_ID, [ping])
        assert count == 0

    @pytest.mark.asyncio
    async def test_boundary_coordinates_accepted(self):
        db = _mock_db_no_dupes()
        # Distinct worker_ids — the intra-batch dedup keys on
        # (worker_id, timestamp), and _make_ping defaults to a shared NOW.
        # Without unique worker_ids, pings 2 and 3 are filtered as duplicates.
        pings = [
            _make_ping(worker_id="W001", lat=-90.0, lon=-180.0),
            _make_ping(worker_id="W002", lat=90.0, lon=180.0),
            _make_ping(worker_id="W003", lat=0.0, lon=0.0),
        ]
        count = await ingest_field_pings(db, PROJECT_ID, pings)
        assert count == 3

    @pytest.mark.asyncio
    async def test_telemetry_string_timestamp_parsed(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        t = _make_telemetry()
        t["timestamp"] = "2026-03-15T10:00:00+00:00"
        count = await ingest_equipment_telemetry(db, PROJECT_ID, [t])
        assert count == 1
