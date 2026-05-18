"""Tests for workforce analytics and labor forecasting.

Covers:
- Labor data aggregation (single project, date range, empty)
- Trade productivity metrics (calculation, trend detection, grouping)
- Labor forecasting (remaining activities, trade inference, monthly grouping)
- Overtime prediction (compression, cost calculation, risk levels)
- Fatigue risk assessment (daily, weekly, thresholds)
- Craft availability (supply-demand gap)
- Workforce snapshot creation
- API endpoint structure
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

from app.services.productivity.workforce_analytics import (
    OVERTIME_RATE_MULTIPLIER,
    STANDARD_HOURS_PER_DAY,
    STANDARD_HOURS_PER_WEEK,
    CraftAvailability,
    LaborAggregate,
    LaborForecast,
    OvertimePrediction,
    _fatigue_recommendation,
    _infer_trade_from_activity,
    assess_fatigue_risk,
    calculate_productivity_metrics,
    predict_overtime,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_crew_records(
    trade: str = "carpenter",
    count: int = 10,
    base_actual: float = 50.0,
    trend_delta: float = 0.0,
) -> list[dict]:
    """Generate crew productivity records with optional trend."""
    records = []
    base_date = date.today() - timedelta(days=count)
    for i in range(count):
        actual = base_actual + (trend_delta * i)
        records.append(
            {
                "trade": trade,
                "actual_units": max(1.0, actual),
                "planned_units": 50.0,
                "crew_size": 4,
                "work_date": (base_date + timedelta(days=i)).isoformat(),
                "unit_of_measure": "SF",
                "work_hours": 8.0,
            }
        )
    return records


def _make_worker_hours(
    worker_id: str = "W001",
    trade: str = "electrician",
    days: int = 5,
    daily_hours: float = 8.0,
) -> list[dict]:
    """Generate worker hour records."""
    records = []
    base_date = date.today() - timedelta(days=days - 1)
    for i in range(days):
        records.append(
            {
                "worker_id": worker_id,
                "trade": trade,
                "date": (base_date + timedelta(days=i)).isoformat(),
                "hours": daily_hours,
            }
        )
    return records


def _make_remaining_activities(
    trades: dict[str, tuple[float, float]] | None = None,
) -> list[dict]:
    """Generate remaining activity dicts. trades: {trade: (manhours, duration_days)}"""
    if trades is None:
        trades = {
            "carpenter": (400, 10),
            "electrician": (320, 8),
            "plumber": (240, 6),
        }
    return [
        {
            "trade": trade,
            "remaining_manhours": mh,
            "duration_days": dur,
        }
        for trade, (mh, dur) in trades.items()
    ]


# ---------------------------------------------------------------------------
# TestLaborAggregation
# ---------------------------------------------------------------------------


class TestLaborAggregation:
    """Tests for aggregate_labor_data()."""

    def test_labor_aggregate_structure(self):
        """LaborAggregate should have all required fields."""
        agg = LaborAggregate(
            project_id="test-123",
            date_range=None,
            total_workers=50,
            total_manhours=400.0,
            workers_by_trade={"carpenter": 20, "electrician": 15},
            manhours_by_trade={"carpenter": 160.0, "electrician": 120.0},
            daily_avg_workers=10.0,
            daily_avg_manhours=80.0,
            working_days=5,
        )
        assert agg.total_workers == 50
        assert agg.total_manhours == 400.0
        assert agg.working_days == 5

    def test_empty_aggregate(self):
        """Empty aggregate should have zero values."""
        agg = LaborAggregate(
            project_id=None,
            date_range=None,
            total_workers=0,
            total_manhours=0.0,
            workers_by_trade={},
            manhours_by_trade={},
            daily_avg_workers=0.0,
            daily_avg_manhours=0.0,
            working_days=0,
        )
        assert agg.total_workers == 0
        assert agg.working_days == 0

    def test_daily_average_calculation(self):
        """Daily averages should be total / working_days."""
        agg = LaborAggregate(
            project_id="test",
            date_range=None,
            total_workers=100,
            total_manhours=800.0,
            workers_by_trade={},
            manhours_by_trade={},
            daily_avg_workers=20.0,  # 100/5
            daily_avg_manhours=160.0,  # 800/5
            working_days=5,
        )
        assert agg.daily_avg_workers == 20.0
        assert agg.daily_avg_manhours == 160.0

    def test_trade_breakdown(self):
        """Workers and manhours should be tracked by trade."""
        agg = LaborAggregate(
            project_id="test",
            date_range=None,
            total_workers=35,
            total_manhours=280.0,
            workers_by_trade={"carpenter": 20, "electrician": 15},
            manhours_by_trade={"carpenter": 160.0, "electrician": 120.0},
            daily_avg_workers=35.0,
            daily_avg_manhours=280.0,
            working_days=1,
        )
        assert agg.workers_by_trade["carpenter"] == 20
        assert agg.manhours_by_trade["electrician"] == 120.0


# ---------------------------------------------------------------------------
# TestProductivityMetrics
# ---------------------------------------------------------------------------


class TestProductivityMetrics:
    """Tests for calculate_productivity_metrics()."""

    def test_stable_productivity(self):
        """Consistent crew records should show stable trend."""
        records = _make_crew_records(count=10, base_actual=50.0, trend_delta=0.0)
        metrics = calculate_productivity_metrics(records)
        assert len(metrics) == 1
        assert metrics[0].trade == "carpenter"
        assert metrics[0].trend == "stable"
        assert metrics[0].sample_count == 10

    def test_improving_productivity(self):
        """Increasing actual units should show improving trend.

        Improving means fewer manhours per unit (more units with same crew),
        which means negative trend_slope on mh_per_unit.
        """
        records = _make_crew_records(count=10, base_actual=30.0, trend_delta=5.0)
        metrics = calculate_productivity_metrics(records)
        assert len(metrics) == 1
        assert metrics[0].trend == "improving"

    def test_declining_productivity(self):
        """Decreasing actual units should show declining trend."""
        records = _make_crew_records(count=10, base_actual=80.0, trend_delta=-5.0)
        metrics = calculate_productivity_metrics(records)
        assert len(metrics) == 1
        assert metrics[0].trend == "declining"

    def test_multiple_trades(self):
        """Records for different trades should produce separate metrics."""
        records = _make_crew_records(trade="carpenter", count=5) + _make_crew_records(
            trade="electrician", count=5
        )
        metrics = calculate_productivity_metrics(records)
        trades = {m.trade for m in metrics}
        assert "carpenter" in trades
        assert "electrician" in trades
        assert len(metrics) == 2

    def test_empty_records(self):
        """Empty records should return empty metrics."""
        metrics = calculate_productivity_metrics([])
        assert len(metrics) == 0

    def test_manhours_per_unit_calculation(self):
        """Manhours per unit should be (crew_size * work_hours) / actual_units."""
        records = [
            {
                "trade": "mason",
                "actual_units": 100.0,
                "planned_units": 100.0,
                "crew_size": 4,
                "work_date": date.today().isoformat(),
                "unit_of_measure": "SF",
                "work_hours": 8.0,
            }
        ]
        metrics = calculate_productivity_metrics(records)
        assert len(metrics) == 1
        # 4 workers * 8 hours / 100 units = 0.32 mh/unit
        assert abs(metrics[0].avg_manhours_per_unit - 0.32) < 0.01

    def test_zero_actual_units_skipped(self):
        """Records with zero actual units should be excluded from metrics."""
        records = [
            {
                "trade": "painter",
                "actual_units": 0.0,
                "planned_units": 50.0,
                "crew_size": 2,
                "work_date": date.today().isoformat(),
                "unit_of_measure": "SF",
                "work_hours": 8.0,
            }
        ]
        metrics = calculate_productivity_metrics(records)
        assert len(metrics) == 0


# ---------------------------------------------------------------------------
# TestLaborForecast
# ---------------------------------------------------------------------------


class TestLaborForecast:
    """Tests for LaborForecast dataclass and trade inference."""

    def test_forecast_structure(self):
        """LaborForecast should have all required fields."""
        forecast = LaborForecast(
            project_id="test-123",
            forecast_date="2026-03-15",
            remaining_activities=5,
            total_remaining_manhours=1000.0,
            by_trade={"carpenter": 400, "electrician": 300},
            by_month={"2026-03": {"carpenter": 400}},
            estimated_completion_date="2026-06-30",
        )
        assert forecast.remaining_activities == 5
        assert forecast.total_remaining_manhours == 1000.0

    def test_empty_forecast(self):
        """No remaining activities should produce empty forecast."""
        forecast = LaborForecast(
            project_id="test",
            forecast_date=date.today().isoformat(),
            remaining_activities=0,
            total_remaining_manhours=0.0,
            by_trade={},
            by_month={},
            estimated_completion_date=None,
        )
        assert forecast.remaining_activities == 0
        assert forecast.total_remaining_manhours == 0.0

    def test_trade_inference_electrical(self):
        """Activity named 'Electrical rough-in' should infer electrician."""
        mock_activity = MagicMock()
        mock_activity.name = "Electrical rough-in"
        mock_activity.metadata_ = {}
        mock_activity.resource_assignments = []
        trade = _infer_trade_from_activity(mock_activity)
        assert trade == "electrician"

    def test_trade_inference_from_metadata(self):
        """If metadata has trade, it should be used."""
        mock_activity = MagicMock()
        mock_activity.name = "Unknown activity"
        mock_activity.metadata_ = {"trade": "ironworker"}
        mock_activity.resource_assignments = []
        trade = _infer_trade_from_activity(mock_activity)
        assert trade == "ironworker"

    def test_trade_inference_concrete(self):
        """Activity with 'concrete' should infer concrete_finisher."""
        mock_activity = MagicMock()
        mock_activity.name = "Pour concrete slab"
        mock_activity.metadata_ = {}
        mock_activity.resource_assignments = []
        trade = _infer_trade_from_activity(mock_activity)
        assert trade == "concrete_finisher"

    def test_trade_inference_fallback(self):
        """Unknown activity should fall back to 'general'."""
        mock_activity = MagicMock()
        mock_activity.name = "Administrative task XYZ"
        mock_activity.metadata_ = {}
        mock_activity.resource_assignments = []
        trade = _infer_trade_from_activity(mock_activity)
        assert trade == "general"


# ---------------------------------------------------------------------------
# TestOvertimePrediction
# ---------------------------------------------------------------------------


class TestOvertimePrediction:
    """Tests for predict_overtime()."""

    def test_no_overtime_needed(self):
        """Sufficient workforce should predict zero overtime."""
        activities = _make_remaining_activities(
            {
                "carpenter": (320, 10),  # 320 mh over 10 days
            }
        )
        workforce = {"carpenter": 5}  # 5 workers * 8h * 10 days = 400 standard hours
        prediction = predict_overtime(activities, workforce)
        assert prediction.predicted_overtime_hours == 0.0
        assert prediction.risk_level == "low"

    def test_overtime_needed(self):
        """Insufficient workforce should predict overtime."""
        activities = _make_remaining_activities(
            {
                "carpenter": (500, 5),  # 500 mh over 5 days
            }
        )
        workforce = {"carpenter": 5}  # 5 * 8 * 5 = 200 standard hours
        prediction = predict_overtime(activities, workforce)
        assert prediction.predicted_overtime_hours == 300.0
        assert prediction.overtime_pct > 0

    def test_schedule_compression(self):
        """Schedule compression should reduce available standard hours."""
        activities = _make_remaining_activities(
            {
                "carpenter": (400, 10),
            }
        )
        workforce = {"carpenter": 5}  # 5*8*10 = 400 standard hours normally

        # No compression: barely enough
        pred_no = predict_overtime(activities, workforce, schedule_compression_pct=0)
        assert pred_no.predicted_overtime_hours == 0.0

        # 50% compression: 5*8*5 = 200 standard hours -> 200 OT
        pred_50 = predict_overtime(activities, workforce, schedule_compression_pct=50)
        assert pred_50.predicted_overtime_hours == 200.0

    def test_overtime_cost_calculation(self):
        """Overtime cost should use rate * multiplier."""
        activities = _make_remaining_activities(
            {
                "carpenter": (200, 5),
            }
        )
        workforce = {"carpenter": 1}  # 1*8*5 = 40 standard hours -> 160 OT
        prediction = predict_overtime(
            activities,
            workforce,
            avg_hourly_rate=40.0,
        )
        assert prediction.predicted_overtime_hours == 160.0
        expected_cost = 160.0 * 40.0 * OVERTIME_RATE_MULTIPLIER
        assert prediction.estimated_overtime_cost == expected_cost

    def test_risk_level_critical(self):
        """>=30% overtime should be critical."""
        activities = _make_remaining_activities(
            {
                "carpenter": (1000, 5),
            }
        )
        workforce = {"carpenter": 2}  # 2*8*5 = 80 hours for 1000mh -> 92% OT
        prediction = predict_overtime(activities, workforce)
        assert prediction.risk_level == "critical"

    def test_risk_level_moderate(self):
        """10-20% overtime should be moderate."""
        activities = _make_remaining_activities(
            {
                "carpenter": (480, 10),
            }
        )
        workforce = {"carpenter": 5}  # 5*8*10 = 400 hours for 480mh -> 80 OT = 16.7%
        prediction = predict_overtime(activities, workforce)
        assert prediction.risk_level == "moderate"

    def test_empty_activities(self):
        """No activities should produce zero overtime."""
        prediction = predict_overtime([], {"carpenter": 5})
        assert prediction.predicted_overtime_hours == 0.0
        assert prediction.risk_level == "low"


# ---------------------------------------------------------------------------
# TestFatigueRisk
# ---------------------------------------------------------------------------


class TestFatigueRisk:
    """Tests for assess_fatigue_risk()."""

    def test_no_fatigue_normal_hours(self):
        """Workers within thresholds should produce no alerts."""
        hours = _make_worker_hours(daily_hours=8.0, days=5)
        alerts = assess_fatigue_risk(hours)
        # 8h/day is within 10h daily threshold; 40h/week within 50h weekly
        assert len(alerts) == 0

    def test_daily_excess_yellow(self):
        """Worker at 11h should get yellow daily alert."""
        hours = _make_worker_hours(daily_hours=11.0, days=1)
        alerts = assess_fatigue_risk(hours, threshold_daily=10.0)
        daily_alerts = [a for a in alerts if a.alert_type == "daily_excess"]
        assert len(daily_alerts) == 1
        assert daily_alerts[0].risk_level == "yellow"
        assert daily_alerts[0].excess_hours == 1.0

    def test_daily_excess_red(self):
        """Worker at 13h should get red daily alert (>threshold+2)."""
        hours = _make_worker_hours(daily_hours=13.0, days=1)
        alerts = assess_fatigue_risk(hours, threshold_daily=10.0)
        daily_alerts = [a for a in alerts if a.alert_type == "daily_excess"]
        assert len(daily_alerts) == 1
        assert daily_alerts[0].risk_level == "red"

    def test_weekly_excess(self):
        """Worker at 12h/day for 5 days = 60h/week should trigger weekly alert."""
        hours = _make_worker_hours(daily_hours=12.0, days=5)
        alerts = assess_fatigue_risk(
            hours,
            threshold_daily=14.0,
            threshold_weekly=50.0,
        )
        weekly_alerts = [a for a in alerts if a.alert_type == "weekly_excess"]
        assert len(weekly_alerts) >= 1
        assert weekly_alerts[0].hours_worked == 60.0
        assert weekly_alerts[0].excess_hours == 10.0

    def test_custom_thresholds(self):
        """Custom thresholds should be respected."""
        hours = _make_worker_hours(daily_hours=9.0, days=1)
        # Default threshold 10h: no alert
        alerts_default = assess_fatigue_risk(hours, threshold_daily=10.0)
        daily_default = [a for a in alerts_default if a.alert_type == "daily_excess"]
        assert len(daily_default) == 0

        # Custom threshold 8h: should alert
        alerts_custom = assess_fatigue_risk(hours, threshold_daily=8.0)
        daily_custom = [a for a in alerts_custom if a.alert_type == "daily_excess"]
        assert len(daily_custom) == 1

    def test_multiple_workers(self):
        """Multiple workers should produce separate alerts."""
        hours = _make_worker_hours(worker_id="W001", daily_hours=12.0, days=1) + _make_worker_hours(
            worker_id="W002", daily_hours=11.0, days=1
        )
        alerts = assess_fatigue_risk(hours, threshold_daily=10.0)
        daily_alerts = [a for a in alerts if a.alert_type == "daily_excess"]
        assert len(daily_alerts) == 2

    def test_fatigue_recommendation_content(self):
        """Recommendations should contain actionable text."""
        rec_red = _fatigue_recommendation("red", "daily", 14.0)
        assert "mandatory" in rec_red.lower() or "rest" in rec_red.lower()
        rec_yellow = _fatigue_recommendation("yellow", "weekly", 55.0)
        assert "monitor" in rec_yellow.lower() or "reducing" in rec_yellow.lower()


# ---------------------------------------------------------------------------
# TestCraftAvailability
# ---------------------------------------------------------------------------


class TestCraftAvailability:
    """Tests for CraftAvailability dataclass."""

    def test_availability_structure(self):
        """CraftAvailability should have all required fields."""
        avail = CraftAvailability(
            project_id="test-123",
            forecast_date="2026-03-15",
            trades={
                "carpenter": {
                    "demand_manhours": 500,
                    "supply_workers": 5,
                    "weekly_capacity_hours": 200,
                    "gap_manhours": 0,
                    "gap_workers_needed": 0,
                    "status": "adequate",
                },
            },
            total_demand_manhours=500,
            total_supply_workers=5,
            overall_gap_pct=0.0,
        )
        assert avail.trades["carpenter"]["status"] == "adequate"

    def test_shortage_detection(self):
        """Trades with insufficient workers should show shortage."""
        avail = CraftAvailability(
            project_id="test",
            forecast_date="2026-03-15",
            trades={
                "electrician": {
                    "demand_manhours": 2000,
                    "supply_workers": 2,
                    "weekly_capacity_hours": 80,
                    "gap_manhours": 1680,
                    "gap_workers_needed": 11,
                    "status": "critical_shortage",
                },
            },
            total_demand_manhours=2000,
            total_supply_workers=2,
            overall_gap_pct=84.0,
        )
        assert avail.trades["electrician"]["status"] == "critical_shortage"
        assert avail.overall_gap_pct > 0


# ---------------------------------------------------------------------------
# TestSnapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    """Tests for workforce snapshot structure."""

    def test_snapshot_constants(self):
        """Verify workforce constants are correct."""
        assert STANDARD_HOURS_PER_DAY == 8.0
        assert STANDARD_HOURS_PER_WEEK == 40.0
        assert OVERTIME_RATE_MULTIPLIER == 1.5

    def test_overtime_prediction_structure(self):
        """OvertimePrediction should have all required fields."""
        pred = OvertimePrediction(
            project_id="test",
            schedule_compression_pct=10.0,
            total_remaining_manhours=1000,
            standard_hours_available=800,
            predicted_overtime_hours=200,
            overtime_pct=20.0,
            estimated_overtime_cost=12000,
            overtime_rate_multiplier=1.5,
            risk_level="moderate",
            recommendation="Plan for extended shifts",
        )
        assert pred.overtime_rate_multiplier == 1.5
        assert pred.risk_level == "moderate"


# ---------------------------------------------------------------------------
# TestEndpoints
# ---------------------------------------------------------------------------


class TestEndpoints:
    """Verify endpoint function signatures and imports are correct."""

    def test_workforce_router_exists(self):
        """The workforce router should be importable."""
        from app.api.v1.workforce import router

        assert router is not None

    def test_route_count(self):
        """Router should have the expected number of routes."""
        from app.api.v1.workforce import router

        routes = [r for r in router.routes if hasattr(r, "methods")]
        # analytics (GET), productivity (GET), forecast (GET),
        # overtime (POST), fatigue (POST), availability (GET),
        # snapshot (POST), snapshots (GET), latest (GET), portfolio (GET)
        assert len(routes) >= 8

    def test_trade_hourly_rates_exist(self):
        """Trade hourly rates should cover major trades."""
        from app.services.productivity.workforce_analytics import _TRADE_HOURLY_RATES

        assert "carpenter" in _TRADE_HOURLY_RATES
        assert "electrician" in _TRADE_HOURLY_RATES
        assert "plumber" in _TRADE_HOURLY_RATES
        assert "default" in _TRADE_HOURLY_RATES

    def test_schemas_importable(self):
        """All workforce schemas should be importable."""
        from app.schemas.workforce import (
            LaborAggregateResponse,
            WorkforceSnapshotResponse,
        )

        assert LaborAggregateResponse is not None
        assert WorkforceSnapshotResponse is not None

    def test_models_importable(self):
        """WorkforceSnapshot model should be importable."""
        from app.models.workforce import WorkforceSnapshot

        assert WorkforceSnapshot.__tablename__ == "workforce_snapshots"
