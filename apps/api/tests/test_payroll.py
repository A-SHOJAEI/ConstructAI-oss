"""Tests for certified payroll and prevailing wage compliance.

Covers:
- Gross pay calculation (straight, overtime, other hours)
- Fringe benefit calculation (per-category and default splits)
- Prevailing wage compliance checks ($0.01 tolerance)
- Federal/state tax withholding and FICA deductions
- WH-347 data generation and certification text
- Payroll record validation (rates, hours, duplicates)
- Period totals aggregation
- Service layer (DB-backed, mocked)
- API endpoints (mocked)
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.compliance.payroll_engine import (
    ZERO,
    WH347Report,
    _round2,
    calculate_fringe_benefits,
    calculate_gross_pay,
    calculate_payroll_deductions,
    calculate_period_totals,
    check_prevailing_wage_compliance,
    generate_wh347_data,
    validate_payroll_records,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(**overrides) -> dict:
    """Create a minimal payroll record dict."""
    base = {
        "worker_name": "John Smith",
        "worker_id": "1234",
        "trade": "carpenter",
        "classification": "journeyman",
        "pay_period_start": date(2026, 3, 1),
        "pay_period_end": date(2026, 3, 7),
        "hours_straight": Decimal("40"),
        "hours_overtime": Decimal("8"),
        "hours_other": Decimal("0"),
        "rate_straight": Decimal("45.00"),
        "rate_overtime": Decimal("67.50"),
        "gross_pay": Decimal("2340.00"),
        "deductions": {},
        "net_pay": Decimal("2340.00"),
        "fringe_benefits": {
            "health": "120.00",
            "pension": "90.00",
            "vacation": "45.00",
            "training": "45.00",
        },
    }
    base.update(overrides)
    return base


# ===========================================================================
# TestGrossPay
# ===========================================================================


class TestGrossPay:
    """Test gross pay calculation."""

    def test_straight_time_only(self):
        result = calculate_gross_pay(
            hours_straight=Decimal("40"),
            hours_overtime=Decimal("0"),
            hours_other=Decimal("0"),
            rate_straight=Decimal("25.00"),
        )
        assert result == Decimal("1000.00")

    def test_with_overtime(self):
        result = calculate_gross_pay(
            hours_straight=Decimal("40"),
            hours_overtime=Decimal("10"),
            hours_other=Decimal("0"),
            rate_straight=Decimal("30.00"),
        )
        # 40 * 30 + 10 * 45 = 1200 + 450 = 1650
        assert result == Decimal("1650.00")

    def test_custom_overtime_rate(self):
        result = calculate_gross_pay(
            hours_straight=Decimal("40"),
            hours_overtime=Decimal("8"),
            hours_other=Decimal("0"),
            rate_straight=Decimal("50.00"),
            rate_overtime=Decimal("100.00"),  # double time
        )
        # 40 * 50 + 8 * 100 = 2000 + 800 = 2800
        assert result == Decimal("2800.00")

    def test_with_other_hours(self):
        result = calculate_gross_pay(
            hours_straight=Decimal("40"),
            hours_overtime=Decimal("0"),
            hours_other=Decimal("4"),  # travel time
            rate_straight=Decimal("20.00"),
        )
        # 40 * 20 + 4 * 20 = 800 + 80 = 880
        assert result == Decimal("880.00")

    def test_zero_hours(self):
        result = calculate_gross_pay(
            hours_straight=Decimal("0"),
            hours_overtime=Decimal("0"),
            hours_other=Decimal("0"),
            rate_straight=Decimal("25.00"),
        )
        assert result == Decimal("0.00")


# ===========================================================================
# TestFringeBenefits
# ===========================================================================


class TestFringeBenefits:
    """Test fringe benefit calculation."""

    def test_default_splits(self):
        result = calculate_fringe_benefits(
            hours_total=Decimal("40"),
            fringe_rate=Decimal("10.00"),
        )
        assert result.total_fringe == Decimal("400.00")
        assert result.health == Decimal("160.00")  # 40%
        assert result.pension == Decimal("120.00")  # 30%
        assert result.vacation == Decimal("60.00")  # 15%
        assert result.training == Decimal("60.00")  # 15%

    def test_custom_breakdown(self):
        breakdown = {
            "health": Decimal("5.00"),
            "pension": Decimal("3.00"),
            "vacation": Decimal("1.50"),
            "training": Decimal("0.50"),
        }
        result = calculate_fringe_benefits(
            hours_total=Decimal("48"),
            fringe_rate=Decimal("10.00"),
            fringe_breakdown=breakdown,
        )
        assert result.total_fringe == Decimal("480.00")
        assert result.health == Decimal("240.00")  # 48 * 5
        assert result.pension == Decimal("144.00")  # 48 * 3

    def test_zero_hours(self):
        result = calculate_fringe_benefits(
            hours_total=Decimal("0"),
            fringe_rate=Decimal("10.00"),
        )
        assert result.total_fringe == Decimal("0.00")

    def test_zero_rate(self):
        result = calculate_fringe_benefits(
            hours_total=Decimal("40"),
            fringe_rate=Decimal("0"),
        )
        assert result.total_fringe == Decimal("0.00")


# ===========================================================================
# TestPrevailingWageCompliance
# ===========================================================================


class TestPrevailingWageCompliance:
    """Test prevailing wage compliance checks."""

    def test_compliant(self):
        record = _make_record(
            rate_straight=Decimal("45.00"),
            fringe_benefits={
                "health": "120.00",
                "pension": "90.00",
                "vacation": "45.00",
                "training": "45.00",
            },
            hours_straight=Decimal("40"),
            hours_overtime=Decimal("0"),
            hours_other=Decimal("0"),
        )
        prevailing = {"total_rate": Decimal("52.00")}
        result = check_prevailing_wage_compliance(record, prevailing)
        # actual = 45 + (120+90+45+45)/40 = 45 + 7.50 = 52.50
        assert result.status == "compliant"
        assert result.shortfall_per_hour == ZERO

    def test_underpayment(self):
        record = _make_record(
            rate_straight=Decimal("30.00"),
            fringe_benefits={"health": "40.00"},
            hours_straight=Decimal("40"),
            hours_overtime=Decimal("0"),
            hours_other=Decimal("0"),
        )
        prevailing = {"total_rate": Decimal("45.00")}
        result = check_prevailing_wage_compliance(record, prevailing)
        # actual = 30 + 40/40 = 31.00, required = 45.00
        assert result.status == "underpayment"
        assert result.shortfall_per_hour == Decimal("14.00")
        assert result.total_shortfall == Decimal("560.00")

    def test_exact_match(self):
        record = _make_record(
            rate_straight=Decimal("40.00"),
            fringe_benefits={"health": "200.00"},
            hours_straight=Decimal("40"),
            hours_overtime=Decimal("0"),
            hours_other=Decimal("0"),
        )
        prevailing = {"total_rate": Decimal("45.00")}
        result = check_prevailing_wage_compliance(record, prevailing)
        # actual = 40 + 200/40 = 40 + 5 = 45.00
        assert result.status == "compliant"

    def test_within_tolerance(self):
        """$0.01 shortfall is within tolerance."""
        record = _make_record(
            rate_straight=Decimal("44.99"),
            fringe_benefits={},
            hours_straight=Decimal("40"),
            hours_overtime=Decimal("0"),
            hours_other=Decimal("0"),
        )
        prevailing = {"total_rate": Decimal("45.00")}
        result = check_prevailing_wage_compliance(record, prevailing)
        assert result.status == "compliant"

    def test_zero_hours_compliant(self):
        record = _make_record(
            rate_straight=Decimal("50.00"),
            fringe_benefits={},
            hours_straight=Decimal("0"),
            hours_overtime=Decimal("0"),
            hours_other=Decimal("0"),
        )
        prevailing = {"total_rate": Decimal("45.00")}
        result = check_prevailing_wage_compliance(record, prevailing)
        # With zero hours, fringe_per_hour = 0, actual = 50 + 0 = 50
        assert result.status == "compliant"

    def test_overtime_hours_included(self):
        record = _make_record(
            rate_straight=Decimal("40.00"),
            fringe_benefits={"health": "480.00"},
            hours_straight=Decimal("40"),
            hours_overtime=Decimal("8"),
            hours_other=Decimal("0"),
        )
        prevailing = {"total_rate": Decimal("50.00")}
        result = check_prevailing_wage_compliance(record, prevailing)
        # total_hours = 48, fringe_per_hour = 480/48 = 10
        # actual = 40 + 10 = 50.00
        assert result.status == "compliant"


# ===========================================================================
# TestDeductions
# ===========================================================================


class TestDeductions:
    """Test payroll deduction calculations."""

    def test_basic_deductions(self):
        result = calculate_payroll_deductions(
            gross_pay=Decimal("2000.00"),
            deduction_rules={"state": "TX", "pay_periods_per_year": 26},
        )
        assert result.state_tax == ZERO  # TX has no state tax
        assert result.social_security > ZERO
        assert result.medicare > ZERO
        assert result.net_pay == result.net_pay  # sanity
        assert result.total_deductions == (
            result.federal_tax
            + result.state_tax
            + result.social_security
            + result.medicare
            + result.union_dues
            + result.garnishments
            + result.other
        )

    def test_state_tax(self):
        result = calculate_payroll_deductions(
            gross_pay=Decimal("1000.00"),
            deduction_rules={"state": "CA"},
        )
        # CA uses progressive brackets: $1000 biweekly (26 periods)
        # annualized = $26,000 -> brackets: 1% up to $10,099, 2% $10,100-$23,942,
        # 4% $23,943-$26,000 -> annual ~$460.11 -> per period ~$17.70
        assert result.state_tax == Decimal("17.70")

    def test_fica_calculations(self):
        result = calculate_payroll_deductions(
            gross_pay=Decimal("5000.00"),
            deduction_rules={"state": "FL"},
        )
        assert result.social_security == Decimal("310.00")  # 5000 * 0.062
        assert result.medicare == Decimal("72.50")  # 5000 * 0.0145

    def test_ss_wage_cap(self):
        """Social Security stops at wage cap."""
        result = calculate_payroll_deductions(
            gross_pay=Decimal("10000.00"),
            deduction_rules={
                "state": "TX",
                "ytd_gross": Decimal("170000"),
            },
        )
        # Only $4,900 is taxable (174900 - 170000)
        expected_ss = _round2(Decimal("4900") * Decimal("0.062"))
        assert result.social_security == expected_ss

    def test_union_dues_and_garnishments(self):
        result = calculate_payroll_deductions(
            gross_pay=Decimal("2000.00"),
            deduction_rules={
                "state": "TX",
                "union_dues": Decimal("50.00"),
                "garnishments": Decimal("100.00"),
            },
        )
        assert result.union_dues == Decimal("50.00")
        assert result.garnishments == Decimal("100.00")

    def test_net_pay_correct(self):
        result = calculate_payroll_deductions(
            gross_pay=Decimal("3000.00"),
            deduction_rules={"state": "TX"},
        )
        assert result.net_pay == _round2(Decimal("3000.00") - result.total_deductions)


# ===========================================================================
# TestWH347Generation
# ===========================================================================


class TestWH347Generation:
    """Test WH-347 data formatting."""

    def test_basic_generation(self):
        records = [
            _make_record(worker_name="Alice Brown"),
            _make_record(worker_name="Bob Jones", trade="electrician"),
        ]
        result = generate_wh347_data(
            contractor_info={"name": "ABC Construction", "address": "123 Main St"},
            project_info={"name": "Highway Bridge #42", "contract_number": "FA-2026-001"},
            payroll_records=records,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 7),
        )
        assert isinstance(result, WH347Report)
        assert result.contractor_name == "ABC Construction"
        assert len(result.workers) == 2
        assert result.total_gross > ZERO

    def test_worker_aggregation(self):
        """Same worker in multiple records gets aggregated."""
        records = [
            _make_record(
                worker_name="Alice Brown", hours_straight=Decimal("20"), gross_pay=Decimal("900.00")
            ),
            _make_record(
                worker_name="Alice Brown", hours_straight=Decimal("20"), gross_pay=Decimal("900.00")
            ),
        ]
        result = generate_wh347_data(
            contractor_info={"name": "Test"},
            project_info={"name": "Test Project"},
            payroll_records=records,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 7),
        )
        assert len(result.workers) == 1
        assert result.workers[0]["hours_straight"] == Decimal("40.00")
        assert result.workers[0]["gross_pay"] == Decimal("1800.00")

    def test_certification_text(self):
        records = [_make_record()]
        result = generate_wh347_data(
            contractor_info={
                "name": "Test Corp",
                "signer_name": "Jane Doe",
                "signer_title": "Payroll Manager",
            },
            project_info={"name": "Federal Building"},
            payroll_records=records,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 7),
        )
        assert "Jane Doe" in result.certification_text
        assert "Payroll Manager" in result.certification_text
        assert "Test Corp" in result.certification_text

    def test_totals_match_workers(self):
        records = [
            _make_record(worker_name="A", gross_pay=Decimal("1000.00"), net_pay=Decimal("800.00")),
            _make_record(worker_name="B", gross_pay=Decimal("2000.00"), net_pay=Decimal("1500.00")),
        ]
        result = generate_wh347_data(
            contractor_info={"name": "Test"},
            project_info={"name": "Test"},
            payroll_records=records,
            period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 7),
        )
        assert result.total_gross == Decimal("3000.00")
        assert result.total_net == Decimal("2300.00")


# ===========================================================================
# TestValidation
# ===========================================================================


class TestValidation:
    """Test payroll record validation."""

    def test_valid_record(self):
        records = [_make_record()]
        errors = validate_payroll_records(records, today=date(2026, 3, 15))
        assert len(errors) == 0

    def test_zero_hours(self):
        records = [
            _make_record(
                hours_straight=Decimal("0"), hours_overtime=Decimal("0"), hours_other=Decimal("0")
            )
        ]
        errors = validate_payroll_records(records)
        assert any(e.field == "hours" for e in errors)

    def test_below_minimum_wage(self):
        records = [
            _make_record(
                rate_straight=Decimal("5.00"),
                rate_overtime=Decimal("7.50"),
                gross_pay=Decimal("260.00"),
            )
        ]
        errors = validate_payroll_records(records)
        assert any("minimum wage" in e.message for e in errors)

    def test_overtime_rate_too_low(self):
        records = [
            _make_record(
                rate_straight=Decimal("30.00"),
                rate_overtime=Decimal("35.00"),
                gross_pay=Decimal("1480.00"),
            )
        ]
        errors = validate_payroll_records(records)
        assert any("Overtime rate" in e.message for e in errors)

    def test_duplicate_worker(self):
        records = [
            _make_record(worker_name="John Smith"),
            _make_record(worker_name="John Smith"),
        ]
        errors = validate_payroll_records(records)
        assert any("Duplicate" in e.message for e in errors)


# ===========================================================================
# TestPeriodTotals
# ===========================================================================


class TestPeriodTotals:
    """Test period totals aggregation."""

    def test_single_record(self):
        records = [_make_record(gross_pay=Decimal("2340.00"), net_pay=Decimal("1800.00"))]
        totals = calculate_period_totals(records)
        assert totals.record_count == 1
        assert totals.total_gross == Decimal("2340.00")
        assert totals.total_net == Decimal("1800.00")

    def test_multiple_records(self):
        records = [
            _make_record(
                worker_name="A",
                trade="carpenter",
                gross_pay=Decimal("1000.00"),
                net_pay=Decimal("800.00"),
            ),
            _make_record(
                worker_name="B",
                trade="electrician",
                gross_pay=Decimal("1500.00"),
                net_pay=Decimal("1200.00"),
            ),
        ]
        totals = calculate_period_totals(records)
        assert totals.record_count == 2
        assert totals.total_gross == Decimal("2500.00")
        assert "carpenter" in totals.by_trade
        assert "electrician" in totals.by_trade

    def test_by_trade_breakdown(self):
        records = [
            _make_record(trade="carpenter", gross_pay=Decimal("1000.00")),
            _make_record(worker_name="B", trade="carpenter", gross_pay=Decimal("1200.00")),
            _make_record(worker_name="C", trade="laborer", gross_pay=Decimal("800.00")),
        ]
        totals = calculate_period_totals(records)
        assert totals.by_trade["carpenter"]["count"] == 2
        assert totals.by_trade["laborer"]["count"] == 1


# ===========================================================================
# TestPayrollService (mocked DB)
# ===========================================================================


class TestPayrollService:
    """Test payroll service layer with mocked DB."""

    @pytest.mark.asyncio
    async def test_lookup_prevailing_wage_county(self):
        """County-level lookup returns county-specific rate."""
        from app.services.compliance.payroll_service import lookup_prevailing_wage

        mock_rate = MagicMock()
        mock_rate.id = uuid.uuid4()
        mock_rate.total_rate = Decimal("55.00")
        mock_rate.expiration_date = None

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_rate
        mock_db.execute.return_value = mock_result

        result = await lookup_prevailing_wage(
            mock_db, "TX", "Harris", "carpenter", date(2026, 3, 1)
        )
        assert result is not None
        assert result.total_rate == Decimal("55.00")

    @pytest.mark.asyncio
    async def test_lookup_prevailing_wage_fallback_to_state(self):
        """Falls back to state-level when county-specific not found."""
        from app.services.compliance.payroll_service import lookup_prevailing_wage

        mock_state_rate = MagicMock()
        mock_state_rate.total_rate = Decimal("50.00")
        mock_state_rate.expiration_date = None

        mock_db = AsyncMock()
        # First call (county): returns None
        # Second call (state fallback): returns rate
        county_result = MagicMock()
        county_result.scalars.return_value.first.return_value = None
        state_result = MagicMock()
        state_result.scalars.return_value.first.return_value = mock_state_rate
        mock_db.execute.side_effect = [county_result, state_result]

        result = await lookup_prevailing_wage(mock_db, "TX", "NonexistentCounty", "carpenter")
        assert result is not None
        assert result.total_rate == Decimal("50.00")

    @pytest.mark.asyncio
    async def test_compliance_summary_empty(self):
        """Empty project returns zero counts."""
        from app.services.compliance.payroll_service import get_compliance_summary

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        summary = await get_compliance_summary(mock_db, uuid.uuid4())
        assert summary.total_records == 0
        assert summary.compliance_rate == ZERO

    @pytest.mark.asyncio
    async def test_certify_report_not_found(self):
        """Certifying non-existent report raises ValueError."""
        from app.services.compliance.payroll_service import certify_report

        mock_db = AsyncMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError, match="Report not found"):
            await certify_report(mock_db, uuid.uuid4(), uuid.uuid4())

    @pytest.mark.asyncio
    async def test_certify_already_certified(self):
        """Certifying already-certified report raises ValueError."""
        from app.services.compliance.payroll_service import certify_report

        mock_report = MagicMock()
        mock_report.status = "certified"

        mock_db = AsyncMock()
        mock_db.get.return_value = mock_report

        with pytest.raises(ValueError, match="already certified"):
            await certify_report(mock_db, uuid.uuid4(), uuid.uuid4())


# ===========================================================================
# TestComplianceSummary
# ===========================================================================


class TestComplianceSummary:
    """Test compliance summary calculations."""

    @pytest.mark.asyncio
    async def test_all_compliant(self):
        from app.services.compliance.payroll_service import get_compliance_summary

        mock_records = []
        for _i in range(5):
            r = MagicMock()
            r.compliance_status = "compliant"
            r.trade = "carpenter"
            mock_records.append(r)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_records
        mock_db.execute.return_value = mock_result

        summary = await get_compliance_summary(mock_db, uuid.uuid4())
        assert summary.total_records == 5
        assert summary.compliant_records == 5
        assert summary.underpayment_records == 0
        assert summary.compliance_rate == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_mixed_compliance(self):
        from app.services.compliance.payroll_service import get_compliance_summary

        records = []
        for status_val in ["compliant", "compliant", "compliant", "underpayment", "review"]:
            r = MagicMock()
            r.compliance_status = status_val
            r.trade = "electrician"
            r.prevailing_wage_rate_id = None
            records.append(r)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = records
        mock_db.execute.return_value = mock_result

        summary = await get_compliance_summary(mock_db, uuid.uuid4())
        assert summary.total_records == 5
        assert summary.compliant_records == 3
        assert summary.compliance_rate == Decimal("60.00")

    @pytest.mark.asyncio
    async def test_trades_with_issues(self):
        from app.services.compliance.payroll_service import get_compliance_summary

        records = []
        for trade, status_val in [
            ("carpenter", "compliant"),
            ("electrician", "underpayment"),
            ("plumber", "underpayment"),
        ]:
            r = MagicMock()
            r.compliance_status = status_val
            r.trade = trade
            r.prevailing_wage_rate_id = None
            records.append(r)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = records
        mock_db.execute.return_value = mock_result

        summary = await get_compliance_summary(mock_db, uuid.uuid4())
        assert "electrician" in summary.trades_with_issues
        assert "plumber" in summary.trades_with_issues
        assert "carpenter" not in summary.trades_with_issues


# ===========================================================================
# TestEndpoints
# ===========================================================================


class TestEndpoints:
    """Test API endpoint structure and validation."""

    def test_payroll_record_schema(self):
        from app.schemas.payroll import PayrollRecordCreate

        schema = PayrollRecordCreate(
            worker_name="Test Worker",
            trade="carpenter",
            classification="journeyman",
            pay_period_start=date(2026, 3, 1),
            pay_period_end=date(2026, 3, 7),
            rate_straight=Decimal("45.00"),
        )
        assert schema.worker_name == "Test Worker"
        assert schema.hours_straight == Decimal("0")

    def test_payroll_record_end_before_start(self):
        from app.schemas.payroll import PayrollRecordCreate

        with pytest.raises(Exception):
            PayrollRecordCreate(
                worker_name="Test",
                trade="carpenter",
                classification="journeyman",
                pay_period_start=date(2026, 3, 7),
                pay_period_end=date(2026, 3, 1),
                rate_straight=Decimal("45.00"),
            )

    def test_certified_report_schema(self):
        from app.schemas.payroll import CertifiedReportGenerateRequest

        req = CertifiedReportGenerateRequest(
            pay_period_start=date(2026, 3, 1),
            pay_period_end=date(2026, 3, 7),
            contractor_name="ABC Construction",
        )
        assert req.contractor_name == "ABC Construction"

    def test_compliance_summary_response_schema(self):
        from app.schemas.payroll import ComplianceSummaryResponse

        resp = ComplianceSummaryResponse(
            total_records=10,
            compliant_records=8,
            underpayment_records=1,
            review_records=1,
            compliance_rate=Decimal("80.00"),
            total_underpayment=Decimal("500.00"),
            trades_with_issues=["electrician"],
        )
        assert resp.compliance_rate == Decimal("80.00")

    def test_batch_create_schema(self):
        from app.schemas.payroll import PayrollRecordBatchCreate, PayrollRecordCreate

        batch = PayrollRecordBatchCreate(
            records=[
                PayrollRecordCreate(
                    worker_name="Worker A",
                    trade="carpenter",
                    classification="journeyman",
                    pay_period_start=date(2026, 3, 1),
                    pay_period_end=date(2026, 3, 7),
                    rate_straight=Decimal("40.00"),
                ),
                PayrollRecordCreate(
                    worker_name="Worker B",
                    trade="electrician",
                    classification="journeyman",
                    pay_period_start=date(2026, 3, 1),
                    pay_period_end=date(2026, 3, 7),
                    rate_straight=Decimal("50.00"),
                ),
            ]
        )
        assert len(batch.records) == 2
