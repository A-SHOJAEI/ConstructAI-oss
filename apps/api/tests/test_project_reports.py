"""Tests for project reports service and API endpoints.

Covers:
  - Monthly cost report generation
  - Schedule performance report generation
  - Safety trend report generation
  - Subcontractor performance report generation
  - Report API endpoints (mocked DB)
  - Edge cases (empty data, missing records)
  - Helper functions
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.reporting.project_reports import (
    _csi_to_division,
    _dec,
    _item_number_to_division,
    _month_range,
    _rfi_responsiveness_score,
    _safe_div,
    generate_monthly_cost_report,
    generate_safety_trend_report,
    generate_schedule_performance_report,
    generate_subcontractor_performance_report,
)

# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    """Test utility/helper functions."""

    def test_dec_none(self):
        assert _dec(None) == 0.0

    def test_dec_decimal(self):
        assert _dec(Decimal("123.456")) == 123.46

    def test_dec_float(self):
        assert _dec(3.14159) == 3.14

    def test_dec_int(self):
        assert _dec(42) == 42.0

    def test_safe_div_normal(self):
        assert _safe_div(10, 4) == 2.5

    def test_safe_div_zero_denominator(self):
        assert _safe_div(10, 0) == 0.0

    def test_safe_div_zero_numerator(self):
        assert _safe_div(0, 5) == 0.0

    def test_month_range_normal(self):
        first, last = _month_range(2026, 3)
        assert first == date(2026, 3, 1)
        assert last == date(2026, 3, 31)

    def test_month_range_february(self):
        first, last = _month_range(2024, 2)  # Leap year
        assert first == date(2024, 2, 1)
        assert last == date(2024, 2, 29)

    def test_month_range_december(self):
        first, last = _month_range(2026, 12)
        assert first == date(2026, 12, 1)
        assert last == date(2026, 12, 31)


class TestCSIDivisionMapping:
    """Test CSI code to division name mapping."""

    def test_csi_code_concrete(self):
        result = _csi_to_division("03 30 00")
        assert "Concrete" in result

    def test_csi_code_electrical(self):
        result = _csi_to_division("26 05 00")
        assert "Electrical" in result

    def test_csi_code_none(self):
        assert _csi_to_division(None) == "Unclassified"

    def test_csi_code_empty(self):
        assert _csi_to_division("") == "Unclassified"

    def test_csi_code_no_spaces(self):
        result = _csi_to_division("033000")
        assert "Concrete" in result

    def test_item_number_to_division_concrete(self):
        result = _item_number_to_division("03.001")
        assert "Concrete" in result

    def test_item_number_to_division_none(self):
        assert _item_number_to_division(None) == "Unclassified"

    def test_item_number_to_division_unknown(self):
        result = _item_number_to_division("XX-999")
        assert result == "General"


class TestRFIResponsivenessScore:
    """Test RFI responsiveness scoring function."""

    def test_no_rfis(self):
        assert _rfi_responsiveness_score(0, 0) == 100

    def test_fast_response(self):
        assert _rfi_responsiveness_score(1.5, 5) == 100

    def test_moderate_response(self):
        assert _rfi_responsiveness_score(4.0, 5) == 80

    def test_slow_response(self):
        assert _rfi_responsiveness_score(8.0, 5) == 60

    def test_very_slow_response(self):
        assert _rfi_responsiveness_score(15.0, 5) == 40

    def test_extremely_slow_response(self):
        assert _rfi_responsiveness_score(25.0, 5) == 20


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_mock_db():
    """Create a mock AsyncSession with execute that returns configurable results."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.flush = AsyncMock()
    return db


def _mock_scalar_result(value):
    """Create a mock result that returns a single scalar."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = value
    mock_result.scalar.return_value = value
    return mock_result


def _mock_scalars_result(items):
    """Create a mock result that returns multiple scalars."""
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = items
    mock_result.scalars.return_value = mock_scalars
    mock_result.scalar_one_or_none.return_value = items[0] if items else None
    mock_result.scalar.return_value = len(items) if items else 0
    return mock_result


# ---------------------------------------------------------------------------
# Monthly Cost Report tests
# ---------------------------------------------------------------------------


class TestMonthlyCostReport:
    """Test the monthly cost report generator."""

    @pytest.mark.asyncio
    async def test_empty_project(self):
        """Report should work with no data."""
        db = _make_mock_db()
        db.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(None),  # No estimate
                _mock_scalars_result([]),  # No pay apps
                _mock_scalar_result(0),  # No cumulative
                _mock_scalars_result([]),  # No change orders
                _mock_scalar_result(None),  # No EVM snapshot
                _mock_scalar_result(None),  # No cash flow
            ]
        )

        project_id = uuid.uuid4()
        result = await generate_monthly_cost_report(db, project_id, 3, 2026)

        assert result["report_type"] == "monthly_cost"
        assert result["period"]["month"] == 3
        assert result["period"]["year"] == 2026
        assert result["summary"]["original_budget"] == 0.0
        assert result["budget_vs_actual_by_division"] == []
        assert result["change_order_summary"]["count"] == 0

    @pytest.mark.asyncio
    async def test_with_estimate(self):
        """Report should pick up budget from a CostEstimate."""
        db = _make_mock_db()

        mock_estimate = MagicMock()
        mock_estimate.id = uuid.uuid4()
        mock_estimate.total_cost = Decimal("5000000.00")

        mock_line_item = MagicMock()
        mock_line_item.csi_code = "03 30 00"
        mock_line_item.total_cost = Decimal("500000.00")

        db.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(mock_estimate),  # Estimate found
                _mock_scalars_result([mock_line_item]),  # Line items
                _mock_scalars_result([]),  # No pay apps
                _mock_scalar_result(0),  # No cumulative
                _mock_scalars_result([]),  # No COs
                _mock_scalar_result(None),  # No EVM
                _mock_scalar_result(None),  # No cash flow
            ]
        )

        project_id = uuid.uuid4()
        result = await generate_monthly_cost_report(db, project_id, 3, 2026)

        assert result["summary"]["original_budget"] == 5000000.0
        assert len(result["budget_vs_actual_by_division"]) >= 1

    @pytest.mark.asyncio
    async def test_with_change_orders(self):
        """Report should include change order impacts."""
        db = _make_mock_db()

        mock_co = MagicMock()
        mock_co.co_number = "CO-001"
        mock_co.title = "Foundation change"
        mock_co.cost_impact = Decimal("50000.00")
        mock_co.schedule_impact_days = 5
        mock_co.status = "approved"

        db.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(None),  # No estimate
                _mock_scalars_result([]),  # No pay apps
                _mock_scalar_result(0),  # No cumulative
                _mock_scalars_result([mock_co]),  # One CO
                _mock_scalar_result(None),  # No EVM
                _mock_scalar_result(None),  # No cash flow
            ]
        )

        project_id = uuid.uuid4()
        result = await generate_monthly_cost_report(db, project_id, 3, 2026)

        assert result["change_order_summary"]["count"] == 1
        assert result["change_order_summary"]["total_cost_impact"] == 50000.0
        assert result["summary"]["approved_cos_total"] == 50000.0


# ---------------------------------------------------------------------------
# Schedule Performance Report tests
# ---------------------------------------------------------------------------


class TestSchedulePerformanceReport:
    """Test the schedule performance report generator."""

    @pytest.mark.asyncio
    async def test_empty_project(self):
        """Report should work with no data."""
        db = _make_mock_db()
        db.execute = AsyncMock(
            side_effect=[
                _mock_scalars_result([]),  # No EVM snapshots
                _mock_scalars_result([]),  # No critical activities
                _mock_scalars_result([]),  # No delayed activities
                _mock_scalars_result([]),  # No lookahead activities
            ]
        )

        project_id = uuid.uuid4()
        result = await generate_schedule_performance_report(db, project_id)

        assert result["report_type"] == "schedule_performance"
        assert result["summary"]["schedule_status"] == "unknown"
        assert result["spi_trend"] == []

    @pytest.mark.asyncio
    async def test_with_evm_data(self):
        """Report should show SPI trend from EVM snapshots."""
        db = _make_mock_db()

        snap = MagicMock()
        snap.snapshot_date = date(2026, 3, 1)
        snap.spi = Decimal("0.97")
        snap.cpi = Decimal("1.02")
        snap.percent_complete = Decimal("45.00")

        db.execute = AsyncMock(
            side_effect=[
                _mock_scalars_result([snap]),  # EVM snapshots
                _mock_scalars_result([]),  # Critical activities
                _mock_scalars_result([]),  # Delayed activities
                _mock_scalars_result([]),  # Lookahead activities
            ]
        )

        project_id = uuid.uuid4()
        result = await generate_schedule_performance_report(db, project_id)

        assert result["summary"]["current_spi"] == 0.97
        assert result["summary"]["current_cpi"] == 1.02
        assert result["summary"]["schedule_status"] == "on_track"

    @pytest.mark.asyncio
    async def test_behind_schedule_status(self):
        """Report should flag behind schedule when SPI < 0.85."""
        db = _make_mock_db()

        snap = MagicMock()
        snap.snapshot_date = date(2026, 3, 1)
        snap.spi = Decimal("0.75")
        snap.cpi = Decimal("0.80")
        snap.percent_complete = Decimal("30.00")

        db.execute = AsyncMock(
            side_effect=[
                _mock_scalars_result([snap]),
                _mock_scalars_result([]),
                _mock_scalars_result([]),
                _mock_scalars_result([]),
            ]
        )

        project_id = uuid.uuid4()
        result = await generate_schedule_performance_report(db, project_id)

        assert result["summary"]["schedule_status"] == "behind"

    @pytest.mark.asyncio
    async def test_with_critical_activities(self):
        """Report should include critical path activities."""
        db = _make_mock_db()

        act = MagicMock()
        act.id = uuid.uuid4()
        act.name = "Foundation Pour"
        act.activity_code = "A-100"
        act.duration_days = 10
        act.start_date = date(2026, 3, 15)
        act.finish_date = date(2026, 3, 25)
        act.total_float = 0
        act.status = "in_progress"
        act.pct_complete = Decimal("50.00")

        db.execute = AsyncMock(
            side_effect=[
                _mock_scalars_result([]),  # EVM
                _mock_scalars_result([act]),  # Critical activities
                _mock_scalars_result([]),  # Delayed
                _mock_scalars_result([]),  # Lookahead
            ]
        )

        project_id = uuid.uuid4()
        result = await generate_schedule_performance_report(db, project_id)

        assert result["summary"]["critical_activity_count"] == 1
        assert result["critical_activities"][0]["name"] == "Foundation Pour"

    @pytest.mark.asyncio
    async def test_with_delayed_activities(self):
        """Report should flag delayed activities."""
        db = _make_mock_db()

        delayed = MagicMock()
        delayed.id = uuid.uuid4()
        delayed.name = "Excavation"
        delayed.activity_code = "A-050"
        delayed.finish_date = date.today() - timedelta(days=5)
        delayed.pct_complete = Decimal("80.00")
        delayed.status = "in_progress"

        db.execute = AsyncMock(
            side_effect=[
                _mock_scalars_result([]),  # EVM
                _mock_scalars_result([]),  # Critical
                _mock_scalars_result([delayed]),  # Delayed
                _mock_scalars_result([]),  # Lookahead
            ]
        )

        project_id = uuid.uuid4()
        result = await generate_schedule_performance_report(db, project_id)

        assert result["summary"]["delayed_activity_count"] == 1
        assert result["delayed_activities"][0]["days_late"] == 5


# ---------------------------------------------------------------------------
# Safety Trend Report tests
# ---------------------------------------------------------------------------


class TestSafetyTrendReport:
    """Test the safety trend report generator."""

    @pytest.mark.asyncio
    async def test_empty_project(self):
        """Report should work with no safety alerts."""
        db = _make_mock_db()
        db.execute = AsyncMock(return_value=_mock_scalars_result([]))

        project_id = uuid.uuid4()
        result = await generate_safety_trend_report(db, project_id, months=6)

        assert result["report_type"] == "safety_trend"
        assert result["summary"]["total_alerts"] == 0
        assert result["monthly_trend"] == []
        assert result["top_hazards"] == []

    @pytest.mark.asyncio
    async def test_with_alerts(self):
        """Report should aggregate alerts correctly."""
        db = _make_mock_db()

        alerts = []
        for i in range(5):
            alert = MagicMock()
            alert.created_at = datetime(2026, 2, 10 + i)
            alert.priority = "high" if i < 3 else "critical"
            alert.alert_type = "no_hard_hat" if i % 2 == 0 else "near_miss"
            alert.is_false_positive = i == 4
            alert.is_acknowledged = i < 4
            alerts.append(alert)

        db.execute = AsyncMock(return_value=_mock_scalars_result(alerts))

        project_id = uuid.uuid4()
        result = await generate_safety_trend_report(db, project_id, months=6)

        assert result["summary"]["total_alerts"] == 5
        assert result["summary"]["critical_alerts"] == 2
        assert len(result["monthly_trend"]) >= 1

    @pytest.mark.asyncio
    async def test_top_hazards_ordering(self):
        """Top hazards should be ordered by count descending."""
        db = _make_mock_db()

        alerts = []
        for _ in range(5):
            a = MagicMock()
            a.created_at = datetime(2026, 3, 1)
            a.priority = "high"
            a.alert_type = "no_hard_hat"
            a.is_false_positive = False
            a.is_acknowledged = True
            alerts.append(a)
        for _ in range(2):
            a = MagicMock()
            a.created_at = datetime(2026, 3, 1)
            a.priority = "medium"
            a.alert_type = "no_vest"
            a.is_false_positive = False
            a.is_acknowledged = True
            alerts.append(a)

        db.execute = AsyncMock(return_value=_mock_scalars_result(alerts))

        project_id = uuid.uuid4()
        result = await generate_safety_trend_report(db, project_id, months=6)

        assert result["top_hazards"][0]["type"] == "no_hard_hat"
        assert result["top_hazards"][0]["count"] == 5

    @pytest.mark.asyncio
    async def test_trir_calculation(self):
        """TRIR should be calculated from critical alerts."""
        db = _make_mock_db()

        # 10 critical alerts
        alerts = []
        for i in range(10):
            a = MagicMock()
            a.created_at = datetime(2026, 3, 1 + i)
            a.priority = "critical"
            a.alert_type = "fall"
            a.is_false_positive = False
            a.is_acknowledged = True
            alerts.append(a)

        db.execute = AsyncMock(return_value=_mock_scalars_result(alerts))

        project_id = uuid.uuid4()
        result = await generate_safety_trend_report(db, project_id, months=6)

        assert result["summary"]["estimated_trir"] > 0


# ---------------------------------------------------------------------------
# Subcontractor Performance Report tests
# ---------------------------------------------------------------------------


class TestSubcontractorPerformanceReport:
    """Test the subcontractor performance report generator."""

    @pytest.mark.asyncio
    async def test_no_subcontractors(self):
        """Report should work with no subs."""
        db = _make_mock_db()
        db.execute = AsyncMock(return_value=_mock_scalars_result([]))

        project_id = uuid.uuid4()
        result = await generate_subcontractor_performance_report(db, project_id)

        assert result["report_type"] == "subcontractor_performance"
        assert result["subcontractor_count"] == 0
        assert result["scorecards"] == []

    @pytest.mark.asyncio
    async def test_with_subcontractors(self):
        """Report should generate scorecards for each sub."""
        db = _make_mock_db()

        sub = MagicMock()
        sub.id = uuid.uuid4()
        sub.user_id = uuid.uuid4()
        sub.company_name = "ABC Concrete LLC"
        sub.trade = "concrete"
        sub.status = "active"

        # First call returns subs, subsequent calls return empty for scorecard queries
        db.execute = AsyncMock(
            side_effect=[
                _mock_scalars_result([sub]),  # Subcontractor profiles
                _mock_scalars_result([]),  # Submissions
                _mock_scalar_result(0),  # Defect count
                _mock_scalars_result([]),  # RFIs
            ]
        )

        project_id = uuid.uuid4()
        result = await generate_subcontractor_performance_report(db, project_id)

        assert result["subcontractor_count"] == 1
        assert result["scorecards"][0]["company_name"] == "ABC Concrete LLC"
        assert result["scorecards"][0]["overall_score"] >= 0

    @pytest.mark.asyncio
    async def test_scorecard_with_submissions(self):
        """Scorecard should reflect submission compliance."""
        db = _make_mock_db()

        sub = MagicMock()
        sub.id = uuid.uuid4()
        sub.user_id = uuid.uuid4()
        sub.company_name = "Steel Works Inc"
        sub.trade = "steel"
        sub.status = "active"

        submission1 = MagicMock()
        submission1.status = "approved"
        submission2 = MagicMock()
        submission2.status = "pending"

        db.execute = AsyncMock(
            side_effect=[
                _mock_scalars_result([sub]),  # Subs
                _mock_scalars_result([submission1, submission2]),  # Submissions
                _mock_scalar_result(0),  # Defects
                _mock_scalars_result([]),  # RFIs
            ]
        )

        project_id = uuid.uuid4()
        result = await generate_subcontractor_performance_report(db, project_id)

        scorecard = result["scorecards"][0]
        assert scorecard["metrics"]["total_submissions"] == 2
        assert scorecard["metrics"]["approved_submissions"] == 1
        # 1/2 = 50% submission compliance
        assert scorecard["scores"]["submission_compliance"] == 50

    @pytest.mark.asyncio
    async def test_scorecard_with_rfi_responses(self):
        """Scorecard should reflect RFI response times."""
        db = _make_mock_db()

        sub = MagicMock()
        sub.id = uuid.uuid4()
        sub.user_id = uuid.uuid4()
        sub.company_name = "MEP Partners"
        sub.trade = "mechanical"
        sub.status = "active"

        rfi = MagicMock()
        rfi.date_answered = datetime(2026, 3, 5, 10, 0, 0)
        rfi.created_at = datetime(2026, 3, 1, 10, 0, 0)  # 4 days

        db.execute = AsyncMock(
            side_effect=[
                _mock_scalars_result([sub]),  # Subs
                _mock_scalars_result([]),  # Submissions
                _mock_scalar_result(0),  # Defects
                _mock_scalars_result([rfi]),  # RFIs
            ]
        )

        project_id = uuid.uuid4()
        result = await generate_subcontractor_performance_report(db, project_id)

        scorecard = result["scorecards"][0]
        assert scorecard["metrics"]["rfi_count"] == 1
        assert scorecard["metrics"]["rfis_answered"] == 1
        assert scorecard["metrics"]["avg_rfi_response_days"] == 4.0


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """Test that report output matches the Pydantic schemas."""

    def test_monthly_cost_schema(self):
        from app.schemas.project_reports import MonthlyCostReportResponse

        data = {
            "report_type": "monthly_cost",
            "project_id": str(uuid.uuid4()),
            "period": {"month": 3, "year": 2026},
            "generated_at": datetime.utcnow().isoformat(),
            "summary": {
                "original_budget": 5000000.0,
                "approved_cos_total": 50000.0,
                "adjusted_budget": 5050000.0,
                "actuals_this_period": 100000.0,
                "actuals_cumulative": 2000000.0,
                "remaining_budget": 3050000.0,
                "percent_spent": 39.6,
            },
            "budget_vs_actual_by_division": [
                {
                    "division": "Div 03 - Concrete",
                    "budget": 500000.0,
                    "actual_this_period": 50000.0,
                    "variance": 450000.0,
                    "percent_spent": 10.0,
                }
            ],
            "change_order_summary": {
                "count": 1,
                "total_cost_impact": 50000.0,
                "total_schedule_impact_days": 5,
                "items": [
                    {
                        "co_number": "CO-001",
                        "title": "Test",
                        "cost_impact": 50000.0,
                        "schedule_impact_days": 5,
                        "status": "approved",
                    }
                ],
            },
            "projection": {},
            "cash_flow_summary": None,
        }
        report = MonthlyCostReportResponse(**data)
        assert report.report_type == "monthly_cost"

    def test_schedule_performance_schema(self):
        from app.schemas.project_reports import SchedulePerformanceReportResponse

        data = {
            "report_type": "schedule_performance",
            "project_id": str(uuid.uuid4()),
            "generated_at": datetime.utcnow().isoformat(),
            "summary": {
                "current_spi": 0.95,
                "current_cpi": 1.02,
                "schedule_status": "on_track",
                "critical_activity_count": 5,
                "delayed_activity_count": 2,
                "lookahead_activity_count": 8,
            },
            "spi_trend": [],
            "critical_activities": [],
            "delayed_activities": [],
            "two_week_lookahead": [],
        }
        report = SchedulePerformanceReportResponse(**data)
        assert report.summary.schedule_status == "on_track"

    def test_safety_trend_schema(self):
        from app.schemas.project_reports import SafetyTrendReportResponse

        data = {
            "report_type": "safety_trend",
            "project_id": str(uuid.uuid4()),
            "generated_at": datetime.utcnow().isoformat(),
            "period": {"months": 6, "start_date": "2025-09-15", "end_date": "2026-03-15"},
            "summary": {
                "total_alerts": 50,
                "critical_alerts": 5,
                "false_positive_rate": 0.1,
                "acknowledgment_rate": 0.9,
                "estimated_trir": 3.2,
            },
            "monthly_trend": [],
            "top_hazards": [{"type": "no_hard_hat", "count": 20}],
            "priority_distribution": {"critical": 5, "high": 15},
            "alert_type_distribution": {"no_hard_hat": 20},
        }
        report = SafetyTrendReportResponse(**data)
        assert report.summary.total_alerts == 50

    def test_subcontractor_schema(self):
        from app.schemas.project_reports import SubcontractorPerformanceReportResponse

        data = {
            "report_type": "subcontractor_performance",
            "project_id": str(uuid.uuid4()),
            "generated_at": datetime.utcnow().isoformat(),
            "subcontractor_count": 1,
            "scorecards": [
                {
                    "subcontractor_id": str(uuid.uuid4()),
                    "company_name": "Test Co",
                    "trade": "concrete",
                    "status": "active",
                    "overall_score": 85,
                    "scores": {
                        "submission_compliance": 90,
                        "quality": 80,
                        "rfi_responsiveness": 85,
                    },
                    "metrics": {
                        "total_submissions": 10,
                        "approved_submissions": 9,
                        "defect_count": 2,
                        "rfi_count": 5,
                        "rfis_answered": 5,
                        "avg_rfi_response_days": 3.0,
                    },
                }
            ],
        }
        report = SubcontractorPerformanceReportResponse(**data)
        assert report.subcontractor_count == 1


# ---------------------------------------------------------------------------
# Bulk RFI Update schema tests
# ---------------------------------------------------------------------------


class TestBulkRFISchema:
    """Test the BulkRFIUpdateRequest schema."""

    def test_valid_request(self):
        from app.schemas.communication import BulkRFIUpdateRequest

        req = BulkRFIUpdateRequest(
            rfi_ids=[uuid.uuid4(), uuid.uuid4()],
            status="answered",
        )
        assert len(req.rfi_ids) == 2
        assert req.status == "answered"
        assert req.assigned_to is None

    def test_with_assigned_to(self):
        from app.schemas.communication import BulkRFIUpdateRequest

        user_id = uuid.uuid4()
        req = BulkRFIUpdateRequest(
            rfi_ids=[uuid.uuid4()],
            assigned_to=user_id,
        )
        assert req.assigned_to == user_id

    def test_empty_ids_rejected(self):
        from pydantic import ValidationError

        from app.schemas.communication import BulkRFIUpdateRequest

        with pytest.raises(ValidationError):
            BulkRFIUpdateRequest(rfi_ids=[], status="open")


class TestBulkRFIResponse:
    """Test the BulkRFIUpdateResponse schema."""

    def test_valid_response(self):
        from app.schemas.communication import BulkRFIUpdateResponse

        resp = BulkRFIUpdateResponse(
            updated=5, failed=1, errors=[{"rfi_id": "x", "error": "not found"}]
        )
        assert resp.updated == 5
        assert resp.failed == 1
        assert len(resp.errors) == 1


# ---------------------------------------------------------------------------
# Schedule Activity List Response schema tests
# ---------------------------------------------------------------------------


class TestScheduleActivityListResponse:
    """Test the schedule activity list response schema."""

    def test_valid_response(self):
        from app.schemas.scheduling import ScheduleActivityListResponse

        resp = ScheduleActivityListResponse(
            data=[],
            total=0,
            skip=0,
            limit=50,
        )
        assert resp.total == 0
        assert resp.skip == 0
        assert resp.limit == 50
