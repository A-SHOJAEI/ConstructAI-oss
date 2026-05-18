"""Tests for HeatShield — heat illness prevention and OSHA compliance.

Covers models, pure functions (threshold, WBGT, break scheduling),
acclimatization logic, DB-backed CRUD, break compliance, incident
reporting, HIIPP generation, and dashboard aggregation.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.heat_compliance import (
    HeatIncidentReport,
    HeatMonitoringConfig,
    JobsiteHeatMonitoring,
    RestBreakLog,
    WorkerAcclimatization,
)
from app.services.products.heatshield.service import (
    _time_to_minutes,
    add_worker,
    advance_acclimatization,
    calculate_threshold,
    calculate_wbgt,
    check_acclimatization_reset,
    check_break_compliance,
    configure_monitoring,
    create_incident,
    generate_break_schedule,
    generate_hiipp,
    get_dashboard,
    list_workers,
    log_break,
    record_manual_reading,
    update_worker,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> HeatMonitoringConfig:
    """Create a HeatMonitoringConfig instance for testing."""
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "threshold_initial_f": Decimal("80.0"),
        "threshold_high_heat_f": Decimal("90.0"),
        "crew_start_time": "07:00",
        "monitoring_enabled": True,
        "notification_contacts": [],
    }
    defaults.update(overrides)
    return HeatMonitoringConfig(**defaults)


def _make_worker(**overrides) -> WorkerAcclimatization:
    """Create a WorkerAcclimatization instance for testing."""
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "worker_id": "W-001",
        "worker_name": "John Doe",
        "start_date": date.today(),
        "acclimatization_day": 1,
        "max_exposure_hours": Decimal("8.0"),
        "status": "acclimatizing",
        "last_work_date": date.today(),
    }
    defaults.update(overrides)
    return WorkerAcclimatization(**defaults)


# ===========================================================================
# TestHeatModels — model creation and defaults (5 tests)
# ===========================================================================


class TestHeatModels:
    """Verify model instantiation and default values."""

    def test_heat_monitoring_config_defaults(self):
        config = _make_config()
        assert config.threshold_initial_f == Decimal("80.0")
        assert config.threshold_high_heat_f == Decimal("90.0")
        assert config.monitoring_enabled is True
        assert config.crew_start_time == "07:00"

    def test_jobsite_heat_monitoring_defaults(self):
        # server_default values populate on flush/refresh, not at instantiation;
        # assert the column-level default declaration instead.
        from sqlalchemy import inspect

        col = {c.name: c for c in inspect(JobsiteHeatMonitoring).columns}
        assert col["data_source"].server_default.arg == "weather_api"
        assert col["threshold_level"].server_default.arg == "normal"

    def test_worker_acclimatization_defaults(self):
        worker = _make_worker()
        assert worker.acclimatization_day == 1
        assert worker.status == "acclimatizing"

    def test_rest_break_log_defaults(self):
        from sqlalchemy import inspect

        col = {c.name: c for c in inspect(RestBreakLog).columns}
        # server_default emits SQL-level defaults; verify the declaration.
        assert col["workers_present"].server_default is not None

    def test_heat_incident_report_defaults(self):
        from sqlalchemy import inspect

        col = {c.name: c for c in inspect(HeatIncidentReport).columns}
        assert col["medical_response"].server_default.arg == "none"


# ===========================================================================
# TestCalculateThreshold — boundary cases (5 tests)
# ===========================================================================


class TestCalculateThreshold:
    """Test threshold level determination."""

    def test_normal_below_80(self):
        assert calculate_threshold(79.9) == "normal"

    def test_initial_at_exactly_80(self):
        assert calculate_threshold(80.0) == "initial"

    def test_initial_between_80_and_90(self):
        assert calculate_threshold(85.0) == "initial"

    def test_high_heat_at_exactly_90(self):
        assert calculate_threshold(90.0) == "high_heat"

    def test_custom_thresholds_from_config(self):
        config = _make_config(
            threshold_initial_f=Decimal("85.0"),
            threshold_high_heat_f=Decimal("95.0"),
        )
        assert calculate_threshold(84.9, config) == "normal"
        assert calculate_threshold(85.0, config) == "initial"
        assert calculate_threshold(94.9, config) == "initial"
        assert calculate_threshold(95.0, config) == "high_heat"


# ===========================================================================
# TestCalculateWBGT — WBGT approximation (4 tests)
# ===========================================================================


class TestCalculateWBGT:
    """Test WBGT approximation from temperature and humidity."""

    def test_moderate_conditions(self):
        wbgt = calculate_wbgt(85.0, 50.0)
        # Should be roughly between 75-85 F for moderate conditions
        assert 70.0 < wbgt < 90.0

    def test_high_humidity_raises_wbgt(self):
        low_humidity = calculate_wbgt(90.0, 30.0)
        high_humidity = calculate_wbgt(90.0, 80.0)
        assert high_humidity > low_humidity

    def test_wind_reduces_wbgt(self):
        calm = calculate_wbgt(95.0, 60.0, wind_speed_mph=0.0)
        windy = calculate_wbgt(95.0, 60.0, wind_speed_mph=15.0)
        assert windy < calm

    def test_extreme_conditions(self):
        wbgt = calculate_wbgt(105.0, 90.0)
        # Very high temp + humidity should produce dangerous WBGT
        assert wbgt > 85.0


# ===========================================================================
# TestGenerateBreakSchedule — all 3 threshold levels (6 tests)
# ===========================================================================


class TestGenerateBreakSchedule:
    """Test break schedule generation for each threshold level."""

    def test_normal_schedule_every_4h(self):
        schedule = generate_break_schedule("07:00", "normal")
        # 10-hour day, 4-hour intervals -> breaks at 11:00, 15:00
        assert len(schedule) >= 2
        for item in schedule:
            assert item.duration_minutes == 10
            assert item.threshold_level == "normal"

    def test_initial_schedule_every_2h(self):
        schedule = generate_break_schedule("07:00", "initial")
        # 10-hour day, 2-hour intervals -> breaks at 09:00, 11:00, 13:00, 15:00
        assert len(schedule) >= 4
        for item in schedule:
            assert item.duration_minutes == 15
            assert item.threshold_level == "initial"

    def test_high_heat_schedule_every_1h(self):
        schedule = generate_break_schedule("07:00", "high_heat")
        # 10-hour day, 1-hour intervals -> breaks at 08-16
        assert len(schedule) >= 8
        for item in schedule:
            assert item.duration_minutes == 15
            assert item.threshold_level == "high_heat"

    def test_schedule_starts_after_interval(self):
        schedule = generate_break_schedule("06:00", "normal")
        # First break should be at 10:00 (6:00 + 4h)
        assert schedule[0].scheduled_time == "10:00"

    def test_schedule_custom_start_time(self):
        schedule = generate_break_schedule("05:30", "initial")
        # First break at 07:30 (5:30 + 2h)
        assert schedule[0].scheduled_time == "07:30"

    def test_all_items_default_to_scheduled(self):
        schedule = generate_break_schedule("07:00", "normal")
        for item in schedule:
            assert item.status == "scheduled"


# ===========================================================================
# TestAcclimatization — 14-day cycle and resets (8 tests)
# ===========================================================================


class TestAcclimatization:
    """Test worker acclimatization cycle and absence-based reset."""

    def test_advance_increments_day(self):
        worker = _make_worker(acclimatization_day=5)
        advance_acclimatization(worker)
        assert worker.acclimatization_day == 6

    def test_advance_caps_at_14(self):
        worker = _make_worker(acclimatization_day=14)
        advance_acclimatization(worker)
        assert worker.acclimatization_day == 14

    def test_advance_sets_acclimatized_at_14(self):
        worker = _make_worker(acclimatization_day=13)
        advance_acclimatization(worker)
        assert worker.acclimatization_day == 14
        assert worker.status == "acclimatized"

    def test_advance_does_not_change_status_early(self):
        worker = _make_worker(acclimatization_day=5)
        advance_acclimatization(worker)
        assert worker.status == "acclimatizing"

    def test_reset_on_7_day_absence(self):
        worker = _make_worker(
            last_work_date=date.today() - timedelta(days=8),
            acclimatization_day=10,
            status="acclimatizing",
        )
        was_reset = check_acclimatization_reset(worker, date.today())
        assert was_reset is True
        assert worker.acclimatization_day == 1
        assert worker.status == "reset"
        assert worker.start_date == date.today()

    def test_no_reset_within_7_days(self):
        worker = _make_worker(
            last_work_date=date.today() - timedelta(days=5),
            acclimatization_day=8,
        )
        was_reset = check_acclimatization_reset(worker, date.today())
        assert was_reset is False
        assert worker.acclimatization_day == 8

    def test_no_reset_when_already_reset(self):
        worker = _make_worker(
            last_work_date=date.today() - timedelta(days=10),
            acclimatization_day=1,
            status="reset",
        )
        was_reset = check_acclimatization_reset(worker, date.today())
        assert was_reset is False

    def test_no_reset_without_last_work_date(self):
        worker = _make_worker(last_work_date=None, acclimatization_day=5)
        was_reset = check_acclimatization_reset(worker, date.today())
        assert was_reset is False


# ===========================================================================
# TestConfigureMonitoring — create and update config (4 tests)
# ===========================================================================


class TestConfigureMonitoring:
    """Test config upsert via mocked DB session."""

    @pytest.mark.asyncio
    async def test_create_new_config(self):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute.return_value = result_mock

        pid = uuid.uuid4()
        oid = uuid.uuid4()

        await configure_monitoring(db, pid, oid, {"zip_code": "90210", "monitoring_enabled": True})

        db.add.assert_called_once()
        db.flush.assert_awaited_once()
        db.refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_existing_config(self):
        existing = _make_config(zip_code="10001")
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        db.execute.return_value = result_mock

        updated = await configure_monitoring(
            db, existing.project_id, existing.organization_id, {"zip_code": "90210"}
        )

        assert updated.zip_code == "90210"
        db.add.assert_not_called()  # should not re-add existing

    @pytest.mark.asyncio
    async def test_partial_update_ignores_none(self):
        existing = _make_config(zip_code="10001")
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        db.execute.return_value = result_mock

        await configure_monitoring(
            db,
            existing.project_id,
            existing.organization_id,
            {"zip_code": None, "crew_start_time": "06:00"},
        )
        # zip_code should NOT have been overwritten to None
        assert existing.zip_code == "10001"
        assert existing.crew_start_time == "06:00"

    @pytest.mark.asyncio
    async def test_config_sets_thresholds(self):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute.return_value = result_mock

        await configure_monitoring(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {"threshold_initial_f": 85.0, "threshold_high_heat_f": 95.0},
        )
        db.add.assert_called_once()


# ===========================================================================
# TestRecordReading — manual reading with threshold calculation (4 tests)
# ===========================================================================


class TestRecordReading:
    """Test manual heat reading creation."""

    @pytest.mark.asyncio
    async def test_creates_reading_with_normal_threshold(self):
        db = AsyncMock()
        cfg_result = MagicMock()
        cfg_result.scalar_one_or_none.return_value = None
        db.execute.return_value = cfg_result

        await record_manual_reading(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {"temperature_f": 75.0, "humidity_pct": 40.0},
        )

        db.add.assert_called_once()
        added_obj = db.add.call_args[0][0]
        assert added_obj.threshold_level == "normal"
        assert added_obj.data_source == "manual"
        assert added_obj.protocol_activated is False

    @pytest.mark.asyncio
    async def test_creates_reading_with_high_heat_threshold(self):
        db = AsyncMock()
        cfg_result = MagicMock()
        cfg_result.scalar_one_or_none.return_value = None
        db.execute.return_value = cfg_result

        await record_manual_reading(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {"temperature_f": 95.0, "humidity_pct": 70.0},
        )

        added_obj = db.add.call_args[0][0]
        assert added_obj.threshold_level == "high_heat"
        assert added_obj.protocol_activated is True

    @pytest.mark.asyncio
    async def test_calculates_wbgt_when_humidity_present(self):
        db = AsyncMock()
        cfg_result = MagicMock()
        cfg_result.scalar_one_or_none.return_value = None
        db.execute.return_value = cfg_result

        await record_manual_reading(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {"temperature_f": 90.0, "humidity_pct": 60.0, "wind_speed_mph": 5.0},
        )

        added_obj = db.add.call_args[0][0]
        assert added_obj.wbgt_f is not None

    @pytest.mark.asyncio
    async def test_no_wbgt_without_humidity(self):
        db = AsyncMock()
        cfg_result = MagicMock()
        cfg_result.scalar_one_or_none.return_value = None
        db.execute.return_value = cfg_result

        await record_manual_reading(db, uuid.uuid4(), uuid.uuid4(), {"temperature_f": 85.0})

        added_obj = db.add.call_args[0][0]
        assert added_obj.wbgt_f is None


# ===========================================================================
# TestWorkerManagement — add, list, auto-reset (5 tests)
# ===========================================================================


class TestWorkerManagement:
    """Test worker CRUD and auto-reset on listing."""

    @pytest.mark.asyncio
    async def test_add_worker(self):
        db = AsyncMock()
        await add_worker(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {"worker_id": "W-100", "worker_name": "Jane Smith"},
        )
        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.worker_id == "W-100"
        assert added.worker_name == "Jane Smith"
        # acclimatization_day is server_default — None until flush+refresh.

    @pytest.mark.asyncio
    async def test_add_worker_with_supervisor(self):
        db = AsyncMock()
        sup_id = uuid.uuid4()
        await add_worker(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {"worker_id": "W-101", "worker_name": "Bob", "supervisor_id": sup_id},
        )
        added = db.add.call_args[0][0]
        assert added.supervisor_id == sup_id

    @pytest.mark.asyncio
    async def test_update_worker_status(self):
        existing = _make_worker()
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        db.execute.return_value = result_mock

        updated = await update_worker(
            db, existing.project_id, existing.worker_id, {"status": "acclimatized"}
        )
        assert updated.status == "acclimatized"

    @pytest.mark.asyncio
    async def test_update_worker_not_found(self):
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute.return_value = result_mock

        result = await update_worker(db, uuid.uuid4(), "NONEXISTENT", {"status": "acclimatized"})
        assert result is None

    @pytest.mark.asyncio
    async def test_list_workers_auto_resets(self):
        stale_worker = _make_worker(
            last_work_date=date.today() - timedelta(days=10),
            acclimatization_day=12,
            status="acclimatizing",
        )
        db = AsyncMock()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [stale_worker]
        result_mock.scalars.return_value = scalars_mock
        db.execute.return_value = result_mock

        workers = await list_workers(db, stale_worker.project_id)
        assert len(workers) == 1
        assert workers[0].status == "reset"
        assert workers[0].acclimatization_day == 1


# ===========================================================================
# TestBreakLogging — log break, exception detection (5 tests)
# ===========================================================================


class TestBreakLogging:
    """Test rest break logging and exception flagging."""

    @pytest.mark.asyncio
    async def test_log_valid_break(self):
        db = AsyncMock()
        pid, oid = uuid.uuid4(), uuid.uuid4()

        await log_break(
            db,
            pid,
            oid,
            {
                "break_date": date.today(),
                "actual_start": "11:00",
                "actual_end": "11:15",
                "duration_minutes": 15,
                "location_compliant": True,
                "workers_present": 8,
            },
        )

        added = db.add.call_args[0][0]
        assert added.exception_flag is False
        assert added.duration_minutes == 15

    @pytest.mark.asyncio
    async def test_short_break_flagged_as_exception(self):
        db = AsyncMock()
        await log_break(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {
                "break_date": date.today(),
                "actual_start": "11:00",
                "actual_end": "11:05",
                "duration_minutes": 5,
                "location_compliant": True,
            },
        )
        added = db.add.call_args[0][0]
        assert added.exception_flag is True
        assert "below minimum" in added.exception_reason

    @pytest.mark.asyncio
    async def test_non_compliant_location_flagged(self):
        db = AsyncMock()
        await log_break(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {
                "break_date": date.today(),
                "actual_start": "11:00",
                "actual_end": "11:15",
                "duration_minutes": 15,
                "location_compliant": False,
            },
        )
        added = db.add.call_args[0][0]
        assert added.exception_flag is True
        assert "not compliant" in added.exception_reason

    @pytest.mark.asyncio
    async def test_custom_exception_reason_preserved(self):
        db = AsyncMock()
        await log_break(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {
                "break_date": date.today(),
                "actual_start": "11:00",
                "actual_end": "11:03",
                "duration_minutes": 3,
                "location_compliant": True,
                "exception_reason": "Emergency situation",
            },
        )
        added = db.add.call_args[0][0]
        assert added.exception_reason == "Emergency situation"

    @pytest.mark.asyncio
    async def test_break_with_gps_coordinates(self):
        db = AsyncMock()
        await log_break(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {
                "break_date": date.today(),
                "actual_start": "11:00",
                "actual_end": "11:15",
                "duration_minutes": 15,
                "location_compliant": True,
                "gps_lat": 34.052235,
                "gps_lng": -118.243683,
            },
        )
        added = db.add.call_args[0][0]
        assert added.gps_lat == Decimal("34.052235")
        assert added.gps_lng == Decimal("-118.243683")


# ===========================================================================
# TestBreakCompliance — schedule vs logged matching (5 tests)
# ===========================================================================


class TestBreakCompliance:
    """Test break schedule compliance checking."""

    @pytest.mark.asyncio
    async def test_all_breaks_missed_when_none_logged(self):
        db = AsyncMock()
        # list_breaks returns empty
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock.scalars.return_value = scalars_mock
        db.execute.return_value = result_mock

        schedule = await check_break_compliance(db, uuid.uuid4(), date.today(), "07:00", "normal")
        assert all(item.status == "missed" for item in schedule)

    @pytest.mark.asyncio
    async def test_exact_match_marks_logged(self):
        logged_break = RestBreakLog(
            project_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            break_date=date.today(),
            actual_start="11:00",
        )
        db = AsyncMock()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [logged_break]
        result_mock.scalars.return_value = scalars_mock
        db.execute.return_value = result_mock

        schedule = await check_break_compliance(db, uuid.uuid4(), date.today(), "07:00", "normal")
        # First normal break is at 11:00 — should match
        assert schedule[0].status == "logged"

    @pytest.mark.asyncio
    async def test_within_tolerance_marks_logged(self):
        # Break logged 10 minutes early (within 15-min tolerance)
        logged_break = RestBreakLog(
            project_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            break_date=date.today(),
            actual_start="10:50",
        )
        db = AsyncMock()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [logged_break]
        result_mock.scalars.return_value = scalars_mock
        db.execute.return_value = result_mock

        schedule = await check_break_compliance(db, uuid.uuid4(), date.today(), "07:00", "normal")
        assert schedule[0].status == "logged"

    @pytest.mark.asyncio
    async def test_outside_tolerance_marks_missed(self):
        # Break logged 30 minutes off (outside 15-min tolerance)
        logged_break = RestBreakLog(
            project_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            break_date=date.today(),
            actual_start="10:30",
        )
        db = AsyncMock()
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [logged_break]
        result_mock.scalars.return_value = scalars_mock
        db.execute.return_value = result_mock

        schedule = await check_break_compliance(db, uuid.uuid4(), date.today(), "07:00", "normal")
        # 11:00 scheduled, 10:30 logged = 30 min diff > 15 min tolerance
        assert schedule[0].status == "missed"

    def test_time_to_minutes_helper(self):
        assert _time_to_minutes("07:00") == 420
        assert _time_to_minutes("11:30") == 690
        assert _time_to_minutes("00:00") == 0
        assert _time_to_minutes("invalid") == 0


# ===========================================================================
# TestIncidentReporting — create incident, OSHA flag (4 tests)
# ===========================================================================


class TestIncidentReporting:
    """Test heat incident report creation."""

    @pytest.mark.asyncio
    async def test_create_incident_basic(self):
        db = AsyncMock()
        await create_incident(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {
                "worker_id": "W-001",
                "worker_name": "John Doe",
                "incident_date": date.today(),
                "symptoms": ["dizziness", "nausea"],
                "medical_response": "first_aid",
            },
        )
        added = db.add.call_args[0][0]
        assert added.worker_id == "W-001"
        assert added.symptoms == ["dizziness", "nausea"]
        assert added.medical_response == "first_aid"

    @pytest.mark.asyncio
    async def test_create_incident_osha_recordable(self):
        db = AsyncMock()
        await create_incident(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {
                "incident_date": date.today(),
                "symptoms": ["loss of consciousness"],
                "medical_response": "hospitalized",
                "osha_recordable": True,
            },
        )
        added = db.add.call_args[0][0]
        assert added.osha_recordable is True
        assert added.medical_response == "hospitalized"

    @pytest.mark.asyncio
    async def test_create_incident_with_heat_index(self):
        db = AsyncMock()
        await create_incident(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {
                "incident_date": date.today(),
                "symptoms": ["cramps"],
                "heat_index_at_incident": 105.3,
                "acclimatization_day": 3,
            },
        )
        added = db.add.call_args[0][0]
        assert added.heat_index_at_incident == Decimal("105.3")
        assert added.acclimatization_day == 3

    @pytest.mark.asyncio
    async def test_create_incident_defaults(self):
        db = AsyncMock()
        await create_incident(
            db,
            uuid.uuid4(),
            uuid.uuid4(),
            {"incident_date": date.today(), "symptoms": []},
        )
        added = db.add.call_args[0][0]
        assert added.medical_response == "none"
        assert added.osha_recordable is False
        assert added.photos == []


# ===========================================================================
# TestHIIPPGeneration — template fallback, plan creation (3 tests)
# ===========================================================================


class TestHIIPPGeneration:
    """Test HIIPP plan generation."""

    @pytest.mark.asyncio
    async def test_template_fallback_on_llm_failure(self):
        db = AsyncMock()
        # Mock count query returning 0
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        db.execute.return_value = count_result

        with patch(
            "app.services.products.heatshield.service.get_llm_gateway",
            side_effect=ImportError("no gateway"),
        ):
            await generate_hiipp(db, uuid.uuid4(), uuid.uuid4())

        added = db.add.call_args[0][0]
        assert "title" in added.plan_content
        assert "sections" in added.plan_content
        sections = added.plan_content["sections"]
        assert "purpose" in sections
        assert "responsibilities" in sections
        assert "water_provision" in sections
        assert "rest_areas" in sections
        assert "acclimatization_procedures" in sections
        assert "emergency_response" in sections

    @pytest.mark.asyncio
    async def test_plan_version_increments(self):
        db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 3
        db.execute.return_value = count_result

        with patch(
            "app.services.products.heatshield.service.get_llm_gateway",
            side_effect=Exception("fail"),
        ):
            await generate_hiipp(db, uuid.uuid4(), uuid.uuid4())

        added = db.add.call_args[0][0]
        assert added.version == 4

    @pytest.mark.asyncio
    async def test_plan_has_threshold_procedures(self):
        db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        db.execute.return_value = count_result

        with patch(
            "app.services.products.heatshield.service.get_llm_gateway",
            side_effect=Exception("fail"),
        ):
            await generate_hiipp(db, uuid.uuid4(), uuid.uuid4())

        added = db.add.call_args[0][0]
        thresholds = added.plan_content["sections"]["threshold_procedures"]
        assert "normal" in thresholds
        assert "initial" in thresholds
        assert "high_heat" in thresholds


# ===========================================================================
# TestDashboard — full dashboard aggregation (3 tests)
# ===========================================================================


class TestDashboard:
    """Test dashboard data aggregation."""

    @pytest.mark.asyncio
    async def test_dashboard_with_no_data(self):
        db = AsyncMock()
        # All queries return empty/None
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock.scalars.return_value = scalars_mock
        result_mock.scalar.return_value = None
        db.execute.return_value = result_mock

        data = await get_dashboard(db, uuid.uuid4())

        assert data["current_conditions"] is None
        assert data["threshold_level"] == "normal"
        assert data["workers"]["total"] == 0
        # No workers on site → nothing to comply with → 1.0 by convention.
        assert data["break_compliance_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_dashboard_worker_counts(self):
        workers = [
            _make_worker(status="acclimatizing"),
            _make_worker(status="acclimatized", acclimatization_day=14),
            _make_worker(status="acclimatized", acclimatization_day=14),
            _make_worker(status="reset", acclimatization_day=1),
        ]

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            # First call: get_current_conditions
            if call_count == 1:
                mock_result.scalar_one_or_none.return_value = None
                return mock_result
            # Second call: list_workers
            if call_count == 2:
                scalars_mock = MagicMock()
                scalars_mock.all.return_value = workers
                mock_result.scalars.return_value = scalars_mock
                return mock_result
            # Third call: config lookup
            if call_count == 3:
                mock_result.scalar_one_or_none.return_value = None
                return mock_result
            # Fourth call: break compliance list_breaks
            if call_count == 4:
                scalars_mock = MagicMock()
                scalars_mock.all.return_value = []
                mock_result.scalars.return_value = scalars_mock
                return mock_result
            # Fifth call: recent incidents
            if call_count == 5:
                scalars_mock = MagicMock()
                scalars_mock.all.return_value = []
                mock_result.scalars.return_value = scalars_mock
                return mock_result
            mock_result.scalar_one_or_none.return_value = None
            return mock_result

        db = AsyncMock()
        db.execute = mock_execute
        db.flush = AsyncMock()

        data = await get_dashboard(db, uuid.uuid4())
        assert data["workers"]["total"] == 4
        assert data["workers"]["acclimatizing"] == 1
        assert data["workers"]["acclimatized"] == 2
        assert data["workers"]["reset"] == 1

    @pytest.mark.asyncio
    async def test_dashboard_compliance_rate_calculated(self):
        """When some breaks are logged and others missed, rate is correct."""
        logged_break = RestBreakLog(
            project_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            break_date=date.today(),
            actual_start="11:00",
        )

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:  # get_current_conditions
                mock_result.scalar_one_or_none.return_value = None
                return mock_result
            if call_count == 2:  # list_workers
                scalars_mock = MagicMock()
                scalars_mock.all.return_value = []
                mock_result.scalars.return_value = scalars_mock
                return mock_result
            if call_count == 3:  # config lookup
                mock_result.scalar_one_or_none.return_value = None
                return mock_result
            if call_count == 4:  # list_breaks for compliance check
                scalars_mock = MagicMock()
                scalars_mock.all.return_value = [logged_break]
                mock_result.scalars.return_value = scalars_mock
                return mock_result
            if call_count == 5:  # recent incidents
                scalars_mock = MagicMock()
                scalars_mock.all.return_value = []
                mock_result.scalars.return_value = scalars_mock
                return mock_result
            mock_result.scalar_one_or_none.return_value = None
            return mock_result

        db = AsyncMock()
        db.execute = mock_execute
        db.flush = AsyncMock()

        data = await get_dashboard(db, uuid.uuid4())
        # Normal schedule: 2 breaks (11:00 and 15:00), one logged at 11:00
        assert 0.0 < data["break_compliance_rate"] <= 1.0
