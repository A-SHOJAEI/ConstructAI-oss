"""Tests for correlated Monte Carlo simulation and industry benchmarks.

Validates:
- Correlation matrix construction from WBS/predecessors/resources
- Correlated vs uncorrelated P10-P90 range widening (15-30%)
- Industry benchmark classification and bounds
- EAC forecaster with benchmark fallback
- Cost simulation with DURATION_UNCERTAINTY
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest

from app.services.controls.industry_benchmarks import (
    CHANGE_ORDER_RATES,
    CPI_BENCHMARKS,
    DURATION_UNCERTAINTY,
    classify_activity,
    get_duration_bounds,
)
from app.services.controls.monte_carlo_schedule import (
    _build_correlation_matrix,
    run_cost_risk_simulation,
    run_schedule_risk_simulation,
)

# ---------------------------------------------------------------------------
# Test data: activities with WBS and resource structure for correlations
# ---------------------------------------------------------------------------

CORRELATED_ACTIVITIES = [
    {
        "id": "A",
        "name": "Site Preparation",
        "duration_days": 15,
        "predecessors": [],
        "wbs_path": "Project/Phase1",
        "resource_assignments": [{"resource_name": "Excavation Crew"}],
    },
    {
        "id": "B",
        "name": "Foundation Excavation",
        "duration_days": 20,
        "predecessors": ["A"],
        "wbs_path": "Project/Phase1",
        "resource_assignments": [{"resource_name": "Excavation Crew"}],
    },
    {
        "id": "C",
        "name": "Foundation Pour",
        "duration_days": 10,
        "predecessors": ["B"],
        "wbs_path": "Project/Phase1",
        "resource_assignments": [{"resource_name": "Concrete Crew"}],
    },
    {
        "id": "D",
        "name": "Structural Steel",
        "duration_days": 30,
        "predecessors": ["C"],
        "wbs_path": "Project/Phase2",
        "resource_assignments": [{"resource_name": "Steel Crew"}],
    },
    {
        "id": "E",
        "name": "MEP Rough-in",
        "duration_days": 25,
        "predecessors": ["C"],
        "wbs_path": "Project/Phase2",
        "resource_assignments": [{"resource_name": "MEP Crew"}],
    },
    {
        "id": "F",
        "name": "Building Enclosure",
        "duration_days": 20,
        "predecessors": ["D"],
        "wbs_path": "Project/Phase3",
        "resource_assignments": [{"resource_name": "Envelope Crew"}],
    },
    {
        "id": "G",
        "name": "Interior Finishes",
        "duration_days": 25,
        "predecessors": ["E", "F"],
        "wbs_path": "Project/Phase3",
        "resource_assignments": [{"resource_name": "Finish Crew"}],
    },
    {
        "id": "H",
        "name": "Commissioning",
        "duration_days": 10,
        "predecessors": ["G"],
        "wbs_path": "Project/Phase4",
        "resource_assignments": [{"resource_name": "Commissioning Team"}],
    },
]


# ---------------------------------------------------------------------------
# Industry Benchmarks Tests
# ---------------------------------------------------------------------------


class TestIndustryBenchmarks:
    def test_cpi_benchmarks_all_project_types(self):
        expected_types = {
            "commercial",
            "infrastructure",
            "residential",
            "institutional",
            "healthcare",
            "industrial",
        }
        assert set(CPI_BENCHMARKS.keys()) == expected_types
        for _ptype, data in CPI_BENCHMARKS.items():
            assert "mean" in data
            assert "std" in data
            assert "p10" in data
            assert "p50" in data
            assert "p90" in data
            assert data["p10"] < data["p50"] < data["p90"]

    def test_duration_uncertainty_all_categories(self):
        expected = {
            "site_work",
            "foundations",
            "structural_steel",
            "concrete_structure",
            "mep_rough_in",
            "building_enclosure",
            "interior_finishes",
            "commissioning",
            "default",
        }
        assert set(DURATION_UNCERTAINTY.keys()) == expected
        for _cat, data in DURATION_UNCERTAINTY.items():
            assert data["optimistic_pct"] < 0  # faster than planned
            assert data["pessimistic_pct"] > 0  # slower than planned

    def test_change_order_rates(self):
        assert "commercial" in CHANGE_ORDER_RATES
        assert "healthcare" in CHANGE_ORDER_RATES
        # Healthcare has higher change order rate than residential
        assert (
            CHANGE_ORDER_RATES["healthcare"]["mean_pct"]
            > CHANGE_ORDER_RATES["residential"]["mean_pct"]
        )


class TestActivityClassification:
    def test_site_work_keywords(self):
        assert classify_activity("Site Grading and Excavation") == "site_work"
        assert classify_activity("Demolition of existing structure") == "site_work"
        assert classify_activity("Storm drain installation") == "site_work"

    def test_foundation_keywords(self):
        assert classify_activity("Foundation Footing Pour") == "foundations"
        assert classify_activity("Pile driving") == "foundations"
        assert classify_activity("Retaining wall construction") == "foundations"

    def test_structural_steel_keywords(self):
        assert classify_activity("Structural Steel Erection") == "structural_steel"
        assert classify_activity("Steel framing level 3") == "structural_steel"

    def test_concrete_keywords(self):
        assert classify_activity("Concrete pour level 2") == "concrete_structure"
        assert classify_activity("Formwork and rebar") == "concrete_structure"
        assert classify_activity("CMU block wall") == "concrete_structure"

    def test_mep_keywords(self):
        assert classify_activity("HVAC ductwork installation") == "mep_rough_in"
        assert classify_activity("Electrical conduit rough-in") == "mep_rough_in"
        assert classify_activity("Plumbing piping") == "mep_rough_in"
        assert classify_activity("Fire protection sprinkler") == "mep_rough_in"

    def test_enclosure_keywords(self):
        assert classify_activity("Curtain wall installation") == "building_enclosure"
        assert classify_activity("Roofing and waterproofing") == "building_enclosure"

    def test_finishes_keywords(self):
        assert classify_activity("Drywall and painting") == "interior_finishes"
        assert classify_activity("Floor tile installation") == "interior_finishes"
        assert classify_activity("Millwork and casework") == "interior_finishes"

    def test_commissioning_keywords(self):
        assert classify_activity("Commissioning and startup") == "commissioning"
        assert classify_activity("Testing and balancing") == "commissioning"
        assert classify_activity("Punch list and closeout") == "commissioning"

    def test_default_category(self):
        assert classify_activity("Random activity XYZ") == "default"
        assert classify_activity("") == "default"

    def test_wbs_code_fallback(self):
        assert classify_activity("Activity A", wbs_code="Foundation/Footing") == "foundations"

    def test_get_duration_bounds(self):
        opt, pess = get_duration_bounds(100, category="site_work")
        # site_work: optimistic_pct=-0.15, pessimistic_pct=0.50
        assert opt == pytest.approx(85.0)
        assert pess == pytest.approx(150.0)

    def test_get_duration_bounds_by_name(self):
        opt, pess = get_duration_bounds(20, name="Foundation pour")
        # foundations: optimistic_pct=-0.10, pessimistic_pct=0.30
        assert opt == pytest.approx(18.0)
        assert pess == pytest.approx(26.0)

    def test_get_duration_bounds_default(self):
        opt, pess = get_duration_bounds(50, name="Unknown task")
        # default: optimistic_pct=-0.20, pessimistic_pct=0.20
        assert opt == pytest.approx(40.0)
        assert pess == pytest.approx(60.0)

    def test_minimum_optimistic_is_1(self):
        opt, _ = get_duration_bounds(1, category="site_work")
        assert opt >= 1


# ---------------------------------------------------------------------------
# Correlation Matrix Tests
# ---------------------------------------------------------------------------


class TestCorrelationMatrix:
    def test_diagonal_is_one(self):
        act_params = [
            {
                "id": "A",
                "name": "A",
                "predecessors": [],
                "optimistic": 8,
                "most_likely": 10,
                "pessimistic": 15,
            },
            {
                "id": "B",
                "name": "B",
                "predecessors": ["A"],
                "optimistic": 16,
                "most_likely": 20,
                "pessimistic": 30,
            },
        ]
        corr = _build_correlation_matrix(act_params)
        assert corr.shape == (2, 2)
        assert corr[0, 0] == pytest.approx(1.0)
        assert corr[1, 1] == pytest.approx(1.0)

    def test_symmetric(self):
        act_params = [
            {
                "id": "A",
                "name": "A",
                "predecessors": [],
                "optimistic": 8,
                "most_likely": 10,
                "pessimistic": 15,
            },
            {
                "id": "B",
                "name": "B",
                "predecessors": ["A"],
                "optimistic": 16,
                "most_likely": 20,
                "pessimistic": 30,
            },
            {
                "id": "C",
                "name": "C",
                "predecessors": ["A"],
                "optimistic": 8,
                "most_likely": 10,
                "pessimistic": 12,
            },
        ]
        corr = _build_correlation_matrix(act_params)
        assert np.allclose(corr, corr.T)

    def test_predecessor_correlation(self):
        act_params = [
            {
                "id": "A",
                "name": "A",
                "predecessors": [],
                "optimistic": 8,
                "most_likely": 10,
                "pessimistic": 15,
            },
            {
                "id": "B",
                "name": "B",
                "predecessors": ["A"],
                "optimistic": 16,
                "most_likely": 20,
                "pessimistic": 30,
            },
        ]
        corr = _build_correlation_matrix(act_params)
        # Direct predecessor-successor should have correlation >= 0.6
        assert corr[0, 1] >= 0.6

    def test_same_wbs_parent_correlation(self):
        act_params = [
            {
                "id": "A",
                "name": "A",
                "predecessors": [],
                "wbs_path": "Project/Phase1",
                "optimistic": 8,
                "most_likely": 10,
                "pessimistic": 15,
            },
            {
                "id": "B",
                "name": "B",
                "predecessors": [],
                "wbs_path": "Project/Phase1",
                "optimistic": 16,
                "most_likely": 20,
                "pessimistic": 30,
            },
        ]
        corr = _build_correlation_matrix(act_params)
        # Same WBS parent → correlation 0.7
        assert corr[0, 1] >= 0.7

    def test_shared_resource_correlation(self):
        act_params = [
            {
                "id": "A",
                "name": "A",
                "predecessors": [],
                "resource_assignments": [{"resource_name": "Concrete Crew"}],
                "optimistic": 8,
                "most_likely": 10,
                "pessimistic": 15,
            },
            {
                "id": "B",
                "name": "B",
                "predecessors": [],
                "resource_assignments": [{"resource_name": "Concrete Crew"}],
                "optimistic": 16,
                "most_likely": 20,
                "pessimistic": 30,
            },
        ]
        corr = _build_correlation_matrix(act_params)
        assert corr[0, 1] >= 0.5

    def test_different_wbs_branch_correlation(self):
        act_params = [
            {
                "id": "A",
                "name": "A",
                "predecessors": [],
                "wbs_path": "ProjectA/Phase1",
                "optimistic": 8,
                "most_likely": 10,
                "pessimistic": 15,
            },
            {
                "id": "B",
                "name": "B",
                "predecessors": [],
                "wbs_path": "ProjectB/Phase2",
                "optimistic": 16,
                "most_likely": 20,
                "pessimistic": 30,
            },
        ]
        corr = _build_correlation_matrix(act_params)
        # Different WBS branches → 0.15
        assert corr[0, 1] == pytest.approx(0.15, abs=0.05)

    def test_positive_semidefinite(self):
        # Build from the full 8-activity schedule
        act_params = [
            {
                "id": a["id"],
                "name": a["name"],
                "predecessors": a["predecessors"],
                "wbs_path": a.get("wbs_path"),
                "resource_assignments": a.get("resource_assignments", []),
                "optimistic": int(a["duration_days"] * 0.8),
                "most_likely": a["duration_days"],
                "pessimistic": int(a["duration_days"] * 1.5),
            }
            for a in CORRELATED_ACTIVITIES
        ]
        corr = _build_correlation_matrix(act_params)
        # All eigenvalues should be positive (PSD)
        eigenvalues = np.linalg.eigvalsh(corr)
        assert all(ev >= -1e-7 for ev in eigenvalues)


# ---------------------------------------------------------------------------
# Correlated vs Uncorrelated Simulation
# ---------------------------------------------------------------------------


class TestCorrelatedSimulation:
    @pytest.mark.asyncio
    async def test_correlated_wider_range(self):
        """Correlated P10-P90 range should be wider than uncorrelated."""
        uncorr = await run_schedule_risk_simulation(
            activities=CORRELATED_ACTIVITIES,
            num_iterations=5000,
            seed=42,
            use_correlations=False,
        )
        corr = await run_schedule_risk_simulation(
            activities=CORRELATED_ACTIVITIES,
            num_iterations=5000,
            seed=42,
            use_correlations=True,
        )

        uncorr_range = uncorr["p90_duration"] - uncorr["p10_duration"]
        corr_range = corr["p90_duration"] - corr["p10_duration"]

        # Correlated range should be wider
        assert corr_range >= uncorr_range, (
            f"Correlated range ({corr_range}) should be >= uncorrelated ({uncorr_range})"
        )

    @pytest.mark.asyncio
    async def test_correlation_impact_summary(self):
        """When use_correlations=True, output should include correlation_impact."""
        result = await run_schedule_risk_simulation(
            activities=CORRELATED_ACTIVITIES,
            num_iterations=3000,
            seed=42,
            use_correlations=True,
        )
        assert "correlation_impact" in result
        impact = result["correlation_impact"]
        assert "uncorrelated_p10" in impact
        assert "uncorrelated_p90" in impact
        assert "correlated_p10" in impact
        assert "correlated_p90" in impact
        assert "range_increase_pct" in impact
        assert "uncorrelated_range" in impact
        assert "correlated_range" in impact

    @pytest.mark.asyncio
    async def test_no_correlation_impact_when_disabled(self):
        """When use_correlations=False, no correlation_impact in output."""
        result = await run_schedule_risk_simulation(
            activities=CORRELATED_ACTIVITIES,
            num_iterations=1000,
            seed=42,
            use_correlations=False,
        )
        assert "correlation_impact" not in result

    @pytest.mark.asyncio
    async def test_criticality_index_present(self):
        """Both correlated and uncorrelated should return criticality_index."""
        result = await run_schedule_risk_simulation(
            activities=CORRELATED_ACTIVITIES,
            num_iterations=1000,
            seed=42,
            use_correlations=False,
        )
        assert "criticality_index" in result
        ci = result["criticality_index"]
        # All activities should have an index between 0 and 1
        for _act_id, idx in ci.items():
            assert 0 <= idx <= 1.0

    @pytest.mark.asyncio
    async def test_variance_contributions_present(self):
        """Output should include variance contribution analysis."""
        result = await run_schedule_risk_simulation(
            activities=CORRELATED_ACTIVITIES,
            num_iterations=1000,
            seed=42,
            use_correlations=False,
        )
        assert "variance_contributions" in result
        vc = result["variance_contributions"]
        assert len(vc) == len(CORRELATED_ACTIVITIES)
        # Should be sorted by variance_contribution_pct descending
        for i in range(len(vc) - 1):
            assert vc[i]["variance_contribution_pct"] >= vc[i + 1]["variance_contribution_pct"]

    @pytest.mark.asyncio
    async def test_deterministic_with_seed_correlated(self):
        """Correlated simulation should be deterministic with same seed."""
        r1 = await run_schedule_risk_simulation(
            activities=CORRELATED_ACTIVITIES,
            num_iterations=1000,
            seed=123,
            use_correlations=True,
        )
        r2 = await run_schedule_risk_simulation(
            activities=CORRELATED_ACTIVITIES,
            num_iterations=1000,
            seed=123,
            use_correlations=True,
        )
        assert r1["p50_duration"] == r2["p50_duration"]
        assert r1["mean_duration"] == r2["mean_duration"]

    @pytest.mark.asyncio
    async def test_backward_compatibility(self):
        """Existing API without use_correlations should still work."""
        from tests.fixtures.sample_evm_data import SAMPLE_SCHEDULE_ACTIVITIES

        result = await run_schedule_risk_simulation(
            activities=SAMPLE_SCHEDULE_ACTIVITIES,
            num_iterations=500,
            seed=42,
        )
        assert result["p50_duration"] > 0
        assert result["use_correlations"] is False

    @pytest.mark.asyncio
    async def test_single_activity_no_crash(self):
        """Correlated mode with single activity should not crash."""
        result = await run_schedule_risk_simulation(
            activities=[
                {
                    "id": "X",
                    "name": "Solo Task",
                    "duration_days": 10,
                    "predecessors": [],
                }
            ],
            num_iterations=500,
            seed=42,
            use_correlations=True,
        )
        assert result["p50_duration"] > 0


# ---------------------------------------------------------------------------
# EAC Forecaster with Industry Benchmarks
# ---------------------------------------------------------------------------


class TestEACWithBenchmarks:
    @pytest.mark.asyncio
    async def test_benchmark_fallback_no_history(self):
        """With no historical data but project_type, should use benchmarks."""
        from app.services.controls.eac_forecaster import forecast_eac

        result = await forecast_eac(
            bac=Decimal("1000000"),
            ev=Decimal("300000"),
            ac=Decimal("350000"),
            spi=Decimal("0.90"),
            cpi=Decimal("0.857"),
            method="cpi",
            project_type="commercial",
        )
        assert result["confidence_low"] < result["eac_value"]
        assert result["confidence_high"] > result["eac_value"]
        # CI should be tighter than phase-aware ±25% (early phase)
        # because benchmark std=0.15 is more informative
        margin_pct = (result["confidence_high"] - result["confidence_low"]) / (
            2 * result["eac_value"]
        )
        assert margin_pct < Decimal("0.25")

    @pytest.mark.asyncio
    async def test_benchmark_vs_no_benchmark(self):
        """With project_type, CI should differ from no project_type."""
        from app.services.controls.eac_forecaster import forecast_eac

        with_bm = await forecast_eac(
            bac=Decimal("500000"),
            ev=Decimal("50000"),
            ac=Decimal("60000"),
            spi=Decimal("0.85"),
            cpi=Decimal("0.833"),
            method="cpi",
            project_type="healthcare",
        )
        without_bm = await forecast_eac(
            bac=Decimal("500000"),
            ev=Decimal("50000"),
            ac=Decimal("60000"),
            spi=Decimal("0.85"),
            cpi=Decimal("0.833"),
            method="cpi",
        )
        # Same EAC value, different CI
        assert with_bm["eac_value"] == without_bm["eac_value"]
        assert with_bm["confidence_low"] != without_bm["confidence_low"]

    @pytest.mark.asyncio
    async def test_sufficient_history_ignores_benchmark(self):
        """With >= 5 CPI values, benchmark should not be used."""
        from app.services.controls.eac_forecaster import forecast_eac

        cpi_values = [
            Decimal("0.95"),
            Decimal("0.92"),
            Decimal("0.88"),
            Decimal("0.90"),
            Decimal("0.93"),
        ]
        with_type = await forecast_eac(
            bac=Decimal("1000000"),
            ev=Decimal("500000"),
            ac=Decimal("540000"),
            spi=Decimal("0.95"),
            cpi=Decimal("0.926"),
            method="cpi",
            historical_cpi_values=cpi_values,
            project_type="commercial",
        )
        without_type = await forecast_eac(
            bac=Decimal("1000000"),
            ev=Decimal("500000"),
            ac=Decimal("540000"),
            spi=Decimal("0.95"),
            cpi=Decimal("0.926"),
            method="cpi",
            historical_cpi_values=cpi_values,
        )
        # With sufficient history, project_type shouldn't matter
        assert with_type["confidence_low"] == without_type["confidence_low"]
        assert with_type["confidence_high"] == without_type["confidence_high"]

    @pytest.mark.asyncio
    async def test_all_method_passes_project_type(self):
        """Method='all' should pass project_type to all sub-methods."""
        from app.services.controls.eac_forecaster import forecast_eac

        result = await forecast_eac(
            bac=Decimal("1000000"),
            ev=Decimal("100000"),
            ac=Decimal("120000"),
            spi=Decimal("0.80"),
            cpi=Decimal("0.833"),
            method="all",
            project_type="infrastructure",
        )
        assert "all_methods" in result
        # Each sub-method should have CI values
        for _method_name, method_result in result["all_methods"].items():
            assert "confidence_low" in method_result
            assert "confidence_high" in method_result

    @pytest.mark.asyncio
    async def test_unknown_project_type_falls_back(self):
        """Unknown project_type should fall back to phase-aware defaults."""
        from app.services.controls.eac_forecaster import forecast_eac

        result = await forecast_eac(
            bac=Decimal("500000"),
            ev=Decimal("50000"),
            ac=Decimal("60000"),
            spi=Decimal("0.85"),
            cpi=Decimal("0.833"),
            method="cpi",
            project_type="space_station",  # not in benchmarks
        )
        # Should still work, using phase-aware defaults
        assert result["confidence_low"] < result["eac_value"]


# ---------------------------------------------------------------------------
# Cost Simulation with Industry Uncertainty
# ---------------------------------------------------------------------------


class TestCostSimulationIndustryUncertainty:
    @pytest.mark.asyncio
    async def test_industry_uncertainty_applied(self):
        """Cost simulation should use industry bounds when no explicit bounds given."""
        activities = [
            {"id": "1", "name": "Foundation Excavation", "estimated_cost": 100000},
            {"id": "2", "name": "Structural Steel Erection", "estimated_cost": 200000},
            {"id": "3", "name": "HVAC Ductwork", "estimated_cost": 150000},
        ]
        result = await run_cost_risk_simulation(
            activities=activities,
            num_iterations=2000,
            seed=42,
            use_industry_uncertainty=True,
        )
        assert result["p50_cost"] > 0
        assert result["p10_cost"] < result["p50_cost"]
        assert result["p90_cost"] > result["p50_cost"]

    @pytest.mark.asyncio
    async def test_explicit_bounds_override_industry(self):
        """Explicit cost_optimistic/cost_pessimistic should override industry defaults."""
        activities = [
            {
                "id": "1",
                "name": "Foundation Excavation",
                "estimated_cost": 100000,
                "cost_optimistic": 90000,
                "cost_pessimistic": 120000,
            },
        ]
        result = await run_cost_risk_simulation(
            activities=activities,
            num_iterations=2000,
            seed=42,
            use_industry_uncertainty=True,
        )
        # P90 should be near 120000, not the wider industry bounds
        assert result["p90_cost"] <= Decimal("125000")

    @pytest.mark.asyncio
    async def test_industry_vs_blanket_difference(self):
        """Industry uncertainty should produce different ranges than blanket ±20%."""
        activities = [
            {"id": "1", "name": "Site Grading", "estimated_cost": 100000},
        ]

        # With industry uncertainty (site_work: -15% / +50%)
        with_industry = await run_cost_risk_simulation(
            activities=activities,
            num_iterations=3000,
            seed=42,
            use_industry_uncertainty=True,
        )

        # Without industry uncertainty (blanket -15% / +30%)
        without_industry = await run_cost_risk_simulation(
            activities=activities,
            num_iterations=3000,
            seed=42,
            use_industry_uncertainty=False,
        )

        # Site work has wider pessimistic (+50% vs +30%)
        # so P90 should be higher with industry uncertainty
        assert with_industry["p90_cost"] > without_industry["p90_cost"]

    @pytest.mark.asyncio
    async def test_backward_compatible_cost_simulation(self):
        """run_cost_risk_simulation without new params should still work."""
        activities = [
            {"id": "1", "name": "Task A", "estimated_cost": 50000},
            {"id": "2", "name": "Task B", "estimated_cost": 75000},
        ]
        result = await run_cost_risk_simulation(
            activities=activities,
            num_iterations=1000,
            seed=42,
        )
        assert result["num_iterations"] == 1000
        assert result["p50_cost"] > 0
