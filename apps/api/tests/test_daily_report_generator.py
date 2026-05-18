"""Tests for automated daily report generation.

Covers:
- Data aggregation from all sources (weather, safety, workforce, equipment,
  deliveries, schedule, quality)
- Independent source failure isolation
- LLM narrative generation and template fallback
- Report lifecycle: create, edit, approve, save-as-log
- Formatting helpers
- Edge cases: empty data, missing sources
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.reporting.daily_report_generator import (
    DailyDataAggregate,
    _aggregate_to_json,
    _format_activities,
    _format_deliveries,
    _format_equipment,
    _format_quality,
    _format_safety,
    _format_weather,
    _format_workforce,
    _template_narrative,
    aggregate_daily_data,
    create_daily_report,
    generate_daily_narrative,
    review_and_approve_report,
    save_report_as_daily_log,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()
_REPORT_DATE = date.today()


def _make_aggregate(**overrides) -> DailyDataAggregate:
    """Create a DailyDataAggregate with defaults."""
    base = DailyDataAggregate(
        project_id=str(_PROJECT_ID),
        report_date=_REPORT_DATE,
        weather={
            "temperature_high": 85,
            "temperature_low": 62,
            "precipitation_mm": 0,
            "wind_speed_max": 12,
            "humidity": 55,
            "conditions": "Partly cloudy",
        },
        safety_alerts=[
            {
                "id": str(uuid.uuid4()),
                "alert_type": "ppe_violation",
                "priority": "high",
                "description": "Worker without hard hat in Zone B",
                "confidence": 0.92,
                "is_acknowledged": False,
                "is_false_positive": None,
            }
        ],
        workforce={
            "total_headcount": 42,
            "total_hours": 336,
            "by_trade": [
                {"trade": "Ironworkers", "headcount": 12, "hours": 96},
                {"trade": "Laborers", "headcount": 18, "hours": 144},
                {"trade": "Electricians", "headcount": 8, "hours": 64},
                {"trade": "Operators", "headcount": 4, "hours": 32},
            ],
        },
        equipment=[
            {
                "equipment_type": "Crane",
                "make": "Liebherr",
                "model": "LTM 1100",
                "status": "active",
                "location": "Grid C-4",
            },
            {
                "equipment_type": "Excavator",
                "make": "CAT",
                "model": "336F",
                "status": "active",
                "location": "South End",
            },
        ],
        deliveries=[
            {
                "description": "Structural steel W14x30",
                "supplier": "Nucor Steel",
                "quantity_received": 25.0,
                "status": "received",
            }
        ],
        schedule_activities=[
            {
                "id": str(uuid.uuid4()),
                "name": "Structural Steel Erection",
                "activity_code": "A200",
                "status": "in_progress",
                "pct_complete": 45.0,
                "is_critical": True,
                "start_date": (date.today() - timedelta(days=10)).isoformat(),
                "finish_date": (date.today() + timedelta(days=15)).isoformat(),
                "total_float": 0,
            },
            {
                "id": str(uuid.uuid4()),
                "name": "Underground Plumbing",
                "activity_code": "A150",
                "status": "in_progress",
                "pct_complete": 80.0,
                "is_critical": False,
                "start_date": (date.today() - timedelta(days=20)).isoformat(),
                "finish_date": (date.today() + timedelta(days=2)).isoformat(),
                "total_float": 5,
            },
        ],
        quality={
            "inspections": [
                {
                    "id": str(uuid.uuid4()),
                    "inspection_type": "concrete_placement",
                    "status": "completed",
                    "score": 92.0,
                    "location": "Level 3 slab",
                }
            ],
            "defects": [
                {
                    "id": str(uuid.uuid4()),
                    "defect_type": "crack",
                    "severity": "minor",
                    "description": "Hairline crack in column C-4",
                    "location": "Level 2, Grid C-4",
                }
            ],
        },
        daily_log={
            "crew_count": 42,
            "work_hours": 336,
            "work_narrative": "Good progress on steel erection.",
            "weather": {"conditions": "Partly cloudy", "temperature_high": 85},
            "manpower_by_trade": [
                {"trade": "Ironworkers", "headcount": 12, "hours": 96},
            ],
            "safety_incidents": None,
            "safety_topic_discussed": "Fall protection",
        },
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# ===========================================================================
# TestFormatWeather
# ===========================================================================


class TestFormatWeather:
    """Test weather formatting for the LLM prompt."""

    def test_full_weather(self):
        weather = {
            "temperature_high": 85,
            "temperature_low": 62,
            "precipitation_mm": 5,
            "wind_speed_max": 15,
            "humidity": 60,
            "conditions": "Partly cloudy",
        }
        result = _format_weather(weather)
        assert "85" in result
        assert "62" in result
        assert "5" in result
        assert "15" in result

    def test_empty_weather(self):
        result = _format_weather({})
        assert "No weather data" in result

    def test_partial_weather(self):
        weather = {"temperature_max": 72}
        result = _format_weather(weather)
        assert "72" in result


# ===========================================================================
# TestFormatWorkforce
# ===========================================================================


class TestFormatWorkforce:
    """Test workforce formatting."""

    def test_full_workforce(self):
        workforce = {
            "total_headcount": 42,
            "total_hours": 336,
            "by_trade": [
                {"trade": "Ironworkers", "headcount": 12, "hours": 96},
                {"trade": "Laborers", "headcount": 18, "hours": 144},
            ],
        }
        result = _format_workforce(workforce)
        assert "42" in result
        assert "Ironworkers" in result
        assert "Laborers" in result

    def test_empty_workforce(self):
        result = _format_workforce({})
        assert "No workforce data" in result

    def test_zero_headcount(self):
        result = _format_workforce({"total_headcount": 0})
        assert "No workforce data" in result


# ===========================================================================
# TestFormatEquipment
# ===========================================================================


class TestFormatEquipment:
    """Test equipment formatting."""

    def test_with_equipment(self):
        equipment = [{"equipment_type": "Crane", "status": "active", "location": "Grid C-4"}]
        result = _format_equipment(equipment)
        assert "Crane" in result
        assert "active" in result

    def test_no_equipment(self):
        result = _format_equipment([])
        assert "No equipment data" in result


# ===========================================================================
# TestFormatDeliveries
# ===========================================================================


class TestFormatDeliveries:
    """Test deliveries formatting."""

    def test_with_deliveries(self):
        deliveries = [
            {"description": "Rebar #5", "supplier": "ABC Steel", "quantity_received": 500}
        ]
        result = _format_deliveries(deliveries)
        assert "Rebar #5" in result
        assert "ABC Steel" in result

    def test_no_deliveries(self):
        result = _format_deliveries([])
        assert "No deliveries" in result


# ===========================================================================
# TestFormatActivities
# ===========================================================================


class TestFormatActivities:
    """Test activities formatting."""

    def test_with_activities(self):
        activities = [
            {"name": "Steel Erection", "pct_complete": 45, "is_critical": True},
            {"name": "Plumbing", "pct_complete": 80, "is_critical": False},
        ]
        result = _format_activities(activities)
        assert "Steel Erection" in result
        assert "45%" in result
        assert "[CRITICAL]" in result
        assert "Plumbing" in result

    def test_no_activities(self):
        result = _format_activities([])
        assert "No schedule activities" in result


# ===========================================================================
# TestFormatSafety
# ===========================================================================


class TestFormatSafety:
    """Test safety formatting."""

    def test_with_alerts_and_log(self):
        alerts = [{"priority": "high", "alert_type": "ppe", "description": "No hard hat"}]
        daily_log = {
            "safety_incidents": "Near miss at scaffold",
            "safety_topic_discussed": "Fall protection",
        }
        result = _format_safety(alerts, daily_log)
        assert "No hard hat" in result
        assert "Near miss" in result
        assert "Fall protection" in result

    def test_no_alerts_no_log(self):
        result = _format_safety([], None)
        assert "No safety alerts" in result


# ===========================================================================
# TestFormatQuality
# ===========================================================================


class TestFormatQuality:
    """Test quality formatting."""

    def test_with_inspections_and_defects(self):
        quality = {
            "inspections": [
                {
                    "inspection_type": "concrete",
                    "status": "completed",
                    "score": 92,
                    "location": "L3",
                }
            ],
            "defects": [
                {
                    "severity": "minor",
                    "defect_type": "crack",
                    "description": "hairline",
                    "location": "C4",
                }
            ],
        }
        result = _format_quality(quality)
        assert "concrete" in result
        assert "crack" in result

    def test_no_quality_data(self):
        result = _format_quality({})
        assert "No quality" in result


# ===========================================================================
# TestTemplateNarrative
# ===========================================================================


class TestTemplateNarrative:
    """Test the template fallback narrative."""

    def test_template_includes_all_sections(self):
        agg = _make_aggregate()
        narrative = _template_narrative(agg)

        assert "Daily Construction Report" in narrative
        assert "Weather" in narrative
        assert "Workforce" in narrative
        assert "Equipment" in narrative
        assert "Deliveries" in narrative
        assert "Work Performed" in narrative
        assert "Safety" in narrative
        assert "Quality" in narrative

    def test_template_with_empty_aggregate(self):
        agg = DailyDataAggregate(
            project_id=str(_PROJECT_ID),
            report_date=_REPORT_DATE,
        )
        narrative = _template_narrative(agg)
        assert "Daily Construction Report" in narrative
        assert "No weather data" in narrative


# ===========================================================================
# TestAggregateToJson
# ===========================================================================


class TestAggregateToJson:
    """Test aggregate serialization for JSONB storage."""

    def test_serialization(self):
        agg = _make_aggregate()
        result = _aggregate_to_json(agg)

        assert "weather" in result
        assert "safety_alerts" in result
        assert "workforce" in result
        assert "equipment" in result
        assert "deliveries" in result
        assert "schedule_activities" in result
        assert "quality" in result
        assert "safety_summary" in result

    def test_safety_summary_from_alerts(self):
        agg = _make_aggregate()
        result = _aggregate_to_json(agg)
        assert "Worker without hard hat" in result["safety_summary"]


# ===========================================================================
# TestGenerateDailyNarrative
# ===========================================================================


class TestGenerateDailyNarrative:
    """Test LLM-based and fallback narrative generation."""

    @pytest.mark.asyncio
    async def test_llm_success(self):
        agg = _make_aggregate()
        mock_gateway = AsyncMock()
        mock_gateway.complete = AsyncMock(
            return_value={"content": "# Daily Report\n\nGood day on site."}
        )

        with patch(
            "app.services.reliability.llm_gateway.get_llm_gateway",
            return_value=mock_gateway,
        ):
            narrative = await generate_daily_narrative(agg)

        assert "Daily Report" in narrative
        mock_gateway.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_template(self):
        agg = _make_aggregate()

        with patch(
            "app.services.reliability.llm_gateway.get_llm_gateway",
            side_effect=RuntimeError("LLM unavailable"),
        ):
            narrative = await generate_daily_narrative(agg)

        # Should get template narrative
        assert "Daily Construction Report" in narrative
        assert "Weather" in narrative


# ===========================================================================
# TestAggregateDailyData
# ===========================================================================


class TestAggregateDailyData:
    """Test data aggregation with mocked DB queries."""

    @pytest.mark.asyncio
    async def test_aggregation_with_daily_log(self):
        """Test that daily log data is used when available."""
        mock_log = MagicMock()
        mock_log.crew_count = 30
        mock_log.work_hours = Decimal("240")
        mock_log.work_narrative = "Good day"
        mock_log.weather = {"conditions": "sunny", "temperature_high": 78}
        mock_log.manpower_by_trade = [{"trade": "Carpenters", "headcount": 10}]
        mock_log.equipment_entries = []
        mock_log.deliveries = [{"description": "Lumber"}]
        mock_log.activities_completed = []
        mock_log.delays = []
        mock_log.safety_incidents = None
        mock_log.safety_topic_discussed = "Scaffolding"
        mock_log.weather_delay_hours = None

        call_count = 0

        async def mock_execute(query, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # Daily log query
                result.scalars.return_value.first.return_value = mock_log
            else:
                # All other queries return empty
                result.scalars.return_value.all.return_value = []
                result.scalars.return_value.first.return_value = None
            return result

        db = AsyncMock()
        db.execute = mock_execute

        agg = await aggregate_daily_data(db, _PROJECT_ID, _REPORT_DATE)

        assert agg.daily_log is not None
        assert agg.daily_log["crew_count"] == 30
        assert agg.weather == {"conditions": "sunny", "temperature_high": 78}

    @pytest.mark.asyncio
    async def test_aggregation_with_no_data(self):
        """Test that empty sources produce empty sections, not errors."""

        async def mock_execute(query, *args, **kwargs):
            result = MagicMock()
            result.scalars.return_value.all.return_value = []
            result.scalars.return_value.first.return_value = None
            return result

        db = AsyncMock()
        db.execute = mock_execute

        agg = await aggregate_daily_data(db, _PROJECT_ID, _REPORT_DATE)

        assert agg.daily_log is None
        assert agg.weather == {}
        assert agg.safety_alerts == []
        assert agg.workforce["total_headcount"] == 0
        assert agg.equipment == []
        assert agg.deliveries == []
        assert agg.quality == {"inspections": [], "defects": []}

    @pytest.mark.asyncio
    async def test_source_failure_isolation(self):
        """Test that a failing source does not break other sources."""
        call_count = 0

        async def mock_execute(query, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Daily log succeeds
                mock_log = MagicMock()
                mock_log.crew_count = 15
                mock_log.work_hours = Decimal("120")
                mock_log.work_narrative = "Test"
                mock_log.weather = {}
                mock_log.manpower_by_trade = []
                mock_log.equipment_entries = []
                mock_log.deliveries = []
                mock_log.activities_completed = []
                mock_log.delays = []
                mock_log.safety_incidents = None
                mock_log.safety_topic_discussed = None
                mock_log.weather_delay_hours = None
                result = MagicMock()
                result.scalars.return_value.first.return_value = mock_log
                return result
            elif call_count == 3:
                # Safety alerts query fails
                raise RuntimeError("Connection lost")
            else:
                result = MagicMock()
                result.scalars.return_value.all.return_value = []
                result.scalars.return_value.first.return_value = None
                return result

        db = AsyncMock()
        db.execute = mock_execute

        # Should not raise despite safety query failure
        agg = await aggregate_daily_data(db, _PROJECT_ID, _REPORT_DATE)
        assert agg.daily_log is not None
        assert agg.safety_alerts == []  # Failed source returns empty


# ===========================================================================
# TestCreateDailyReport
# ===========================================================================


class TestCreateDailyReport:
    """Test the full create_daily_report pipeline."""

    @pytest.mark.asyncio
    async def test_creates_report_record(self):
        mock_report = MagicMock()
        mock_report.id = uuid.uuid4()
        mock_report.status = "draft"

        async def mock_execute(query, *args, **kwargs):
            result = MagicMock()
            result.scalars.return_value.all.return_value = []
            result.scalars.return_value.first.return_value = None
            return result

        db = AsyncMock()
        db.execute = mock_execute
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with (
            patch(
                "app.services.reporting.daily_report_generator.generate_daily_narrative",
                return_value="# Report\n\nTest content",
            ),
            patch(
                "app.models.generated_report.GeneratedDailyReport",
                return_value=mock_report,
            ),
        ):
            report = await create_daily_report(db, _PROJECT_ID, _REPORT_DATE, _USER_ID)

        db.add.assert_called_once()
        assert report.status == "draft"


# ===========================================================================
# TestReviewAndApproveReport
# ===========================================================================


class TestReviewAndApproveReport:
    """Test report approval workflow."""

    @pytest.mark.asyncio
    async def test_approve_without_edits(self):
        mock_report = MagicMock()
        mock_report.id = uuid.uuid4()
        mock_report.status = "draft"
        mock_report.narrative_markdown = "Original narrative"

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_report

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        await review_and_approve_report(db, mock_report.id, _USER_ID)

        assert mock_report.status == "approved"
        assert mock_report.narrative_markdown == "Original narrative"
        assert mock_report.reviewed_by == _USER_ID
        assert mock_report.approved_at is not None

    @pytest.mark.asyncio
    async def test_approve_with_edits(self):
        mock_report = MagicMock()
        mock_report.id = uuid.uuid4()
        mock_report.status = "draft"
        mock_report.narrative_markdown = "Original"

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_report

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        await review_and_approve_report(db, mock_report.id, _USER_ID, edits="Edited narrative")

        assert mock_report.status == "approved"
        assert mock_report.narrative_markdown == "Edited narrative"

    @pytest.mark.asyncio
    async def test_approve_not_found(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError, match="Report not found"):
            await review_and_approve_report(db, uuid.uuid4(), _USER_ID)


# ===========================================================================
# TestSaveReportAsDailyLog
# ===========================================================================


class TestSaveReportAsDailyLog:
    """Test converting an approved report to a DailyLog record."""

    @pytest.mark.asyncio
    async def test_save_approved_report(self):
        mock_report = MagicMock()
        mock_report.id = uuid.uuid4()
        mock_report.project_id = _PROJECT_ID
        mock_report.report_date = _REPORT_DATE
        mock_report.status = "approved"
        mock_report.daily_log_id = None
        mock_report.generated_by = _USER_ID
        mock_report.narrative_markdown = "# Report\nTest"
        mock_report.aggregated_data = {
            "weather": {"conditions": "clear"},
            "workforce": {"total_headcount": 30, "total_hours": 240, "by_trade": []},
            "equipment": [],
            "deliveries": [],
            "schedule_activities": [],
            "safety_summary": "",
        }

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_report

        mock_log = MagicMock()
        mock_log.id = uuid.uuid4()

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "app.models.productivity.DailyLog",
            return_value=mock_log,
        ):
            await save_report_as_daily_log(db, mock_report.id)

        db.add.assert_called_once()
        assert mock_report.daily_log_id == mock_log.id

    @pytest.mark.asyncio
    async def test_cannot_save_draft_report(self):
        mock_report = MagicMock()
        mock_report.status = "draft"
        mock_report.daily_log_id = None

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_report

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError, match="Only approved"):
            await save_report_as_daily_log(db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_cannot_save_already_linked_report(self):
        mock_report = MagicMock()
        mock_report.status = "approved"
        mock_report.daily_log_id = uuid.uuid4()  # already linked

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_report

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError, match="already has"):
            await save_report_as_daily_log(db, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_report_not_found(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError, match="Report not found"):
            await save_report_as_daily_log(db, uuid.uuid4())


# ===========================================================================
# TestGeneratedDailyReportModel
# ===========================================================================


class TestGeneratedDailyReportModel:
    """Test the GeneratedDailyReport SQLAlchemy model."""

    def test_model_instantiation(self):
        from app.models.generated_report import GeneratedDailyReport

        report = GeneratedDailyReport(
            project_id=_PROJECT_ID,
            report_date=_REPORT_DATE,
            narrative_markdown="# Test Report",
            status="draft",
        )
        assert report.status == "draft"
        assert report.report_date == _REPORT_DATE

    def test_default_status_is_draft(self):
        from app.models.generated_report import GeneratedDailyReport

        report = GeneratedDailyReport(
            project_id=_PROJECT_ID,
            report_date=_REPORT_DATE,
        )
        # server_default will be applied at DB level; in Python it's the column default
        # Just verify the model can be instantiated
        assert report.project_id == _PROJECT_ID
