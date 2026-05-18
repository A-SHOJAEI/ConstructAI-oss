"""Tests for daily log field-data-capture workflow.

Covers:
- Status transitions (draft → submitted → approved)
- Weather auto-populate integration
- Copy-previous-day template
- Weekly summary aggregation
- CSV export
- Create / update validation
- API endpoints (mocked service/DB)
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_daily_log(**overrides):
    """Build a mock DailyLog model object with sensible defaults."""
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "log_date": date.today(),
        "status": "draft",
        "weather": {"conditions": "clear", "temperature_high": 75},
        "crew_count": 15,
        "work_hours": Decimal("120.00"),
        "work_narrative": "Poured foundation on grid A-C.",
        "manpower_by_trade": [
            {"trade": "concrete", "headcount": 8, "hours": 64},
            {"trade": "electrical", "headcount": 4, "hours": 32},
        ],
        "equipment_entries": [
            {"equipment_type": "crane", "hours_used": 6},
        ],
        "deliveries": [
            {"description": "Rebar delivery", "supplier": "Steel Co"},
        ],
        "visitors": [
            {"name": "John Inspector", "company": "City", "purpose": "inspection"},
        ],
        "photos": [],
        "activities_completed": [{"description": "Foundation pour"}],
        "delays": [],
        "notes": None,
        "location_lat": Decimal("40.712776"),
        "location_lon": Decimal("-74.005974"),
        "safety_incidents": None,
        "safety_topic_discussed": None,
        "weather_delay_hours": None,
        "approved_by": None,
        "approved_at": None,
        "submitted_at": None,
        "data_source": "manual",
        "procore_id": None,
        "created_by": uuid.uuid4(),
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# 1. Status Transitions
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    def test_valid_draft_to_submitted(self):
        from app.services.productivity.daily_log_service import _validate_transition

        _validate_transition("draft", "submitted")  # should not raise

    def test_valid_submitted_to_approved(self):
        from app.services.productivity.daily_log_service import _validate_transition

        _validate_transition("submitted", "approved")

    def test_valid_submitted_to_draft(self):
        from app.services.productivity.daily_log_service import _validate_transition

        _validate_transition("submitted", "draft")  # rejection

    def test_invalid_draft_to_approved(self):
        from app.services.productivity.daily_log_service import _validate_transition

        with pytest.raises(ValueError, match="Cannot transition"):
            _validate_transition("draft", "approved")

    def test_invalid_approved_to_anything(self):
        from app.services.productivity.daily_log_service import _validate_transition

        with pytest.raises(ValueError, match="Cannot transition"):
            _validate_transition("approved", "draft")

    def test_invalid_approved_to_submitted(self):
        from app.services.productivity.daily_log_service import _validate_transition

        with pytest.raises(ValueError, match="Cannot transition"):
            _validate_transition("approved", "submitted")


# ---------------------------------------------------------------------------
# 2. Create / Update
# ---------------------------------------------------------------------------


class TestDailyLogCreation:
    @pytest.mark.asyncio
    async def test_create_sets_draft_status(self):
        from app.services.productivity.daily_log_service import create_daily_log

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        project_id = uuid.uuid4()
        data = {"log_date": date.today(), "crew_count": 10}

        await create_daily_log(mock_db, project_id, data)
        # The DailyLog constructor is called with status="draft"
        mock_db.add.assert_called_once()
        added_obj = mock_db.add.call_args[0][0]
        assert added_obj.status == "draft"
        assert added_obj.crew_count == 10

    @pytest.mark.asyncio
    async def test_create_with_manpower(self):
        from app.services.productivity.daily_log_service import create_daily_log

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        data = {
            "log_date": date.today(),
            "manpower_by_trade": [
                {"trade": "concrete", "headcount": 8, "hours": 64},
            ],
        }
        await create_daily_log(mock_db, uuid.uuid4(), data)
        added_obj = mock_db.add.call_args[0][0]
        assert len(added_obj.manpower_by_trade) == 1
        assert added_obj.manpower_by_trade[0]["trade"] == "concrete"

    @pytest.mark.asyncio
    async def test_update_rejects_non_draft(self):
        from app.services.productivity.daily_log_service import update_daily_log

        mock_db = AsyncMock()
        log = _make_daily_log(status="submitted")

        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = log
        mock_db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(ValueError, match="Only draft logs"):
            await update_daily_log(mock_db, log.id, log.project_id, {"crew_count": 20})

    @pytest.mark.asyncio
    async def test_update_draft_succeeds(self):
        from app.services.productivity.daily_log_service import update_daily_log

        mock_db = AsyncMock()
        log = _make_daily_log(status="draft")

        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = log
        mock_db.execute = AsyncMock(return_value=result_mock)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        updated = await update_daily_log(mock_db, log.id, log.project_id, {"crew_count": 25})
        assert updated.crew_count == 25


# ---------------------------------------------------------------------------
# 3. Submit / Approve / Reject
# ---------------------------------------------------------------------------


class TestSubmitApprove:
    @pytest.mark.asyncio
    async def test_submit_sets_status_and_timestamp(self):
        from app.services.productivity.daily_log_service import submit_daily_log

        mock_db = AsyncMock()
        log = _make_daily_log(status="draft")
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = log
        mock_db.execute = AsyncMock(return_value=result_mock)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await submit_daily_log(mock_db, log.id, log.project_id, uuid.uuid4())
        assert result.status == "submitted"
        assert result.submitted_at is not None

    @pytest.mark.asyncio
    async def test_approve_sets_status_and_approver(self):
        from app.services.productivity.daily_log_service import approve_daily_log

        mock_db = AsyncMock()
        approver_id = uuid.uuid4()
        log = _make_daily_log(status="submitted")
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = log
        mock_db.execute = AsyncMock(return_value=result_mock)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await approve_daily_log(mock_db, log.id, log.project_id, approver_id)
        assert result.status == "approved"
        assert result.approved_by == approver_id
        assert result.approved_at is not None

    @pytest.mark.asyncio
    async def test_reject_returns_to_draft(self):
        from app.services.productivity.daily_log_service import reject_to_draft

        mock_db = AsyncMock()
        log = _make_daily_log(status="submitted", submitted_at=datetime.now(UTC))
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = log
        mock_db.execute = AsyncMock(return_value=result_mock)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await reject_to_draft(mock_db, log.id, log.project_id)
        assert result.status == "draft"
        assert result.submitted_at is None


# ---------------------------------------------------------------------------
# 4. Weather Auto-populate
# ---------------------------------------------------------------------------


class TestWeatherAutoPopulate:
    @pytest.mark.asyncio
    async def test_returns_weather_dict_on_success(self):
        from app.services.productivity.daily_log_service import auto_populate_weather

        mock_forecast = [
            {
                "temperature_max": 82,
                "temperature_min": 65,
                "precipitation_mm": 0,
                "wind_speed_max": 12,
                "humidity": 55,
                "weather_code": "clear",
            }
        ]

        with patch(
            "app.services.scheduling.weather_service.get_weather_forecast",
            new_callable=AsyncMock,
            return_value=mock_forecast,
        ):
            result = await auto_populate_weather(40.71, -74.00, date.today())
            assert result["temperature_high"] == 82
            assert result["temperature_low"] == 65
            assert result["source"] == "auto"

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        from app.services.productivity.daily_log_service import auto_populate_weather

        with patch(
            "app.services.scheduling.weather_service.get_weather_forecast",
            new_callable=AsyncMock,
            side_effect=Exception("provider down"),
        ):
            result = await auto_populate_weather(40.71, -74.00, date.today())
            assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_data(self):
        from app.services.productivity.daily_log_service import auto_populate_weather

        with patch(
            "app.services.scheduling.weather_service.get_weather_forecast",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await auto_populate_weather(40.71, -74.00, date.today())
            assert result == {}


# ---------------------------------------------------------------------------
# 5. Copy Previous Day
# ---------------------------------------------------------------------------


class TestCopyPreviousDay:
    @pytest.mark.asyncio
    async def test_copies_manpower_and_equipment(self):
        from app.services.productivity.daily_log_service import copy_previous_day

        mock_db = AsyncMock()
        prev_log = _make_daily_log(
            log_date=date.today() - timedelta(days=1),
            crew_count=12,
            manpower_by_trade=[{"trade": "plumbing", "headcount": 4, "hours": 32}],
            equipment_entries=[{"equipment_type": "excavator", "hours_used": 8}],
            location_lat=Decimal("40.71"),
            location_lon=Decimal("-74.00"),
        )
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = prev_log
        mock_db.execute = AsyncMock(return_value=result_mock)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        await copy_previous_day(mock_db, prev_log.project_id, date.today())
        added = mock_db.add.call_args[0][0]
        assert added.log_date == date.today()
        assert added.status == "draft"
        assert added.crew_count == 12
        assert added.manpower_by_trade == prev_log.manpower_by_trade
        assert added.equipment_entries == prev_log.equipment_entries
        assert added.deliveries == []  # day-specific, not copied
        assert added.weather == {}  # should be re-fetched

    @pytest.mark.asyncio
    async def test_raises_when_no_previous_log(self):
        from app.services.productivity.daily_log_service import copy_previous_day

        mock_db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(ValueError, match="No daily log found"):
            await copy_previous_day(mock_db, uuid.uuid4(), date.today())


# ---------------------------------------------------------------------------
# 6. Weekly Summary
# ---------------------------------------------------------------------------


class TestWeeklySummary:
    @pytest.mark.asyncio
    async def test_aggregates_crew_and_hours(self):
        from app.services.productivity.daily_log_service import get_weekly_summary

        mock_db = AsyncMock()
        logs = [
            _make_daily_log(
                log_date=date.today(),
                crew_count=10,
                work_hours=Decimal("80"),
                manpower_by_trade=[{"trade": "concrete", "headcount": 6, "hours": 48}],
                delays=[],
                weather={"conditions": "clear"},
            ),
            _make_daily_log(
                log_date=date.today() + timedelta(days=1),
                crew_count=12,
                work_hours=Decimal("96"),
                manpower_by_trade=[{"trade": "concrete", "headcount": 8, "hours": 64}],
                delays=[{"description": "rain delay", "hours_lost": 2}],
                weather={"conditions": "rain"},
            ),
        ]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = logs
        mock_db.execute = AsyncMock(return_value=result_mock)

        summary = await get_weekly_summary(mock_db, uuid.uuid4(), date.today())
        assert summary["total_logs"] == 2
        assert summary["total_crew_count"] == 22
        assert summary["total_work_hours"] == Decimal("176")
        assert summary["manpower_summary"]["concrete"]["headcount"] == 14
        assert len(summary["delay_summary"]) == 1

    @pytest.mark.asyncio
    async def test_empty_week(self):
        from app.services.productivity.daily_log_service import get_weekly_summary

        mock_db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result_mock)

        summary = await get_weekly_summary(mock_db, uuid.uuid4(), date.today())
        assert summary["total_logs"] == 0
        assert summary["total_work_hours"] == Decimal("0")


# ---------------------------------------------------------------------------
# 7. CSV Export
# ---------------------------------------------------------------------------


class TestCSVExport:
    def test_generates_valid_csv(self):
        from app.services.productivity.daily_log_service import export_daily_logs_csv

        logs = [
            _make_daily_log(
                log_date=date(2025, 6, 1),
                status="approved",
                crew_count=15,
                work_hours=Decimal("120"),
                work_narrative="Foundation pour",
                weather={"conditions": "clear"},
                delays=[{"description": "material delay"}],
                notes="All good",
            ),
        ]
        csv_bytes = export_daily_logs_csv(logs)
        reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
        rows = list(reader)
        assert rows[0][0] == "Date"  # header
        assert rows[1][0] == "2025-06-01"
        assert rows[1][1] == "approved"
        assert "material delay" in rows[1][9]  # Delays column (index 9)

    def test_empty_export(self):
        from app.services.productivity.daily_log_service import export_daily_logs_csv

        csv_bytes = export_daily_logs_csv([])
        reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
        rows = list(reader)
        assert len(rows) == 1  # header only


# ---------------------------------------------------------------------------
# 8. Serialize list helper
# ---------------------------------------------------------------------------


class TestSerializeList:
    def test_dicts_pass_through(self):
        from app.services.productivity.daily_log_service import _serialize_list

        result = _serialize_list([{"trade": "concrete", "headcount": 8}])
        assert result == [{"trade": "concrete", "headcount": 8}]

    def test_pydantic_models_serialized(self):
        from app.schemas.productivity import ManpowerEntry
        from app.services.productivity.daily_log_service import _serialize_list

        entry = ManpowerEntry(trade="electrical", headcount=4, hours=Decimal("32"))
        result = _serialize_list([entry])
        assert result[0]["trade"] == "electrical"
        assert result[0]["headcount"] == 4


# ---------------------------------------------------------------------------
# 9. Schemas
# ---------------------------------------------------------------------------


class TestDailyLogSchemas:
    def test_create_v2_defaults(self):
        from app.schemas.productivity import DailyLogCreateV2

        schema = DailyLogCreateV2(log_date=date.today())
        assert schema.crew_count == 0
        assert schema.manpower_by_trade == []
        assert schema.weather == {}

    def test_update_v2_partial(self):
        from app.schemas.productivity import DailyLogUpdateV2

        schema = DailyLogUpdateV2(crew_count=20)
        dumped = schema.model_dump(exclude_unset=True)
        assert dumped == {"crew_count": 20}

    def test_detail_response_from_attributes(self):
        from app.schemas.productivity import DailyLogDetailResponse

        log = _make_daily_log()
        resp = DailyLogDetailResponse.model_validate(log, from_attributes=True)
        assert resp.status == "draft"

    def test_weekly_summary_fields(self):
        from app.schemas.productivity import DailyLogWeeklySummary

        summary = DailyLogWeeklySummary(
            week_start=date.today(),
            week_end=date.today() + timedelta(days=6),
            total_logs=5,
            total_crew_count=60,
            total_work_hours=Decimal("480"),
            manpower_summary={"concrete": {"headcount": 40, "hours": 320}},
            weather_summary=[],
            delay_summary=[],
        )
        assert summary.total_logs == 5


# ---------------------------------------------------------------------------
# 10. API Endpoints (mocked)
# ---------------------------------------------------------------------------


class TestDailyLogAPIEndpoints:
    @pytest.mark.asyncio
    async def test_create_endpoint(self):
        from app.api.v1.daily_logs import create_log
        from app.schemas.productivity import DailyLogCreateV2

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.daily_logs.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.daily_logs.create_daily_log", new_callable=AsyncMock
            ) as mock_create:
                mock_create.return_value = _make_daily_log()
                request = DailyLogCreateV2(log_date=date.today())
                await create_log(uuid.uuid4(), request, mock_user, mock_db)
                mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_endpoint(self):
        from app.api.v1.daily_logs import submit_log

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.daily_logs.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.daily_logs.submit_daily_log", new_callable=AsyncMock
            ) as mock_submit:
                mock_submit.return_value = _make_daily_log(status="submitted")
                result = await submit_log(uuid.uuid4(), uuid.uuid4(), mock_user, mock_db)
                assert result.status == "submitted"

    @pytest.mark.asyncio
    async def test_approve_endpoint(self):
        from app.api.v1.daily_logs import approve_log

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.daily_logs.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.daily_logs.approve_daily_log", new_callable=AsyncMock
            ) as mock_approve:
                mock_approve.return_value = _make_daily_log(status="approved")
                result = await approve_log(uuid.uuid4(), uuid.uuid4(), mock_user, mock_db)
                assert result.status == "approved"

    @pytest.mark.asyncio
    async def test_reject_endpoint(self):
        from app.api.v1.daily_logs import reject_log

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.daily_logs.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.daily_logs.reject_to_draft", new_callable=AsyncMock
            ) as mock_reject:
                mock_reject.return_value = _make_daily_log(status="draft")
                result = await reject_log(uuid.uuid4(), uuid.uuid4(), mock_user, mock_db)
                assert result.status == "draft"

    @pytest.mark.asyncio
    async def test_weather_endpoint(self):
        from app.api.v1.daily_logs import get_weather

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.daily_logs.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.daily_logs.auto_populate_weather", new_callable=AsyncMock
            ) as mock_wx:
                mock_wx.return_value = {"temperature_high": 80, "source": "auto"}
                result = await get_weather(
                    uuid.uuid4(), date.today(), 40.71, -74.00, mock_user, mock_db
                )
                assert result["temperature_high"] == 80

    @pytest.mark.asyncio
    async def test_copy_previous_endpoint(self):
        from app.api.v1.daily_logs import copy_previous

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.daily_logs.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.daily_logs.copy_previous_day", new_callable=AsyncMock
            ) as mock_copy:
                mock_copy.return_value = _make_daily_log()
                await copy_previous(uuid.uuid4(), date.today(), mock_user, mock_db)
                mock_copy.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_detail_not_found(self):
        from app.api.v1.daily_logs import get_log

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.daily_logs.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.daily_logs.get_daily_log_detail", new_callable=AsyncMock
            ) as mock_get:
                mock_get.side_effect = ValueError("Daily log not found")
                with pytest.raises(Exception):  # HTTPException
                    await get_log(uuid.uuid4(), uuid.uuid4(), mock_user, mock_db)

    @pytest.mark.asyncio
    async def test_list_endpoint(self):
        from app.api.v1.daily_logs import list_logs

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.daily_logs.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.daily_logs.list_daily_logs", new_callable=AsyncMock
            ) as mock_list:
                mock_list.return_value = {"data": [], "meta": {"cursor": None, "has_more": False}}
                result = await list_logs(
                    uuid.uuid4(), None, None, None, None, 20, mock_user, mock_db
                )
                assert result["data"] == []
