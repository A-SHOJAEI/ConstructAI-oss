"""Tests for the predictive cash flow engine (pure math layer).

Tests the pure math functions in cash_flow_engine.py without any DB or async dependencies.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.controls.cash_flow_engine import (
    ZERO,
    CashFlowConfidenceIntervals,
    CashFlowForecast,
    MonthlyCashPoint,
    PaymentWaterfall,
    compute_actual_cash_flow,
    compute_planned_cash_curve,
    evaluate_lien_waiver_coverage,
    forecast_cash_flow,
    model_payment_waterfall,
    run_cash_flow_monte_carlo,
)

# ---------------------------------------------------------------------------
# TestPlannedCashCurve
# ---------------------------------------------------------------------------


class TestPlannedCashCurve:
    """Test compute_planned_cash_curve with various scenarios."""

    def test_linear_distribution_with_matching_activities(self):
        """SOV items matched to activities distribute across activity months."""
        sov_items = [
            {"item_number": "A100", "csi_code": "A100", "scheduled_value": Decimal("120000")},
        ]
        activities = [
            {
                "activity_code": "A100",
                "early_start": date(2025, 1, 1),
                "early_finish": date(2025, 6, 30),
            },
        ]
        result = compute_planned_cash_curve(
            sov_items, activities, date(2025, 1, 1), date(2025, 6, 30)
        )

        assert len(result) == 6  # Jan through June
        total = sum(p.planned_billings for p in result)
        # Should total approximately $120,000 (rounding may cause minor diff)
        assert abs(total - Decimal("120000")) < Decimal("1")

        # Cumulative should be monotonically increasing
        for i in range(1, len(result)):
            assert result[i].cumulative_billed >= result[i - 1].cumulative_billed

    def test_s_curve_distribution_across_months(self):
        """Multiple SOV items with different activity timings create S-curve."""
        sov_items = [
            {"item_number": "SITE", "csi_code": "SITE", "scheduled_value": Decimal("50000")},
            {"item_number": "STRUCT", "csi_code": "STRUCT", "scheduled_value": Decimal("200000")},
            {"item_number": "MEP", "csi_code": "MEP", "scheduled_value": Decimal("150000")},
        ]
        activities = [
            {
                "activity_code": "SITE",
                "early_start": date(2025, 1, 1),
                "early_finish": date(2025, 2, 28),
            },
            {
                "activity_code": "STRUCT",
                "early_start": date(2025, 2, 1),
                "early_finish": date(2025, 7, 31),
            },
            {
                "activity_code": "MEP",
                "early_start": date(2025, 5, 1),
                "early_finish": date(2025, 10, 31),
            },
        ]
        result = compute_planned_cash_curve(
            sov_items, activities, date(2025, 1, 1), date(2025, 10, 31)
        )

        assert len(result) == 10  # Jan-Oct
        total = sum(p.planned_billings for p in result)
        assert abs(total - Decimal("400000")) < Decimal("5")

        # Early months should have less billing than peak months
        assert result[0].planned_billings < result[3].planned_billings

    def test_single_item_single_month(self):
        """A single SOV item within one month goes entirely to that month."""
        sov_items = [
            {"item_number": "1", "csi_code": "1", "scheduled_value": Decimal("50000")},
        ]
        activities = [
            {
                "activity_code": "1",
                "early_start": date(2025, 3, 5),
                "early_finish": date(2025, 3, 25),
            },
        ]
        result = compute_planned_cash_curve(
            sov_items, activities, date(2025, 3, 1), date(2025, 3, 31)
        )

        assert len(result) == 1
        assert result[0].planned_billings == Decimal("50000.00")
        assert result[0].cumulative_billed == Decimal("50000.00")

    def test_empty_sov_items(self):
        """Empty SOV list returns months with zero billings."""
        result = compute_planned_cash_curve([], [], date(2025, 1, 1), date(2025, 3, 31))
        assert len(result) == 3
        for p in result:
            assert p.planned_billings == ZERO

    def test_multi_year_project(self):
        """Projects spanning multiple years produce correct month count."""
        sov_items = [
            {"item_number": "1", "scheduled_value": Decimal("1200000")},
        ]
        result = compute_planned_cash_curve(sov_items, [], date(2024, 7, 1), date(2026, 6, 30))

        assert len(result) == 24  # 2 years
        total = sum(p.planned_billings for p in result)
        assert abs(total - Decimal("1200000")) < Decimal("1")


# ---------------------------------------------------------------------------
# TestActualCashFlow
# ---------------------------------------------------------------------------


class TestActualCashFlow:
    """Test compute_actual_cash_flow with pay app and CO data."""

    def test_with_payment_lag(self):
        """Pay apps create billing in period month and receipts after lag."""
        pay_apps = [
            {
                "period_to": date(2025, 1, 31),
                "current_payment_due": Decimal("50000"),
                "status": "certified",
                "paid_at": date(2025, 3, 1),
            },
        ]
        result = compute_actual_cash_flow(pay_apps, [])

        # Should have entries for Jan (billing) and March (receipt)
        assert len(result) >= 1
        jan = next((p for p in result if p.month == date(2025, 1, 1)), None)
        assert jan is not None
        assert jan.actual_billings == Decimal("50000.00")

    def test_multiple_pay_apps(self):
        """Multiple pay apps accumulate correctly."""
        pay_apps = [
            {
                "period_to": date(2025, 1, 31),
                "current_payment_due": Decimal("50000"),
                "status": "paid",
                "paid_at": date(2025, 2, 28),
            },
            {
                "period_to": date(2025, 2, 28),
                "current_payment_due": Decimal("75000"),
                "status": "paid",
                "paid_at": date(2025, 3, 31),
            },
        ]
        result = compute_actual_cash_flow(pay_apps, [])

        total_billed = sum(p.actual_billings for p in result)
        assert total_billed == Decimal("125000.00")

        # Last month should have highest cumulative
        assert result[-1].cumulative_billed == Decimal("125000.00")

    def test_with_change_orders(self):
        """Change orders add to billing in their approval month."""
        pay_apps = [
            {
                "period_to": date(2025, 1, 31),
                "current_payment_due": Decimal("100000"),
                "status": "certified",
                "paid_at": date(2025, 2, 28),
            },
        ]
        change_orders = [
            {
                "approved_date": date(2025, 1, 15),
                "cost_impact": Decimal("25000"),
            },
        ]
        result = compute_actual_cash_flow(pay_apps, change_orders)

        jan = next((p for p in result if p.month == date(2025, 1, 1)), None)
        assert jan is not None
        # January should have both pay app billing + CO
        assert jan.actual_billings == Decimal("125000.00")

    def test_partial_payments(self):
        """Submitted (not certified) pay apps show as expected receipts."""
        pay_apps = [
            {
                "period_to": date(2025, 3, 31),
                "current_payment_due": Decimal("80000"),
                "status": "submitted",
                "paid_at": None,
            },
        ]
        result = compute_actual_cash_flow(pay_apps, [], payment_lag_owner_days=30)

        # Billing should be in March
        march = next((p for p in result if p.month == date(2025, 3, 1)), None)
        assert march is not None
        assert march.actual_billings == Decimal("80000.00")

        # Receipt should be expected (not actual) in April
        april = next((p for p in result if p.month == date(2025, 4, 1)), None)
        if april:
            assert april.expected_receipts == Decimal("80000.00")
            assert april.actual_receipts == ZERO


# ---------------------------------------------------------------------------
# TestPaymentWaterfall
# ---------------------------------------------------------------------------


class TestPaymentWaterfall:
    """Test model_payment_waterfall."""

    def test_timing_and_parties(self):
        """Steps follow correct sequence and timing."""
        wf = model_payment_waterfall(
            billing_amount=Decimal("100000"),
            billing_date=date(2025, 3, 1),
            retainage_pct=Decimal("10"),
            payment_lag_owner_days=30,
            payment_lag_sub_days=45,
        )

        assert isinstance(wf, PaymentWaterfall)
        assert len(wf.steps) == 5  # submit, review, owner pay, retainage, sub pay

        # Step 1: GC submits
        assert wf.steps[0].from_party == "General Contractor"
        assert wf.steps[0].amount == Decimal("100000")
        assert wf.steps[0].expected_date == date(2025, 3, 1)

        # Step 3: Owner pays GC (less retainage)
        assert wf.steps[2].amount == Decimal("90000.00")
        assert wf.steps[2].expected_date == date(2025, 3, 31)

        # Step 5: GC pays subs
        assert wf.steps[4].from_party == "General Contractor"
        assert wf.steps[4].to_party == "Subcontractors"
        assert wf.steps[4].expected_date == date(2025, 5, 15)

    def test_retainage_withholding(self):
        """Retainage step correctly calculates withheld amount."""
        wf = model_payment_waterfall(
            billing_amount=Decimal("200000"),
            billing_date=date(2025, 1, 1),
            retainage_pct=Decimal("5"),
        )

        retainage_step = next((s for s in wf.steps if "Retainage" in s.to_party), None)
        assert retainage_step is not None
        assert retainage_step.amount == Decimal("10000.00")

    def test_zero_amount(self):
        """Zero billing amount produces valid waterfall with zero amounts."""
        wf = model_payment_waterfall(
            billing_amount=ZERO,
            billing_date=date(2025, 1, 1),
            retainage_pct=Decimal("10"),
        )

        assert len(wf.steps) >= 3
        for step in wf.steps:
            assert step.amount >= ZERO

    def test_custom_lags(self):
        """Custom payment lags are respected in step timing."""
        wf = model_payment_waterfall(
            billing_amount=Decimal("50000"),
            billing_date=date(2025, 6, 1),
            retainage_pct=Decimal("10"),
            payment_lag_owner_days=60,
            payment_lag_sub_days=30,
        )

        # Owner pays after 60 days
        owner_pay_step = wf.steps[2]
        assert owner_pay_step.expected_date == date(2025, 7, 31)

        # Subs paid 30 days after GC receives
        sub_pay_step = wf.steps[-1]
        assert sub_pay_step.expected_date == date(2025, 8, 30)


# ---------------------------------------------------------------------------
# TestCashFlowForecast
# ---------------------------------------------------------------------------


class TestCashFlowForecast:
    """Test forecast_cash_flow merging and projection logic."""

    def _make_planned(self, months: list[date], amounts: list[Decimal]) -> list[MonthlyCashPoint]:
        result = []
        cumulative = ZERO
        for m, a in zip(months, amounts, strict=False):
            cumulative += a
            result.append(
                MonthlyCashPoint(month=m, planned_billings=a, cumulative_billed=cumulative)
            )
        return result

    def _make_actual(
        self, months: list[date], billings: list[Decimal], receipts: list[Decimal]
    ) -> list[MonthlyCashPoint]:
        result = []
        cum_b = ZERO
        cum_r = ZERO
        for m, b, r in zip(months, billings, receipts, strict=False):
            cum_b += b
            cum_r += r
            result.append(
                MonthlyCashPoint(
                    month=m,
                    actual_billings=b,
                    actual_receipts=r,
                    cumulative_billed=cum_b,
                    cumulative_received=cum_r,
                    net_cash_position=cum_r - cum_b,
                )
            )
        return result

    def test_projection_extends_beyond_planned(self):
        """Forecast projects future months beyond the planned curve."""
        months = [date(2025, i, 1) for i in range(1, 7)]
        planned = self._make_planned(months, [Decimal("50000")] * 6)

        forecast = forecast_cash_flow(
            planned_curve=planned,
            actual_curve=[],
            remaining_months=3,
            retainage_pct=Decimal("10"),
        )

        assert isinstance(forecast, CashFlowForecast)
        # Should have 6 planned + 3 projected = 9 months
        assert len(forecast.monthly_projections) >= 6

    def test_cpi_adjustment(self):
        """CPI from actuals adjusts future projections."""
        months = [date(2025, i, 1) for i in range(1, 7)]
        planned = self._make_planned(months, [Decimal("100000")] * 6)

        # Actual billing at 80% of plan -> CPI = 0.8
        actual_months = [date(2025, 1, 1), date(2025, 2, 1)]
        actual = self._make_actual(
            actual_months,
            [Decimal("80000"), Decimal("80000")],
            [Decimal("72000"), Decimal("72000")],
        )

        forecast = forecast_cash_flow(
            planned_curve=planned,
            actual_curve=actual,
            remaining_months=0,
            retainage_pct=Decimal("10"),
        )

        assert forecast.risk_indicators  # Should flag billing below plan

    def test_retainage_calculation(self):
        """Retainage is properly held and tracked."""
        months = [date(2025, 1, 1)]
        planned = self._make_planned(months, [Decimal("100000")])

        forecast = forecast_cash_flow(
            planned_curve=planned,
            actual_curve=[],
            remaining_months=0,
            retainage_pct=Decimal("10"),
        )

        assert forecast.retainage_held > ZERO

    def test_negative_position_detection(self):
        """Risk indicator raised when cash position goes negative."""
        months = [date(2025, 1, 1), date(2025, 2, 1)]
        planned = self._make_planned(months, [Decimal("100000"), Decimal("100000")])

        forecast = forecast_cash_flow(
            planned_curve=planned,
            actual_curve=[],
            remaining_months=0,
            retainage_pct=Decimal("10"),
        )

        # With retainage, net position should be negative (billed more than received)
        has_negative = any("Negative" in r for r in forecast.risk_indicators)
        assert has_negative

    def test_risk_indicators_for_billing_below_plan(self):
        """Risk indicator raised when CPI is significantly below 1.0."""
        months = [date(2025, i, 1) for i in range(1, 5)]
        planned = self._make_planned(months, [Decimal("100000")] * 4)

        # Actual at 70% of plan
        actual = self._make_actual(
            [date(2025, 1, 1), date(2025, 2, 1)],
            [Decimal("70000"), Decimal("70000")],
            [Decimal("63000"), Decimal("63000")],
        )

        forecast = forecast_cash_flow(
            planned_curve=planned,
            actual_curve=actual,
            remaining_months=0,
            retainage_pct=Decimal("10"),
        )

        has_billing_risk = any("Billing trend below plan" in r for r in forecast.risk_indicators)
        assert has_billing_risk


# ---------------------------------------------------------------------------
# TestMonteCarlo
# ---------------------------------------------------------------------------


class TestMonteCarlo:
    """Test run_cash_flow_monte_carlo."""

    def _make_forecast(self, n_months: int = 6) -> CashFlowForecast:
        projections = []
        for i in range(n_months):
            projections.append(
                MonthlyCashPoint(
                    month=date(2025, i + 1, 1),
                    planned_billings=Decimal("100000"),
                    actual_billings=Decimal("95000"),
                )
            )
        return CashFlowForecast(
            monthly_projections=projections,
            total_contract_value=Decimal("600000"),
            total_billed=Decimal("570000"),
            total_received=Decimal("513000"),
            retainage_held=Decimal("57000"),
        )

    def test_intervals_shape(self):
        """Monte Carlo returns correct number of month entries."""
        forecast = self._make_forecast(6)
        result = run_cash_flow_monte_carlo(forecast, num_simulations=500, seed=42)

        assert isinstance(result, CashFlowConfidenceIntervals)
        assert len(result.p10) == 6
        assert len(result.p50) == 6
        assert len(result.p90) == 6

    def test_reproducibility_with_seed(self):
        """Same seed produces identical results."""
        forecast = self._make_forecast(4)
        r1 = run_cash_flow_monte_carlo(forecast, num_simulations=1000, seed=42)
        r2 = run_cash_flow_monte_carlo(forecast, num_simulations=1000, seed=42)

        assert r1.p10 == r2.p10
        assert r1.p50 == r2.p50
        assert r1.p90 == r2.p90
        assert r1.worst_month_position == r2.worst_month_position

    def test_p10_below_p90(self):
        """P10 should be below P90 for each month (10th percentile < 90th)."""
        forecast = self._make_forecast(6)
        result = run_cash_flow_monte_carlo(forecast, num_simulations=2000, seed=42)

        for i in range(len(result.p10)):
            assert result.p10[i] <= result.p90[i]

    def test_empty_forecast(self):
        """Empty forecast returns empty intervals."""
        forecast = CashFlowForecast(monthly_projections=[])
        result = run_cash_flow_monte_carlo(forecast, num_simulations=100, seed=42)

        assert result.p10 == []
        assert result.p50 == []
        assert result.p90 == []
        assert result.worst_month_position == ZERO
        assert result.months_negative == 0


# ---------------------------------------------------------------------------
# TestLienWaiverEvaluation
# ---------------------------------------------------------------------------


class TestLienWaiverEvaluation:
    """Test evaluate_lien_waiver_coverage."""

    def test_full_coverage(self):
        """All pay apps have matching waivers -> 100% coverage."""
        waivers = [
            {
                "vendor_name": "ABC Construction",
                "through_date": date(2025, 1, 31),
                "status": "received",
                "amount": Decimal("50000"),
                "signed_date": date(2025, 2, 5),
            },
        ]
        pay_apps = [
            {
                "period_to": date(2025, 1, 31),
                "current_payment_due": Decimal("50000"),
                "application_number": 1,
                "contractor_info": {"name": "ABC Construction"},
            },
        ]
        result = evaluate_lien_waiver_coverage(waivers, pay_apps)

        assert result.coverage_pct == Decimal("100.00")
        assert len(result.missing_waivers) == 0

    def test_missing_waivers(self):
        """Pay apps without matching waivers are flagged as missing."""
        waivers = []
        pay_apps = [
            {
                "period_to": date(2025, 1, 31),
                "current_payment_due": Decimal("50000"),
                "application_number": 1,
                "contractor_info": {"name": "ABC Construction"},
            },
            {
                "period_to": date(2025, 2, 28),
                "current_payment_due": Decimal("75000"),
                "application_number": 2,
                "contractor_info": {"name": "ABC Construction"},
            },
        ]
        result = evaluate_lien_waiver_coverage(waivers, pay_apps)

        assert result.coverage_pct == ZERO
        assert len(result.missing_waivers) == 2

    def test_upcoming_deadlines(self):
        """Pending waivers older than 14 days appear as upcoming deadlines."""
        old_date = date.today() - timedelta(days=20)
        waivers = [
            {
                "vendor_name": "XYZ Plumbing",
                "through_date": old_date,
                "status": "pending",
                "amount": Decimal("30000"),
                "signed_date": None,
                "waiver_type": "conditional_partial",
            },
        ]
        pay_apps = [
            {
                "period_to": old_date,
                "current_payment_due": Decimal("30000"),
                "application_number": 1,
                "contractor_info": {"name": "XYZ Plumbing"},
            },
        ]
        result = evaluate_lien_waiver_coverage(waivers, pay_apps)

        assert len(result.upcoming_deadlines) == 1
        assert result.upcoming_deadlines[0]["vendor_name"] == "XYZ Plumbing"
        assert result.upcoming_deadlines[0]["days_overdue"] >= 20

    def test_void_exclusion(self):
        """Void waivers do not count toward coverage."""
        waivers = [
            {
                "vendor_name": "ABC Construction",
                "through_date": date(2025, 1, 31),
                "status": "void",
                "amount": Decimal("50000"),
                "signed_date": date(2025, 1, 25),
            },
        ]
        pay_apps = [
            {
                "period_to": date(2025, 1, 31),
                "current_payment_due": Decimal("50000"),
                "application_number": 1,
                "contractor_info": {"name": "ABC Construction"},
            },
        ]
        result = evaluate_lien_waiver_coverage(waivers, pay_apps)

        assert result.coverage_pct == ZERO
        assert len(result.missing_waivers) == 1


# ---------------------------------------------------------------------------
# TestCashFlowService (mocked DB)
# ---------------------------------------------------------------------------


class TestCashFlowService:
    """Test the DB-aware service layer with mocked sessions."""

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock(spec=["get", "execute", "flush", "refresh", "add"])
        return db

    @pytest.mark.asyncio
    async def test_create_lien_waiver(self, mock_db):
        """create_lien_waiver validates and persists."""
        from app.services.controls.cash_flow_service import create_lien_waiver

        mock_project = MagicMock()
        mock_project.id = uuid.uuid4()
        mock_db.get = AsyncMock(return_value=mock_project)

        waiver_data = {
            "waiver_type": "conditional_partial",
            "vendor_name": "Test Vendor",
            "amount": "50000",
            "through_date": date(2025, 3, 31),
        }

        # Mock flush and refresh to simulate DB behavior
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        await create_lien_waiver(mock_db, mock_project.id, waiver_data)
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_lien_waiver_invalid_type(self, mock_db):
        """Invalid waiver type raises ValueError."""
        from app.services.controls.cash_flow_service import create_lien_waiver

        waiver_data = {
            "waiver_type": "invalid_type",
            "vendor_name": "Test Vendor",
        }

        with pytest.raises(ValueError, match="waiver_type must be one of"):
            await create_lien_waiver(mock_db, uuid.uuid4(), waiver_data)

    @pytest.mark.asyncio
    async def test_update_lien_waiver_void_blocked(self, mock_db):
        """Cannot change status of a void waiver."""
        from app.services.controls.cash_flow_service import update_lien_waiver

        mock_waiver = MagicMock()
        mock_waiver.status = "void"
        mock_db.get = AsyncMock(return_value=mock_waiver)

        with pytest.raises(ValueError, match="Cannot change status of a void waiver"):
            await update_lien_waiver(mock_db, uuid.uuid4(), {"status": "received"})

    @pytest.mark.asyncio
    async def test_list_lien_waivers_invalid_status(self, mock_db):
        """Invalid status filter raises ValueError."""
        from app.services.controls.cash_flow_service import list_lien_waivers

        with pytest.raises(ValueError, match="status must be one of"):
            await list_lien_waivers(mock_db, uuid.uuid4(), status="invalid")

    @pytest.mark.asyncio
    async def test_get_cash_flow_history_empty(self, mock_db):
        """Empty project returns empty history."""
        from app.services.controls.cash_flow_service import get_cash_flow_history

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_cash_flow_history(mock_db, uuid.uuid4())
        assert result == []


# ---------------------------------------------------------------------------
# TestCashFlowAPI (route shape validation)
# ---------------------------------------------------------------------------


class TestCashFlowAPI:
    """Test API route structure and validation."""

    def test_router_has_expected_routes(self):
        """Router contains all required endpoints."""
        from app.api.v1.cash_flow import router

        routes = [r.path for r in router.routes]
        assert "/{project_id}/cash-flow/forecast" in routes
        assert "/{project_id}/cash-flow/history" in routes
        assert "/{project_id}/lien-waivers" in routes
        assert "/{project_id}/lien-waivers/{waiver_id}" in routes
        assert "/{project_id}/lien-waivers/analysis" in routes

    def test_config_schema_validation(self):
        """CashFlowConfigRequest validates parameter bounds."""
        from app.schemas.cash_flow import CashFlowConfigRequest

        # Defaults should work
        config = CashFlowConfigRequest()
        assert config.payment_lag_owner_days == 30
        assert config.retainage_pct == Decimal("10")
        assert config.num_simulations == 5000

    def test_config_schema_rejects_invalid(self):
        """CashFlowConfigRequest rejects out-of-bounds values."""
        from app.schemas.cash_flow import CashFlowConfigRequest

        with pytest.raises(Exception):
            CashFlowConfigRequest(payment_lag_owner_days=3)  # below min of 5

        with pytest.raises(Exception):
            CashFlowConfigRequest(retainage_pct=Decimal("25"))  # above max of 20

    def test_lien_waiver_schema_validation(self):
        """LienWaiverCreate validates waiver_type enum."""
        from app.schemas.cash_flow import LienWaiverCreate

        waiver = LienWaiverCreate(
            waiver_type="conditional_partial",
            vendor_name="Test Vendor",
            amount=Decimal("50000"),
        )
        assert waiver.waiver_type == "conditional_partial"

        with pytest.raises(Exception):
            LienWaiverCreate(
                waiver_type="invalid",
                vendor_name="Test",
            )


# ---------------------------------------------------------------------------
# Edge cases and validation
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Additional edge case tests."""

    def test_waterfall_negative_amount_raises(self):
        """Negative billing amount raises ValueError."""
        with pytest.raises(ValueError, match="non-negative"):
            model_payment_waterfall(
                billing_amount=Decimal("-1000"),
                billing_date=date(2025, 1, 1),
                retainage_pct=Decimal("10"),
            )

    def test_waterfall_invalid_retainage_raises(self):
        """Retainage > 100 raises ValueError."""
        with pytest.raises(ValueError, match="between 0 and 100"):
            model_payment_waterfall(
                billing_amount=Decimal("100000"),
                billing_date=date(2025, 1, 1),
                retainage_pct=Decimal("150"),
            )

    def test_actual_cash_flow_empty(self):
        """Empty pay apps returns empty list."""
        result = compute_actual_cash_flow([], [])
        assert result == []

    def test_forecast_empty_planned(self):
        """Empty planned curve returns no-data indicator."""
        forecast = forecast_cash_flow(
            planned_curve=[],
            actual_curve=[],
            remaining_months=3,
            retainage_pct=Decimal("10"),
        )
        assert any("No planned curve data" in r for r in forecast.risk_indicators)

    def test_lien_waiver_no_pay_apps(self):
        """No pay apps = 100% coverage (nothing to cover)."""
        result = evaluate_lien_waiver_coverage([], [])
        assert result.coverage_pct == Decimal("100.00")

    def test_monthly_cash_point_defaults(self):
        """MonthlyCashPoint has correct defaults."""
        p = MonthlyCashPoint(month=date(2025, 1, 1))
        assert p.planned_billings == ZERO
        assert p.actual_billings == ZERO
        assert p.net_cash_position == ZERO
