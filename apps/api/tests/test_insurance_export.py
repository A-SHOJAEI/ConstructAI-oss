"""Tests for insurance and risk data export service.

Covers:
- TRIR/DART/severity rate calculations
- EMR calculation with NCCI formula (primary/excess splits)
- Loss run generation and cost estimation
- Risk profile with TRIR trend
- OSHA 300 log generation and privacy masking
- Safety summary aggregation
- CSV export formatting
- API endpoint schemas
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.compliance.insurance_export_service import (
    NCCI_CLASS_CODES,
    ZERO,
    EMRResult,
    LossRun,
    OSHA300Log,
    RiskProfile,
    SafetySummary,
    _calculate_dart,
    _calculate_expected_losses,
    _calculate_severity_rate,
    _calculate_trir,
    _mask_name,
    calculate_emr,
    export_to_csv,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_summary(**overrides) -> SafetySummary:
    """Create a minimal SafetySummary."""
    base = dict(
        org_id="org-1",
        project_id=None,
        date_range_start=date(2025, 1, 1),
        date_range_end=date(2025, 12, 31),
        total_hours_worked=Decimal("500000"),
        total_recordable_incidents=5,
        trir=Decimal("2.00"),
        dart_incidents=3,
        dart_rate=Decimal("1.20"),
        lost_time_injuries=2,
        ltir=Decimal("0.80"),
        near_misses=10,
        near_miss_frequency=Decimal("4.00"),
        severity_rate=Decimal("8.00"),
        lost_workdays=20,
    )
    base.update(overrides)
    return SafetySummary(**base)


# ===========================================================================
# TestTRIRCalculation
# ===========================================================================


class TestTRIRCalculation:
    """Test TRIR, DART, and severity rate formulas."""

    def test_trir_basic(self):
        # 5 incidents, 500,000 hours = 5 * 200000 / 500000 = 2.0
        result = _calculate_trir(5, Decimal("500000"))
        assert result == Decimal("2.00")

    def test_trir_zero_hours(self):
        result = _calculate_trir(3, ZERO)
        assert result == ZERO

    def test_dart_basic(self):
        # 3 DART, 500,000 hours = 3 * 200000 / 500000 = 1.2
        result = _calculate_dart(3, Decimal("500000"))
        assert result == Decimal("1.20")

    def test_severity_rate(self):
        # 20 lost workdays, 500,000 hours = 20 * 200000 / 500000 = 8.0
        result = _calculate_severity_rate(20, Decimal("500000"))
        assert result == Decimal("8.00")


# ===========================================================================
# TestEMRCalculation
# ===========================================================================


class TestEMRCalculation:
    """Test NCCI EMR formula."""

    def test_emr_equal_losses(self):
        """When actual = expected, EMR should be close to 1.0."""
        result = calculate_emr(
            actual_losses=Decimal("50000"),
            expected_losses=Decimal("50000"),
        )
        assert isinstance(result, EMRResult)
        assert abs(result.emr_value - Decimal("1.000")) < Decimal("0.05")

    def test_emr_zero_losses(self):
        """Zero actual losses should give EMR < 1.0."""
        result = calculate_emr(
            actual_losses=ZERO,
            expected_losses=Decimal("50000"),
        )
        assert result.emr_value < Decimal("1.000")

    def test_emr_high_losses(self):
        """High actual losses should give EMR > 1.0."""
        result = calculate_emr(
            actual_losses=Decimal("200000"),
            expected_losses=Decimal("50000"),
        )
        assert result.emr_value > Decimal("1.000")

    def test_emr_zero_expected(self):
        """Zero expected losses returns default EMR of 1.0."""
        result = calculate_emr(
            actual_losses=Decimal("10000"),
            expected_losses=ZERO,
        )
        assert result.emr_value == Decimal("1.000")

    def test_emr_components_present(self):
        result = calculate_emr(
            actual_losses=Decimal("75000"),
            expected_losses=Decimal("100000"),
        )
        assert result.actual_primary > ZERO
        assert result.actual_excess > ZERO
        assert result.expected_primary > ZERO
        assert result.expected_excess > ZERO
        assert result.ballast_value > ZERO

    def test_emr_custom_ballast(self):
        result = calculate_emr(
            actual_losses=Decimal("50000"),
            expected_losses=Decimal("50000"),
            ballast_value=Decimal("5000"),
            weighting_factor=Decimal("0.50"),
        )
        assert isinstance(result.emr_value, Decimal)
        assert result.ballast_value == Decimal("5000")


# ===========================================================================
# TestExpectedLosses
# ===========================================================================


class TestExpectedLosses:
    """Test expected loss calculation from payroll by NCCI class."""

    def test_single_class(self):
        payroll = {"5403": Decimal("500000")}  # Carpentry — Commercial
        total, by_class = _calculate_expected_losses(payroll)
        # 500000 / 100 * 11.23 = 56,150
        assert by_class["5403"] == Decimal("56150.00")
        assert total == Decimal("56150.00")

    def test_multiple_classes(self):
        payroll = {
            "5403": Decimal("300000"),  # Carpentry
            "5190": Decimal("200000"),  # Electrical
        }
        total, by_class = _calculate_expected_losses(payroll)
        assert len(by_class) == 2
        assert total == by_class["5403"] + by_class["5190"]

    def test_unknown_class(self):
        payroll = {"9999": Decimal("100000")}
        total, by_class = _calculate_expected_losses(payroll)
        assert total == ZERO
        assert len(by_class) == 0


# ===========================================================================
# TestLossRun
# ===========================================================================


class TestLossRun:
    """Test loss run report generation."""

    @pytest.mark.asyncio
    async def test_loss_run_empty(self):
        """Empty org returns empty loss run."""
        from app.services.compliance.insurance_export_service import generate_loss_run

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        loss_run = await generate_loss_run(
            mock_db, str(uuid.uuid4()), date(2025, 1, 1), date(2025, 12, 31)
        )
        assert isinstance(loss_run, LossRun)
        assert len(loss_run.entries) == 0
        assert loss_run.total_incurred == ZERO

    @pytest.mark.asyncio
    async def test_loss_run_with_incidents(self):
        from app.services.compliance.insurance_export_service import generate_loss_run

        mock_rows = [
            {
                "id": uuid.uuid4(),
                "created_at": date(2025, 6, 15),
                "alert_type": "fall",
                "description": "Worker fell from scaffold",
                "priority": "high",
                "is_acknowledged": True,
            },
            {
                "id": uuid.uuid4(),
                "created_at": date(2025, 8, 20),
                "alert_type": "struck_by",
                "description": "Hit by falling object",
                "priority": "medium",
                "is_acknowledged": False,
            },
        ]
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = mock_rows
        mock_db.execute.return_value = mock_result

        loss_run = await generate_loss_run(
            mock_db, str(uuid.uuid4()), date(2025, 1, 1), date(2025, 12, 31)
        )
        assert len(loss_run.entries) == 2
        assert loss_run.closed_claims == 1
        assert loss_run.open_claims == 1
        assert loss_run.total_incurred > ZERO

    @pytest.mark.asyncio
    async def test_loss_run_cost_by_priority(self):
        """Critical incidents cost more than low priority."""
        from app.services.compliance.insurance_export_service import generate_loss_run

        mock_rows = [
            {
                "id": uuid.uuid4(),
                "created_at": date(2025, 3, 1),
                "alert_type": "fall",
                "description": "Fatal fall",
                "priority": "critical",
                "is_acknowledged": False,
            },
            {
                "id": uuid.uuid4(),
                "created_at": date(2025, 3, 2),
                "alert_type": "near_miss",
                "description": "Near miss",
                "priority": "low",
                "is_acknowledged": True,
            },
        ]
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = mock_rows
        mock_db.execute.return_value = mock_result

        loss_run = await generate_loss_run(
            mock_db, str(uuid.uuid4()), date(2025, 1, 1), date(2025, 12, 31)
        )
        critical_entry = next(e for e in loss_run.entries if e.incident_type == "fall")
        low_entry = next(e for e in loss_run.entries if e.incident_type == "near_miss")
        assert critical_entry.total_cost > low_entry.total_cost

    @pytest.mark.asyncio
    async def test_loss_run_db_failure_graceful(self):
        """DB failure returns empty loss run."""
        from app.services.compliance.insurance_export_service import generate_loss_run

        mock_db = AsyncMock()
        mock_db.execute.side_effect = Exception("DB connection lost")

        loss_run = await generate_loss_run(
            mock_db, str(uuid.uuid4()), date(2025, 1, 1), date(2025, 12, 31)
        )
        assert len(loss_run.entries) == 0


# ===========================================================================
# TestRiskProfile
# ===========================================================================


class TestRiskProfile:
    """Test risk profile generation."""

    @pytest.mark.asyncio
    async def test_risk_profile_structure(self):
        from app.services.compliance.insurance_export_service import generate_risk_profile

        mock_db = AsyncMock()
        # Mock the safety summary calls (3-year trend) and other queries
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        mock_result.mappings.return_value.first.return_value = None
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar_one.return_value = 0
        mock_db.execute.return_value = mock_result

        profile = await generate_risk_profile(mock_db, str(uuid.uuid4()))
        assert isinstance(profile, RiskProfile)
        assert len(profile.trir_trend) == 3  # 3-year trend

    @pytest.mark.asyncio
    async def test_risk_profile_with_project(self):
        from app.services.compliance.insurance_export_service import generate_risk_profile

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        mock_result.mappings.return_value.first.return_value = None
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar_one.return_value = 0
        mock_db.execute.return_value = mock_result

        profile = await generate_risk_profile(mock_db, str(uuid.uuid4()), str(uuid.uuid4()))
        assert profile.project_id is not None

    @pytest.mark.asyncio
    async def test_risk_profile_emr_history(self):
        from app.services.compliance.insurance_export_service import generate_risk_profile

        mock_emr = MagicMock()
        mock_emr.calculation_year = 2024
        mock_emr.emr_value = Decimal("0.95")
        mock_emr.actual_losses = Decimal("10000")
        mock_emr.expected_losses = Decimal("12000")

        mock_db = AsyncMock()
        mock_result_generic = MagicMock()
        mock_result_generic.mappings.return_value.all.return_value = []
        mock_result_generic.mappings.return_value.first.return_value = None
        mock_result_generic.scalar_one.return_value = 0

        mock_result_emr = MagicMock()
        mock_result_emr.scalars.return_value.all.return_value = [mock_emr]

        # The EMR query is the last one in the function
        mock_db.execute.side_effect = [
            # 3 safety summary calls (each uses multiple DB calls)
            mock_result_generic,
            mock_result_generic,
            mock_result_generic,
            mock_result_generic,
            mock_result_generic,
            mock_result_generic,
            mock_result_generic,
            mock_result_generic,
            mock_result_generic,
            # risk scores
            mock_result_generic,
            # PPE compliance
            mock_result_generic,
            # EMR history
            mock_result_emr,
        ]

        profile = await generate_risk_profile(mock_db, str(uuid.uuid4()))
        assert len(profile.emr_history) == 1
        assert profile.emr_history[0]["year"] == 2024

    @pytest.mark.asyncio
    async def test_risk_profile_ppe_compliance_default(self):
        from app.services.compliance.insurance_export_service import generate_risk_profile

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        mock_result.mappings.return_value.first.return_value = {"compliant": 0, "total": 0}
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar_one.return_value = 0
        mock_db.execute.return_value = mock_result

        profile = await generate_risk_profile(mock_db, str(uuid.uuid4()))
        assert profile.ppe_compliance_rate >= ZERO


# ===========================================================================
# TestOSHA300
# ===========================================================================


class TestOSHA300:
    """Test OSHA 300 log generation."""

    @pytest.mark.asyncio
    async def test_osha_300_empty(self):
        from app.services.compliance.insurance_export_service import generate_osha_300_log

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        log = await generate_osha_300_log(mock_db, str(uuid.uuid4()), "Main Office", 2025)
        assert isinstance(log, OSHA300Log)
        assert len(log.entries) == 0
        assert log.total_deaths == 0

    @pytest.mark.asyncio
    async def test_osha_300_classification(self):
        from app.services.compliance.insurance_export_service import generate_osha_300_log

        mock_rows = [
            {
                "id": uuid.uuid4(),
                "created_at": date(2025, 5, 10),
                "alert_type": "fall",
                "description": "Fall from height",
                "priority": "critical",
            },
            {
                "id": uuid.uuid4(),
                "created_at": date(2025, 7, 20),
                "alert_type": "struck_by",
                "description": "Hit by object",
                "priority": "high",
            },
            {
                "id": uuid.uuid4(),
                "created_at": date(2025, 9, 1),
                "alert_type": "near_miss",
                "description": "Near miss event",
                "priority": "medium",
            },
        ]
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = mock_rows
        mock_db.execute.return_value = mock_result

        log = await generate_osha_300_log(mock_db, str(uuid.uuid4()), "Site A", 2025)
        assert len(log.entries) == 3
        assert log.total_days_away_cases == 1  # critical = days_away
        assert log.total_restricted_cases == 1  # high = restricted
        assert log.total_other_recordable == 1  # medium = other

    def test_privacy_masking(self):
        assert _mask_name("John Smith") == "J. Smith"
        assert _mask_name("Jane") == "J."
        assert _mask_name("") == "Anonymous"
        assert _mask_name("Mary Jane Watson") == "M. Watson"

    @pytest.mark.asyncio
    async def test_osha_300_case_numbers(self):
        from app.services.compliance.insurance_export_service import generate_osha_300_log

        mock_rows = [
            {
                "id": uuid.uuid4(),
                "created_at": date(2025, 1, 15),
                "alert_type": "fall",
                "description": "Test",
                "priority": "medium",
            },
            {
                "id": uuid.uuid4(),
                "created_at": date(2025, 2, 20),
                "alert_type": "fall",
                "description": "Test2",
                "priority": "medium",
            },
        ]
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = mock_rows
        mock_db.execute.return_value = mock_result

        log = await generate_osha_300_log(mock_db, str(uuid.uuid4()), "Office", 2025)
        assert log.entries[0].case_number == "2025-0001"
        assert log.entries[1].case_number == "2025-0002"

    @pytest.mark.asyncio
    async def test_osha_300_days_calculation(self):
        from app.services.compliance.insurance_export_service import generate_osha_300_log

        mock_rows = [
            {
                "id": uuid.uuid4(),
                "created_at": date(2025, 3, 1),
                "alert_type": "fall",
                "description": "Serious fall",
                "priority": "critical",
            },
        ]
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = mock_rows
        mock_db.execute.return_value = mock_result

        log = await generate_osha_300_log(mock_db, str(uuid.uuid4()), "Site", 2025)
        assert log.total_days_away == 10  # critical = 10 days away


# ===========================================================================
# TestSafetySummary
# ===========================================================================


class TestSafetySummary:
    """Test safety summary generation."""

    @pytest.mark.asyncio
    async def test_safety_summary_basic(self):
        from app.services.compliance.insurance_export_service import generate_safety_summary

        mock_db = AsyncMock()
        # Incident query
        incident_result = MagicMock()
        incident_result.mappings.return_value.all.return_value = [
            {"incident_type": "fall", "cnt": 3},
            {"incident_type": "struck_by", "cnt": 2},
        ]
        # Hours query
        hours_result = MagicMock()
        hours_result.scalar_one.return_value = 400000
        # Near miss query
        nm_result = MagicMock()
        nm_result.scalar_one.return_value = 8

        mock_db.execute.side_effect = [incident_result, hours_result, nm_result]

        summary = await generate_safety_summary(
            mock_db,
            str(uuid.uuid4()),
            None,
            date(2025, 1, 1),
            date(2025, 12, 31),
        )
        assert isinstance(summary, SafetySummary)
        assert summary.total_recordable_incidents == 5
        assert summary.incident_by_type["fall"] == 3

    @pytest.mark.asyncio
    async def test_safety_summary_with_project(self):
        from app.services.compliance.insurance_export_service import generate_safety_summary

        mock_db = AsyncMock()
        incident_result = MagicMock()
        incident_result.mappings.return_value.all.return_value = []
        hours_result = MagicMock()
        hours_result.scalar_one.return_value = 100000
        nm_result = MagicMock()
        nm_result.scalar_one.return_value = 0

        mock_db.execute.side_effect = [incident_result, hours_result, nm_result]

        summary = await generate_safety_summary(
            mock_db,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            date(2025, 6, 1),
            date(2025, 6, 30),
        )
        assert summary.project_id is not None

    @pytest.mark.asyncio
    async def test_safety_summary_rates(self):
        from app.services.compliance.insurance_export_service import generate_safety_summary

        mock_db = AsyncMock()
        incident_result = MagicMock()
        incident_result.mappings.return_value.all.return_value = [
            {"incident_type": "fall", "cnt": 10},
        ]
        hours_result = MagicMock()
        hours_result.scalar_one.return_value = 1000000  # 1M hours
        nm_result = MagicMock()
        nm_result.scalar_one.return_value = 20

        mock_db.execute.side_effect = [incident_result, hours_result, nm_result]

        summary = await generate_safety_summary(
            mock_db,
            str(uuid.uuid4()),
            None,
            date(2025, 1, 1),
            date(2025, 12, 31),
        )
        # TRIR = 10 * 200000 / 1000000 = 2.0
        assert summary.trir == Decimal("2.00")

    @pytest.mark.asyncio
    async def test_safety_summary_db_failure(self):
        from app.services.compliance.insurance_export_service import generate_safety_summary

        mock_db = AsyncMock()
        mock_db.execute.side_effect = Exception("DB error")

        summary = await generate_safety_summary(
            mock_db,
            str(uuid.uuid4()),
            None,
            date(2025, 1, 1),
            date(2025, 12, 31),
        )
        # Should gracefully fallback
        assert summary.total_recordable_incidents == 0


# ===========================================================================
# TestCSVExport
# ===========================================================================


class TestCSVExport:
    """Test CSV export formatting."""

    def test_safety_summary_csv(self):
        data = {
            "org_id": "test",
            "trir": "2.00",
            "dart_rate": "1.20",
            "total_recordable_incidents": 5,
            "entries": [],  # list — should be skipped
        }
        csv_bytes = export_to_csv(data, "safety_summary")
        text = csv_bytes.decode("utf-8")
        assert "Metric" in text
        assert "trir" in text
        assert "2.00" in text

    def test_loss_run_csv(self):
        data = {
            "entries": [
                {
                    "incident_date": "2025-06-15",
                    "incident_type": "fall",
                    "description": "Test incident",
                    "medical_cost": "10000",
                    "indemnity_cost": "15000",
                    "property_cost": "5000",
                    "total_cost": "30000",
                    "status": "closed",
                    "reserve_amount": "0",
                },
            ],
        }
        csv_bytes = export_to_csv(data, "loss_run")
        text = csv_bytes.decode("utf-8")
        assert "Date" in text
        assert "fall" in text
        assert "30000" in text

    def test_osha_300_csv(self):
        data = {
            "entries": [
                {
                    "case_number": "2025-0001",
                    "employee_name": "J. Smith",
                    "job_title": "Carpenter",
                    "date_of_injury": "2025-03-15",
                    "where_event_occurred": "Site A",
                    "description": "Fall from scaffold",
                    "classified_as": "days_away",
                    "days_away": 10,
                    "days_restricted": 0,
                },
            ],
        }
        csv_bytes = export_to_csv(data, "osha_300")
        text = csv_bytes.decode("utf-8")
        assert "Case No." in text
        assert "2025-0001" in text
        assert "J. Smith" in text


# ===========================================================================
# TestEndpoints
# ===========================================================================


class TestEndpoints:
    """Test API endpoint schemas and validation."""

    def test_emr_calculate_request(self):
        from app.schemas.insurance import EMRCalculateRequest

        req = EMRCalculateRequest(
            payroll_by_class={"5403": Decimal("500000")},
            year=2025,
        )
        assert req.year == 2025
        assert "5403" in req.payroll_by_class

    def test_export_request_validation(self):
        from app.schemas.insurance import ExportRequest

        with pytest.raises(Exception):
            ExportRequest(
                export_type="invalid_type",
                format="csv",
                date_range_start=date(2025, 1, 1),
                date_range_end=date(2025, 12, 31),
            )

    def test_export_format_validation(self):
        from app.schemas.insurance import ExportRequest

        req = ExportRequest(
            export_type="safety_summary",
            format="pdf",
            date_range_start=date(2025, 1, 1),
            date_range_end=date(2025, 12, 31),
        )
        assert req.format == "pdf"

    def test_safety_summary_response(self):
        from app.schemas.insurance import SafetySummaryResponse

        resp = SafetySummaryResponse(
            org_id="org-1",
            date_range_start=date(2025, 1, 1),
            date_range_end=date(2025, 12, 31),
            total_hours_worked=Decimal("500000"),
            total_recordable_incidents=5,
            trir=Decimal("2.00"),
            dart_incidents=3,
            dart_rate=Decimal("1.20"),
            lost_time_injuries=2,
            ltir=Decimal("0.80"),
            near_misses=10,
            near_miss_frequency=Decimal("4.00"),
            severity_rate=Decimal("8.00"),
            lost_workdays=20,
        )
        assert resp.trir == Decimal("2.00")

    def test_ncci_class_codes_present(self):
        """Verify key construction class codes are defined."""
        assert "5403" in NCCI_CLASS_CODES  # Carpentry
        assert "5190" in NCCI_CLASS_CODES  # Electrical
        assert "5545" in NCCI_CLASS_CODES  # Roofing
        assert "5213" in NCCI_CLASS_CODES  # Concrete
        assert len(NCCI_CLASS_CODES) >= 30
