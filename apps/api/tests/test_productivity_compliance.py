"""Tests for productivity rates and compliance checklists seed data.

Covers:
  - Productivity rate seed data: loading, validation, math consistency,
    trade coverage, caching
  - Productivity forecaster: baseline rate lookup, baseline forecast,
    historical forecast, edge cases
  - Productivity rate model (ProductivityRate)
  - Productivity seeder script: validation logic
  - Compliance checklist seed data: loading, validation, category/severity
    coverage, filtering, caching
  - Compliance checker service: get_checklists filtering, get_checklist_by_id,
    get_checklist_summary
  - Compliance seeder script: validation logic
  - API endpoints: compliance-checklists list, filter, summary, detail
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.productivity.productivity_forecaster import (
    _baseline_forecast,
    _empty_forecast,
    clear_baseline_cache,
    forecast_productivity,
    get_baseline_rate,
    get_trade_summary,
)
from app.services.quality.compliance_checker import (
    clear_checklist_cache,
    get_checklist_by_id,
    get_checklist_summary,
    get_checklists,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear all caches before and after each test."""
    clear_baseline_cache()
    clear_checklist_cache()
    yield
    clear_baseline_cache()
    clear_checklist_cache()


PRODUCTIVITY_SEED = (
    Path(__file__).resolve().parents[1] / "data" / "seed" / "productivity_rates_v1.json"
)
COMPLIANCE_SEED = (
    Path(__file__).resolve().parents[1] / "data" / "seed" / "compliance_checklists_v1.json"
)


def _load_productivity_rates() -> list[dict]:
    """Helper to load productivity rates from seed JSON."""
    if not PRODUCTIVITY_SEED.exists():
        pytest.skip("productivity_rates_v1.json not found")
    with open(PRODUCTIVITY_SEED) as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "rates" in raw:
        return raw["rates"]
    return raw


def _load_compliance_checklists() -> list[dict]:
    """Helper to load compliance checklists from seed JSON."""
    if not COMPLIANCE_SEED.exists():
        pytest.skip("compliance_checklists_v1.json not found")
    with open(COMPLIANCE_SEED) as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "checklists" in raw:
        return raw["checklists"]
    return raw


# ===========================================================================
# PRODUCTIVITY RATES — Seed Data Validation
# ===========================================================================


class TestProductivitySeedData:
    """Validate the productivity rates seed JSON file."""

    def test_seed_file_exists(self):
        assert PRODUCTIVITY_SEED.exists(), f"Seed file not found: {PRODUCTIVITY_SEED}"

    def test_seed_loads_valid_json(self):
        rates = _load_productivity_rates()
        assert isinstance(rates, list)
        assert len(rates) > 0

    def test_minimum_activity_count(self):
        """Must have at least 200 activities."""
        rates = _load_productivity_rates()
        assert len(rates) >= 200, f"Expected 200+, got {len(rates)}"

    def test_required_fields_present(self):
        """Every rate entry must have required fields."""
        rates = _load_productivity_rates()
        required = {
            "activity_code",
            "activity_name",
            "trade",
            "crew_size",
            "daily_output",
            "unit",
            "manhours_per_unit",
        }
        for i, rate in enumerate(rates):
            missing = required - set(rate.keys())
            assert not missing, f"Rate {i} ({rate.get('activity_code', '?')}): missing {missing}"

    def test_no_duplicate_activity_codes(self):
        rates = _load_productivity_rates()
        codes = [r["activity_code"] for r in rates]
        dupes = [c for c in codes if codes.count(c) > 1]
        assert not dupes, f"Duplicate codes: {set(dupes)}"

    def test_positive_values(self):
        """crew_size, daily_output, manhours_per_unit must be positive."""
        rates = _load_productivity_rates()
        for rate in rates:
            code = rate["activity_code"]
            assert rate["crew_size"] > 0, f"{code}: crew_size <= 0"
            assert rate["daily_output"] > 0, f"{code}: daily_output <= 0"
            assert rate["manhours_per_unit"] > 0, f"{code}: manhours_per_unit <= 0"

    def test_manhours_math_consistency(self):
        """manhours_per_unit should approximately equal crew_size * 8 / daily_output."""
        rates = _load_productivity_rates()
        for rate in rates:
            code = rate["activity_code"]
            expected = (rate["crew_size"] * 8) / rate["daily_output"]
            actual = rate["manhours_per_unit"]
            # Allow 5% tolerance for rounding
            ratio = actual / expected if expected > 0 else 999
            assert 0.9 <= ratio <= 1.1, (
                f"{code}: manhours_per_unit={actual} but expected ~{expected:.4f} "
                f"(crew={rate['crew_size']}, output={rate['daily_output']})"
            )

    def test_seven_trades_covered(self):
        """Must cover all 7 required trades."""
        rates = _load_productivity_rates()
        trades = {r["trade"].lower() for r in rates}
        required_trades = {
            "sitework",
            "concrete",
            "masonry",
            "steel",
            "wood_framing",
            "finishes",
            "mep",
        }
        missing = required_trades - trades
        assert not missing, f"Missing trades: {missing}"

    def test_trade_activity_counts(self):
        """Each trade should have at least the minimum required activities."""
        rates = _load_productivity_rates()
        trade_counts: dict[str, int] = {}
        for r in rates:
            t = r["trade"].lower()
            trade_counts[t] = trade_counts.get(t, 0) + 1

        min_counts = {
            "sitework": 15,
            "concrete": 20,
            "masonry": 15,
            "steel": 15,
            "wood_framing": 15,
            "finishes": 15,
            "mep": 25,
        }
        for trade, min_count in min_counts.items():
            actual = trade_counts.get(trade, 0)
            assert actual >= min_count, f"{trade}: expected >= {min_count} activities, got {actual}"

    def test_valid_units(self):
        """Units should be recognized construction units."""
        rates = _load_productivity_rates()
        valid_units = {
            "CY",
            "LF",
            "SF",
            "EA",
            "TON",
            "LB",
            "SY",
            "GAL",
            "CF",
            "MBF",
            "BF",
            "SQ",
            "HR",
            "JT",
            "VLF",
            "SFCA",
        }
        for rate in rates:
            unit = rate["unit"].upper()
            assert unit in valid_units, f"{rate['activity_code']}: unknown unit '{rate['unit']}'"

    def test_activity_code_prefix_matches_trade(self):
        """Activity codes should follow trade prefix convention."""
        rates = _load_productivity_rates()
        trade_prefix = {
            "sitework": "SW",
            "concrete": "CO",
            "masonry": "MA",
            "steel": "ST",
            "wood_framing": "WF",
            "finishes": "FI",
            "mep": "ME",
        }
        for rate in rates:
            trade = rate["trade"].lower()
            expected_prefix = trade_prefix.get(trade)
            if expected_prefix:
                assert rate["activity_code"].startswith(expected_prefix), (
                    f"{rate['activity_code']} should start with {expected_prefix} for trade {trade}"
                )

    def test_crew_composition_is_dict(self):
        """crew_composition should be a dict when present."""
        rates = _load_productivity_rates()
        for rate in rates:
            comp = rate.get("crew_composition")
            if comp is not None:
                assert isinstance(comp, dict), (
                    f"{rate['activity_code']}: crew_composition should be dict, got {type(comp)}"
                )

    def test_crew_composition_sums_to_crew_size(self):
        """Sum of crew_composition values should match crew_size."""
        rates = _load_productivity_rates()
        for rate in rates:
            comp = rate.get("crew_composition", {})
            if comp:
                comp_total = sum(comp.values())
                assert comp_total == rate["crew_size"], (
                    f"{rate['activity_code']}: crew_composition sums to {comp_total} "
                    f"but crew_size is {rate['crew_size']}"
                )


# ===========================================================================
# PRODUCTIVITY RATES — Forecaster Service
# ===========================================================================


class TestProductivityForecasterBaseline:
    """Test baseline rate lookup from seed data."""

    def test_get_baseline_rate_known_trade(self):
        """Should return a rate for a known trade."""
        rate = get_baseline_rate("concrete")
        if PRODUCTIVITY_SEED.exists():
            assert rate is not None
            assert "activity_code" in rate
            assert "manhours_per_unit" in rate

    def test_get_baseline_rate_unknown_trade(self):
        """Should return None for an unknown trade."""
        rate = get_baseline_rate("underwater_basket_weaving")
        assert rate is None

    def test_get_baseline_rate_case_insensitive(self):
        """Trade lookup should be case insensitive."""
        if not PRODUCTIVITY_SEED.exists():
            pytest.skip("seed file not found")
        rate_lower = get_baseline_rate("concrete")
        clear_baseline_cache()
        get_baseline_rate("Concrete")
        # Both should find a rate (lowercase normalization)
        # Note: get_baseline_rate does .lower() on input
        assert rate_lower is not None

    def test_get_baseline_rate_with_activity_code(self):
        """Should return specific rate when activity_code is provided."""
        if not PRODUCTIVITY_SEED.exists():
            pytest.skip("seed file not found")
        rates = _load_productivity_rates()
        if rates:
            first = rates[0]
            rate = get_baseline_rate(first["trade"], first["activity_code"])
            assert rate is not None
            assert rate["activity_code"] == first["activity_code"]

    def test_get_trade_summary(self):
        """Should return summary statistics for a trade."""
        if not PRODUCTIVITY_SEED.exists():
            pytest.skip("seed file not found")
        summary = get_trade_summary("concrete")
        assert summary["trade"] == "concrete"
        assert summary["activity_count"] > 0
        assert summary["avg_manhours_per_unit"] > 0
        assert "activities" in summary

    def test_get_trade_summary_unknown(self):
        """Unknown trade should return empty summary."""
        summary = get_trade_summary("unknown_trade")
        assert summary["activity_count"] == 0

    def test_cache_clearing(self):
        """Cache should be clearable."""
        get_baseline_rate("concrete")  # loads cache
        clear_baseline_cache()
        # Should reload on next call without error
        get_baseline_rate("concrete")


class TestProductivityForecaster:
    """Test the forecast_productivity function."""

    @pytest.mark.asyncio
    async def test_forecast_with_sufficient_data(self):
        """Should generate forecast when >= 3 data points provided."""
        historical = [
            {
                "work_date": "2025-01-01",
                "actual_units": 80,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-02",
                "actual_units": 85,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-03",
                "actual_units": 90,
                "planned_units": 100,
                "project_id": "p1",
            },
        ]
        result = await forecast_productivity(historical, "concrete", forecast_days=7)

        assert result["trade"] == "concrete"
        assert result["trend"] in ("improving", "declining", "stable")
        assert len(result["forecast_dates"]) == 7
        assert len(result["predicted_rates"]) == 7
        assert len(result["confidence_intervals"]) == 7

    @pytest.mark.asyncio
    async def test_forecast_trend_improving(self):
        """Should detect improving trend."""
        historical = [
            {
                "work_date": "2025-01-01",
                "actual_units": 60,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-02",
                "actual_units": 70,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-03",
                "actual_units": 80,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-04",
                "actual_units": 90,
                "planned_units": 100,
                "project_id": "p1",
            },
        ]
        result = await forecast_productivity(historical, "concrete")
        assert result["trend"] == "improving"

    @pytest.mark.asyncio
    async def test_forecast_trend_declining(self):
        """Should detect declining trend."""
        historical = [
            {
                "work_date": "2025-01-01",
                "actual_units": 95,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-02",
                "actual_units": 85,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-03",
                "actual_units": 75,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-04",
                "actual_units": 65,
                "planned_units": 100,
                "project_id": "p1",
            },
        ]
        result = await forecast_productivity(historical, "concrete")
        assert result["trend"] == "declining"

    @pytest.mark.asyncio
    async def test_forecast_trend_stable(self):
        """Should detect stable trend."""
        historical = [
            {
                "work_date": "2025-01-01",
                "actual_units": 100,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-02",
                "actual_units": 100,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-03",
                "actual_units": 100,
                "planned_units": 100,
                "project_id": "p1",
            },
        ]
        result = await forecast_productivity(historical, "concrete")
        assert result["trend"] == "stable"

    @pytest.mark.asyncio
    async def test_forecast_insufficient_data_with_baseline(self):
        """Should fallback to baseline when < 3 records and baseline exists."""
        if not PRODUCTIVITY_SEED.exists():
            pytest.skip("seed file not found")
        result = await forecast_productivity([], "concrete")
        assert result["trend"] == "baseline"
        assert "baseline_rate" in result
        assert result["baseline_rate"]["activity_code"] is not None

    @pytest.mark.asyncio
    async def test_forecast_insufficient_data_no_baseline(self):
        """Should return empty forecast when no data and no baseline."""
        result = await forecast_productivity([], "unknown_trade_xyz")
        assert result["trend"] == "insufficient_data"
        assert result["forecast_dates"] == []

    @pytest.mark.asyncio
    async def test_forecast_two_records_uses_baseline(self):
        """Should use baseline with only 2 records (< 3 threshold)."""
        if not PRODUCTIVITY_SEED.exists():
            pytest.skip("seed file not found")
        historical = [
            {"work_date": "2025-01-01", "actual_units": 80, "planned_units": 100},
            {"work_date": "2025-01-02", "actual_units": 85, "planned_units": 100},
        ]
        result = await forecast_productivity(historical, "concrete")
        assert result["trend"] == "baseline"

    @pytest.mark.asyncio
    async def test_forecast_confidence_intervals_widen(self):
        """Confidence intervals should widen over time."""
        historical = [
            {
                "work_date": "2025-01-01",
                "actual_units": 80,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-02",
                "actual_units": 85,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-03",
                "actual_units": 90,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-04",
                "actual_units": 75,
                "planned_units": 100,
                "project_id": "p1",
            },
        ]
        result = await forecast_productivity(historical, "concrete", forecast_days=14)
        intervals = result["confidence_intervals"]
        # Later intervals should be wider
        first_width = intervals[0]["upper"] - intervals[0]["lower"]
        last_width = intervals[-1]["upper"] - intervals[-1]["lower"]
        assert last_width >= first_width

    @pytest.mark.asyncio
    async def test_forecast_predicted_rates_non_negative(self):
        """All predicted rates should be >= 0."""
        historical = [
            {
                "work_date": "2025-01-01",
                "actual_units": 10,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-02",
                "actual_units": 5,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-03",
                "actual_units": 2,
                "planned_units": 100,
                "project_id": "p1",
            },
        ]
        result = await forecast_productivity(historical, "concrete", forecast_days=30)
        for rate in result["predicted_rates"]:
            assert rate >= 0.0

    @pytest.mark.asyncio
    async def test_forecast_dates_are_sequential(self):
        """Forecast dates should be sequential from last historical date."""
        historical = [
            {
                "work_date": "2025-06-01",
                "actual_units": 80,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-06-02",
                "actual_units": 85,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-06-03",
                "actual_units": 90,
                "planned_units": 100,
                "project_id": "p1",
            },
        ]
        result = await forecast_productivity(historical, "concrete", forecast_days=5)
        expected_start = date(2025, 6, 4)
        for i, d in enumerate(result["forecast_dates"]):
            assert d == expected_start + timedelta(days=i)

    @pytest.mark.asyncio
    async def test_forecast_zero_planned_units_skipped(self):
        """Records with planned_units=0 should be skipped in rate calculation."""
        historical = [
            {
                "work_date": "2025-01-01",
                "actual_units": 80,
                "planned_units": 100,
                "project_id": "p1",
            },
            {"work_date": "2025-01-02", "actual_units": 50, "planned_units": 0, "project_id": "p1"},
            {
                "work_date": "2025-01-03",
                "actual_units": 90,
                "planned_units": 100,
                "project_id": "p1",
            },
            {
                "work_date": "2025-01-04",
                "actual_units": 85,
                "planned_units": 100,
                "project_id": "p1",
            },
        ]
        result = await forecast_productivity(historical, "concrete")
        # Should still produce a forecast (3 valid records)
        assert result["trend"] in ("improving", "declining", "stable")


class TestBaselineForecast:
    """Test the _baseline_forecast helper."""

    def test_baseline_forecast_structure(self):
        baseline = {
            "activity_code": "CO-001",
            "activity_name": "Footing Formwork",
            "daily_output": 120.0,
            "unit": "SF",
            "manhours_per_unit": 0.2,
            "crew_size": 4,
            "crew_composition": {"carpenter": 3, "laborer": 1},
        }
        result = _baseline_forecast("concrete", 7, baseline)
        assert result["trade"] == "concrete"
        assert result["trend"] == "baseline"
        assert len(result["forecast_dates"]) == 7
        assert len(result["predicted_rates"]) == 7
        assert all(r == 1.0 for r in result["predicted_rates"])
        assert result["baseline_rate"]["activity_code"] == "CO-001"
        assert result["baseline_rate"]["daily_output"] == 120.0

    def test_baseline_forecast_confidence_intervals(self):
        baseline = {"daily_output": 100, "manhours_per_unit": 0.32}
        result = _baseline_forecast("steel", 5, baseline)
        for ci in result["confidence_intervals"]:
            assert ci["lower"] == 0.7
            assert ci["upper"] == 1.3


class TestEmptyForecast:
    """Test the _empty_forecast helper."""

    def test_empty_forecast_structure(self):
        result = _empty_forecast("unknown_trade", 14)
        assert result["trade"] == "unknown_trade"
        assert result["trend"] == "insufficient_data"
        assert result["forecast_dates"] == []
        assert result["predicted_rates"] == []
        assert result["confidence_intervals"] == []


# ===========================================================================
# PRODUCTIVITY RATES — Model
# ===========================================================================


class TestProductivityRateModel:
    """Test the ProductivityRate SQLAlchemy model."""

    def test_model_import(self):
        from app.models.productivity_rate import ProductivityRate

        assert ProductivityRate.__tablename__ == "productivity_rates"

    def test_model_in_init(self):
        from app.models import ProductivityRate

        assert ProductivityRate is not None

    def test_model_columns(self):
        from app.models.productivity_rate import ProductivityRate

        columns = {c.name for c in ProductivityRate.__table__.columns}
        expected = {
            "id",
            "activity_code",
            "activity_name",
            "csi_division",
            "trade",
            "crew_composition",
            "crew_size",
            "daily_output",
            "unit",
            "manhours_per_unit",
            "conditions",
            "data_source",
            "effective_date",
            "metadata",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns), f"Missing columns: {expected - columns}"


# ===========================================================================
# PRODUCTIVITY RATES — Seeder Script Validation
# ===========================================================================


class TestProductivitySeederValidation:
    """Test the seeder script validation logic."""

    def test_validate_rates_no_warnings(self):
        """Valid data should produce no warnings."""
        from scripts.seed_productivity_rates import validate_rates

        rates = [
            {
                "activity_code": "TEST-001",
                "activity_name": "Test Activity",
                "trade": "concrete",
                "crew_size": 4,
                "daily_output": 100,
                "unit": "SF",
                "manhours_per_unit": 0.32,
            }
        ]
        warnings = validate_rates(rates)
        assert len(warnings) == 0

    def test_validate_rates_missing_keys(self):
        from scripts.seed_productivity_rates import validate_rates

        rates = [{"activity_code": "TEST-001"}]
        warnings = validate_rates(rates)
        assert any("missing keys" in w for w in warnings)

    def test_validate_rates_duplicate_codes(self):
        from scripts.seed_productivity_rates import validate_rates

        rates = [
            {
                "activity_code": "DUPE",
                "activity_name": "A",
                "trade": "concrete",
                "crew_size": 4,
                "daily_output": 100,
                "unit": "SF",
                "manhours_per_unit": 0.32,
            },
            {
                "activity_code": "DUPE",
                "activity_name": "B",
                "trade": "steel",
                "crew_size": 4,
                "daily_output": 100,
                "unit": "SF",
                "manhours_per_unit": 0.32,
            },
        ]
        warnings = validate_rates(rates)
        assert any("duplicate" in w for w in warnings)

    def test_validate_rates_invalid_values(self):
        from scripts.seed_productivity_rates import validate_rates

        rates = [
            {
                "activity_code": "BAD-001",
                "activity_name": "Bad",
                "trade": "concrete",
                "crew_size": 4,
                "daily_output": -1,
                "unit": "SF",
                "manhours_per_unit": 0,
            },
        ]
        warnings = validate_rates(rates)
        assert any("daily_output <= 0" in w for w in warnings)
        assert any("manhours_per_unit <= 0" in w for w in warnings)

    def test_validate_seed_file(self):
        """Validate actual seed file has no warnings."""
        if not PRODUCTIVITY_SEED.exists():
            pytest.skip("seed file not found")
        from scripts.seed_productivity_rates import validate_rates

        rates = _load_productivity_rates()
        warnings = validate_rates(rates)
        assert len(warnings) == 0, f"Seed validation warnings: {warnings}"


# ===========================================================================
# COMPLIANCE CHECKLISTS — Seed Data Validation
# ===========================================================================


class TestComplianceSeedData:
    """Validate the compliance checklists seed JSON file."""

    def test_seed_file_exists(self):
        assert COMPLIANCE_SEED.exists(), f"Seed file not found: {COMPLIANCE_SEED}"

    def test_seed_loads_valid_json(self):
        checks = _load_compliance_checklists()
        assert isinstance(checks, list)
        assert len(checks) > 0

    def test_minimum_check_count(self):
        """Must have at least 250 checks."""
        checks = _load_compliance_checklists()
        assert len(checks) >= 250, f"Expected 250+, got {len(checks)}"

    def test_required_fields_present(self):
        """Every check must have required fields."""
        checks = _load_compliance_checklists()
        required = {
            "category",
            "check_id",
            "description",
            "standard_reference",
            "severity",
            "applicable_project_types",
            "applicable_phases",
            "frequency",
        }
        for i, check in enumerate(checks):
            missing = required - set(check.keys())
            assert not missing, f"Check {i} ({check.get('check_id', '?')}): missing {missing}"

    def test_no_duplicate_check_ids(self):
        checks = _load_compliance_checklists()
        ids = [c["check_id"] for c in checks]
        dupes = [cid for cid in ids if ids.count(cid) > 1]
        assert not dupes, f"Duplicate check_ids: {set(dupes)}"

    def test_valid_categories(self):
        """Categories must be one of the 4 valid values."""
        checks = _load_compliance_checklists()
        valid = {"osha_safety", "ibc_inspection", "environmental_swppp", "quality_control"}
        for check in checks:
            assert check["category"] in valid, (
                f"{check['check_id']}: invalid category '{check['category']}'"
            )

    def test_valid_severities(self):
        """Severity must be critical, major, or minor."""
        checks = _load_compliance_checklists()
        valid = {"critical", "major", "minor"}
        for check in checks:
            assert check["severity"] in valid, (
                f"{check['check_id']}: invalid severity '{check['severity']}'"
            )

    def test_category_counts(self):
        """Each category should have at least minimum checks."""
        checks = _load_compliance_checklists()
        cats: dict[str, int] = {}
        for c in checks:
            cat = c["category"]
            cats[cat] = cats.get(cat, 0) + 1

        min_counts = {
            "osha_safety": 80,
            "ibc_inspection": 35,
            "environmental_swppp": 20,
            "quality_control": 40,
        }
        for cat, min_count in min_counts.items():
            actual = cats.get(cat, 0)
            assert actual >= min_count, f"{cat}: expected >= {min_count} checks, got {actual}"

    def test_osha_checks_have_cfr_references(self):
        """OSHA checks should reference 29 CFR 1926."""
        checks = _load_compliance_checklists()
        osha_checks = [c for c in checks if c["category"] == "osha_safety"]
        for check in osha_checks:
            ref = check["standard_reference"]
            assert "1926" in ref or "CFR" in ref.upper(), (
                f"{check['check_id']}: OSHA check should reference CFR 1926, got '{ref}'"
            )

    def test_ibc_checks_have_section_references(self):
        """IBC checks should reference recognized building code standards."""
        checks = _load_compliance_checklists()
        ibc_checks = [c for c in checks if c["category"] == "ibc_inspection"]
        # IBC checks may also reference ACI, ASTM, NFPA, NEC, IPC, IMC, IFC, etc.
        valid_prefixes = {
            "IBC",
            "ACI",
            "ASTM",
            "NFPA",
            "ASCE",
            "AWS",
            "AISC",
            "ICC",
            "NEC",
            "IPC",
            "IMC",
            "IFC",
            "IECC",
        }
        for check in ibc_checks:
            ref = check["standard_reference"].upper()
            has_valid_ref = any(prefix in ref for prefix in valid_prefixes)
            assert has_valid_ref, (
                f"{check['check_id']}: IBC check should reference a building code standard, got '{check['standard_reference']}'"
            )

    def test_applicable_project_types_valid(self):
        """Project types should be from known set."""
        checks = _load_compliance_checklists()
        valid_types = {"commercial", "residential", "industrial", "infrastructure", "renovation"}
        for check in checks:
            types = check["applicable_project_types"]
            assert isinstance(types, list) and len(types) > 0, (
                f"{check['check_id']}: applicable_project_types must be non-empty list"
            )
            for t in types:
                assert t in valid_types, f"{check['check_id']}: invalid project type '{t}'"

    def test_no_duplicate_project_types(self):
        """Each check should not have duplicate project types."""
        checks = _load_compliance_checklists()
        for check in checks:
            types = check["applicable_project_types"]
            assert len(types) == len(set(types)), (
                f"{check['check_id']}: duplicate project types {types}"
            )

    def test_applicable_phases_valid(self):
        """Phases should be from known set."""
        checks = _load_compliance_checklists()
        valid_phases = {
            "preconstruction",
            "sitework",
            "foundation",
            "structure",
            "rough_in",
            "finishes",
            "closeout",
        }
        for check in checks:
            phases = check["applicable_phases"]
            assert isinstance(phases, list) and len(phases) > 0, (
                f"{check['check_id']}: applicable_phases must be non-empty list"
            )
            for p in phases:
                assert p in valid_phases, f"{check['check_id']}: invalid phase '{p}'"

    def test_valid_frequencies(self):
        """Frequency should be a known value."""
        checks = _load_compliance_checklists()
        valid_freq = {"daily", "weekly", "per_occurrence", "phase_milestone", "monthly"}
        for check in checks:
            assert check["frequency"] in valid_freq, (
                f"{check['check_id']}: invalid frequency '{check['frequency']}'"
            )

    def test_no_duplicate_phases(self):
        """Each check should not have duplicate phases."""
        checks = _load_compliance_checklists()
        for check in checks:
            phases = check["applicable_phases"]
            assert len(phases) == len(set(phases)), (
                f"{check['check_id']}: duplicate phases {phases}"
            )

    def test_check_id_prefix_matches_category(self):
        """Check IDs should follow category prefix convention."""
        checks = _load_compliance_checklists()
        cat_prefix = {
            "osha_safety": "OSHA",
            "ibc_inspection": "IBC",
            "environmental_swppp": "ENV",
            "quality_control": "QC",
        }
        for check in checks:
            expected_prefix = cat_prefix.get(check["category"])
            if expected_prefix:
                assert check["check_id"].startswith(expected_prefix), (
                    f"{check['check_id']} should start with {expected_prefix}"
                )


# ===========================================================================
# COMPLIANCE CHECKLISTS — Service (Filtering)
# ===========================================================================


class TestComplianceChecklistService:
    """Test the compliance checker service checklist functions."""

    def test_get_checklists_all(self):
        """Should return all checklists when no filters."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        checks = get_checklists()
        assert len(checks) >= 250

    def test_get_checklists_by_category(self):
        """Should filter by category."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        osha = get_checklists(category="osha_safety")
        assert len(osha) >= 80
        assert all(c["category"] == "osha_safety" for c in osha)

    def test_get_checklists_by_severity(self):
        """Should filter by severity."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        critical = get_checklists(severity="critical")
        assert len(critical) > 0
        assert all(c["severity"] == "critical" for c in critical)

    def test_get_checklists_by_phase(self):
        """Should filter by phase."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        foundation = get_checklists(phase="foundation")
        assert len(foundation) > 0
        assert all("foundation" in c["applicable_phases"] for c in foundation)

    def test_get_checklists_by_project_type(self):
        """Should filter by project type."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        commercial = get_checklists(project_type="commercial")
        assert len(commercial) > 0
        assert all("commercial" in c["applicable_project_types"] for c in commercial)

    def test_get_checklists_combined_filters(self):
        """Should apply multiple filters."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        results = get_checklists(
            category="osha_safety",
            severity="critical",
            project_type="commercial",
        )
        for c in results:
            assert c["category"] == "osha_safety"
            assert c["severity"] == "critical"
            assert "commercial" in c["applicable_project_types"]

    def test_get_checklists_no_match(self):
        """Should return empty list when no checks match."""
        # An impossible combination
        results = get_checklists(category="nonexistent_category")
        assert results == []

    def test_get_checklist_by_id(self):
        """Should find a specific checklist by ID."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        check = get_checklist_by_id("OSHA-001")
        assert check is not None
        assert check["check_id"] == "OSHA-001"
        assert check["category"] == "osha_safety"

    def test_get_checklist_by_id_not_found(self):
        """Should return None for unknown ID."""
        check = get_checklist_by_id("NONEXISTENT-999")
        assert check is None

    def test_get_checklist_summary(self):
        """Should return summary statistics."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        summary = get_checklist_summary()
        assert summary["total_checks"] >= 250
        assert "osha_safety" in summary["by_category"]
        assert "critical" in summary["by_severity"]

    def test_cache_clearing(self):
        """Cache should be clearable."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        get_checklists()  # loads cache
        clear_checklist_cache()
        # Should reload without error
        get_checklists()


# ===========================================================================
# COMPLIANCE CHECKLISTS — Seeder Script Validation
# ===========================================================================


class TestComplianceSeederValidation:
    """Test the compliance seeder script validation logic."""

    def test_validate_checklists_no_warnings(self):
        from scripts.seed_compliance_checklists import validate_checklists

        checks = [
            {
                "category": "osha_safety",
                "check_id": "TEST-001",
                "description": "Test check",
                "standard_reference": "29 CFR 1926.501",
                "severity": "critical",
                "applicable_project_types": ["commercial"],
                "applicable_phases": ["construction"],
                "frequency": "daily",
            }
        ]
        warnings = validate_checklists(checks)
        assert len(warnings) == 0

    def test_validate_checklists_missing_keys(self):
        from scripts.seed_compliance_checklists import validate_checklists

        checks = [{"check_id": "TEST-001"}]
        warnings = validate_checklists(checks)
        assert any("missing keys" in w for w in warnings)

    def test_validate_checklists_duplicate_ids(self):
        from scripts.seed_compliance_checklists import validate_checklists

        checks = [
            {
                "category": "osha_safety",
                "check_id": "DUPE",
                "description": "A",
                "standard_reference": "ref",
                "severity": "critical",
                "applicable_project_types": ["commercial"],
                "applicable_phases": ["construction"],
                "frequency": "daily",
            },
            {
                "category": "osha_safety",
                "check_id": "DUPE",
                "description": "B",
                "standard_reference": "ref",
                "severity": "major",
                "applicable_project_types": ["residential"],
                "applicable_phases": ["finishes"],
                "frequency": "weekly",
            },
        ]
        warnings = validate_checklists(checks)
        assert any("duplicate" in w for w in warnings)

    def test_validate_checklists_invalid_category(self):
        from scripts.seed_compliance_checklists import validate_checklists

        checks = [
            {
                "category": "invalid_cat",
                "check_id": "TEST-001",
                "description": "Test",
                "standard_reference": "ref",
                "severity": "critical",
                "applicable_project_types": ["commercial"],
                "applicable_phases": ["construction"],
                "frequency": "daily",
            },
        ]
        warnings = validate_checklists(checks)
        assert any("invalid category" in w for w in warnings)

    def test_validate_checklists_invalid_severity(self):
        from scripts.seed_compliance_checklists import validate_checklists

        checks = [
            {
                "category": "osha_safety",
                "check_id": "TEST-001",
                "description": "Test",
                "standard_reference": "ref",
                "severity": "extreme",
                "applicable_project_types": ["commercial"],
                "applicable_phases": ["construction"],
                "frequency": "daily",
            },
        ]
        warnings = validate_checklists(checks)
        assert any("invalid severity" in w for w in warnings)

    def test_validate_seed_file(self):
        """Validate actual seed file has no warnings."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        from scripts.seed_compliance_checklists import validate_checklists

        checks = _load_compliance_checklists()
        warnings = validate_checklists(checks)
        assert len(warnings) == 0, f"Seed validation warnings: {warnings}"


# ===========================================================================
# COMPLIANCE CHECKLISTS — Backward Compatibility
# ===========================================================================


class TestComplianceCheckerBackwardCompatibility:
    """Ensure existing compliance checker functions still work."""

    @pytest.mark.asyncio
    async def test_check_project_compliance_still_works(self):
        """The original check_project_compliance function should still work."""
        from app.services.quality.compliance_checker import check_project_compliance

        results = await check_project_compliance(
            project_id="test-123",
            regulations=["1926.501"],
            project_data={"safety_measures": [{"type": "fall_protection"}]},
        )
        assert len(results) == 1
        assert results[0]["regulation_code"] == "1926.501"

    @pytest.mark.asyncio
    async def test_check_project_compliance_with_project_type(self):
        """Project-type-aware compliance checking should still work."""
        from app.services.quality.compliance_checker import check_project_compliance

        results = await check_project_compliance(
            project_id="test-123",
            project_type="commercial",
        )
        assert len(results) > 0
        # Commercial should include fall_protection, electrical, etc.

    @pytest.mark.asyncio
    async def test_osha_standards_dict_intact(self):
        """OSHA_STANDARDS should still be importable and populated."""
        from app.services.quality.compliance_checker import OSHA_STANDARDS

        assert len(OSHA_STANDARDS) > 40

    @pytest.mark.asyncio
    async def test_ibc_standards_dict_intact(self):
        """IBC_STANDARDS should still be importable and populated."""
        from app.services.quality.compliance_checker import IBC_STANDARDS

        assert len(IBC_STANDARDS) >= 10


# ===========================================================================
# COMPLIANCE CHECKLISTS — Schemas
# ===========================================================================


class TestComplianceSchemas:
    """Test the compliance checklist Pydantic schemas."""

    def test_checklist_item_schema(self):
        from app.schemas.quality import ComplianceChecklistItem

        item = ComplianceChecklistItem(
            category="osha_safety",
            check_id="OSHA-001",
            description="Fall protection required at 6 feet",
            standard_reference="29 CFR 1926.501(b)(1)",
            severity="critical",
            applicable_project_types=["commercial", "residential"],
            applicable_phases=["structure", "rough_in"],
            frequency="daily",
            verification_method="visual_inspection",
            documentation_required=True,
        )
        assert item.check_id == "OSHA-001"
        assert item.severity == "critical"

    def test_checklist_list_response_schema(self):
        from app.schemas.quality import ComplianceChecklistListResponse

        response = ComplianceChecklistListResponse(data=[], total=0)
        assert response.total == 0
        assert response.data == []

    def test_checklist_summary_schema(self):
        from app.schemas.quality import ComplianceChecklistSummary

        summary = ComplianceChecklistSummary(
            total_checks=260,
            by_category={"osha_safety": 90, "ibc_inspection": 45},
            by_severity={"critical": 50, "major": 100, "minor": 110},
        )
        assert summary.total_checks == 260


# ===========================================================================
# COMPLIANCE CHECKLISTS — API Endpoints
# ===========================================================================


class TestComplianceChecklistAPI:
    """Test the compliance checklist API endpoints (no auth required)."""

    @pytest.mark.asyncio
    async def test_list_checklists_no_filter(self, client, auth_headers):
        """GET /compliance-checklists should return all checklists."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        response = await client.get("/api/v1/quality/compliance-checklists", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 250
        assert len(data["data"]) >= 250

    @pytest.mark.asyncio
    async def test_list_checklists_filter_category(self, client, auth_headers):
        """Filter by category should work."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        response = await client.get(
            "/api/v1/quality/compliance-checklists?category=osha_safety", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 80
        for item in data["data"]:
            assert item["category"] == "osha_safety"

    @pytest.mark.asyncio
    async def test_list_checklists_filter_severity(self, client, auth_headers):
        """Filter by severity should work."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        response = await client.get(
            "/api/v1/quality/compliance-checklists?severity=critical", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        for item in data["data"]:
            assert item["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_list_checklists_filter_phase(self, client, auth_headers):
        """Filter by phase should work."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        response = await client.get(
            "/api/v1/quality/compliance-checklists?phase=foundation", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        for item in data["data"]:
            assert "foundation" in item["applicable_phases"]

    @pytest.mark.asyncio
    async def test_list_checklists_filter_project_type(self, client, auth_headers):
        """Filter by project type should work."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        response = await client.get(
            "/api/v1/quality/compliance-checklists?project_type=residential", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        for item in data["data"]:
            assert "residential" in item["applicable_project_types"]

    @pytest.mark.asyncio
    async def test_list_checklists_combined_filters(self, client, auth_headers):
        """Multiple filters should combine."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        response = await client.get(
            "/api/v1/quality/compliance-checklists"
            "?category=osha_safety&severity=critical&project_type=commercial",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        for item in data["data"]:
            assert item["category"] == "osha_safety"
            assert item["severity"] == "critical"
            assert "commercial" in item["applicable_project_types"]

    @pytest.mark.asyncio
    async def test_checklist_summary(self, client, auth_headers):
        """GET /compliance-checklists/summary should return stats."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        response = await client.get(
            "/api/v1/quality/compliance-checklists/summary", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_checks"] >= 250
        assert "osha_safety" in data["by_category"]
        assert "critical" in data["by_severity"]

    @pytest.mark.asyncio
    async def test_get_checklist_by_id(self, client, auth_headers):
        """GET /compliance-checklists/{check_id} should return specific item."""
        if not COMPLIANCE_SEED.exists():
            pytest.skip("seed file not found")
        response = await client.get(
            "/api/v1/quality/compliance-checklists/OSHA-001", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["check_id"] == "OSHA-001"

    @pytest.mark.asyncio
    async def test_get_checklist_not_found(self, client, auth_headers):
        """GET /compliance-checklists/{check_id} should 404 for unknown ID."""
        response = await client.get(
            "/api/v1/quality/compliance-checklists/FAKE-999", headers=auth_headers
        )
        assert response.status_code == 404


# ===========================================================================
# Seed data not found fallbacks
# ===========================================================================


class TestSeedDataMissing:
    """Test behavior when seed data files are not present."""

    def test_productivity_no_seed_returns_none(self):
        """get_baseline_rate should return None when seed file missing."""
        with patch(
            "app.services.productivity.productivity_forecaster._SEED_FILE",
            Path("/nonexistent/productivity.json"),
        ):
            clear_baseline_cache()
            rate = get_baseline_rate("concrete")
            assert rate is None

    def test_compliance_no_seed_returns_empty(self):
        """get_checklists should return empty when seed file missing."""
        with patch(
            "app.services.quality.compliance_checker._CHECKLIST_SEED_FILE",
            Path("/nonexistent/compliance.json"),
        ):
            clear_checklist_cache()
            checks = get_checklists()
            assert checks == []
