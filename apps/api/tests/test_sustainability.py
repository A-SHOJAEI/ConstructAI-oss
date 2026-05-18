"""Tests for LEED v5 sustainability tracking and embodied carbon calculations.

Covers:
- Carbon factor lookup (exact, division prefix, missing)
- Embodied carbon calculation (single item, multiple divisions, unit conversion)
- LEED credit evaluation (each credit category, thresholds)
- Salvaged material tracking
- Recycled content tracking
- Sustainability dashboard integration
- API endpoint structure
"""

from __future__ import annotations

from app.services.estimating.carbon_database import (
    CARBON_FACTORS,
    LEED_V5_CREDITS,
    EmbodiedCarbonResult,
    SustainabilityDashboard,
    _get_unit_conversion,
    _normalize_unit,
    calculate_embodied_carbon,
    evaluate_leed_credits,
    get_carbon_factor,
)

# ---------------------------------------------------------------------------
# TestCarbonFactorLookup
# ---------------------------------------------------------------------------


class TestCarbonFactorLookup:
    """Tests for get_carbon_factor() lookup logic."""

    def test_exact_match_concrete(self):
        """Concrete 4000 psi should return exact factor."""
        factor = get_carbon_factor("03 30 00")
        assert factor is not None
        assert factor.material_name == "Cast-in-place concrete (4000 psi)"
        assert factor.embodied_carbon_kgco2e == 350
        assert factor.unit == "CY"
        assert factor.data_source == "CLF"

    def test_exact_match_structural_steel(self):
        """Structural steel W-shapes should return 1850 kgCO2e/TON."""
        factor = get_carbon_factor("05 12 00")
        assert factor is not None
        assert factor.embodied_carbon_kgco2e == 1850
        assert factor.unit == "TON"

    def test_exact_match_copper_pipe(self):
        """Copper pipe should return 5500 kgCO2e/TON."""
        factor = get_carbon_factor("22 11 00")
        assert factor is not None
        assert factor.embodied_carbon_kgco2e == 5500

    def test_division_prefix_match(self):
        """An unknown code in Division 03 should match a Division 03 factor."""
        factor = get_carbon_factor("03 99 99")
        assert factor is not None
        assert factor.csi_code.startswith("03")

    def test_division_prefix_match_division_05(self):
        """Unknown Division 05 code should match a metals factor."""
        factor = get_carbon_factor("05 99 00")
        assert factor is not None
        assert factor.csi_code.startswith("05")

    def test_no_match_returns_none(self):
        """A code in an unmapped division should return None."""
        factor = get_carbon_factor("99 99 99")
        assert factor is None

    def test_empty_code_returns_none(self):
        """Empty string should return None."""
        factor = get_carbon_factor("")
        assert factor is None

    def test_whitespace_handling(self):
        """Leading/trailing whitespace should be stripped."""
        factor = get_carbon_factor("  03 30 00  ")
        assert factor is not None

    def test_carbon_factors_count(self):
        """Verify we have 60+ carbon factors as specified."""
        assert len(CARBON_FACTORS) >= 60


# ---------------------------------------------------------------------------
# TestEmbodiedCarbonCalc
# ---------------------------------------------------------------------------


class TestEmbodiedCarbonCalc:
    """Tests for calculate_embodied_carbon()."""

    def test_single_concrete_item(self):
        """100 CY of concrete should yield 35,000 kgCO2e."""
        items = [
            {"csi_code": "03 30 00", "quantity": 100, "unit": "CY", "description": "Concrete slab"}
        ]
        result = calculate_embodied_carbon(items)
        assert result.total_kgco2e == 35000.0
        assert result.total_tonco2e == 35.0
        assert result.item_count == 1
        assert len(result.unmatched_items) == 0

    def test_multiple_divisions(self):
        """Items across divisions should be grouped correctly."""
        items = [
            {"csi_code": "03 30 00", "quantity": 50, "unit": "CY"},
            {"csi_code": "05 12 00", "quantity": 10, "unit": "TON"},
            {"csi_code": "09 21 00", "quantity": 5000, "unit": "SF"},
        ]
        result = calculate_embodied_carbon(items)
        assert result.item_count == 3
        assert "Concrete" in result.by_division
        assert "Metals" in result.by_division
        assert "Finishes" in result.by_division
        # 50*350 + 10*1850 + 5000*12 = 17500 + 18500 + 60000 = 96000
        assert result.total_kgco2e == 96000.0

    def test_carbon_per_sf(self):
        """Carbon per SF should be calculated when gross_area_sf is provided."""
        items = [
            {"csi_code": "03 30 00", "quantity": 100, "unit": "CY"},
        ]
        result = calculate_embodied_carbon(items, gross_area_sf=10000)
        assert result.carbon_per_sf is not None
        assert result.carbon_per_sf == 3.5  # 35000 / 10000

    def test_no_gross_area(self):
        """Carbon per SF should be None when gross_area_sf is not provided."""
        items = [{"csi_code": "03 30 00", "quantity": 10, "unit": "CY"}]
        result = calculate_embodied_carbon(items)
        assert result.carbon_per_sf is None

    def test_empty_items(self):
        """Empty item list should return zero totals."""
        result = calculate_embodied_carbon([])
        assert result.total_kgco2e == 0.0
        assert result.item_count == 0

    def test_unmatched_items_tracked(self):
        """Items with no matching factor should be listed in unmatched."""
        items = [
            {"csi_code": "99 99 99", "quantity": 10, "unit": "EA", "description": "Unknown item"}
        ]
        result = calculate_embodied_carbon(items)
        assert len(result.unmatched_items) == 1
        assert "99 99 99" in result.unmatched_items[0]

    def test_zero_quantity_skipped(self):
        """Items with zero quantity should be skipped."""
        items = [{"csi_code": "03 30 00", "quantity": 0, "unit": "CY"}]
        result = calculate_embodied_carbon(items)
        assert result.total_kgco2e == 0.0
        assert result.item_count == 0

    def test_unit_normalization(self):
        """Various unit strings should be normalized correctly."""
        assert _normalize_unit("cubic yards") == "CY"
        assert _normalize_unit("sf") == "SF"
        assert _normalize_unit("TON") == "TON"
        assert _normalize_unit("each") == "EA"
        assert _normalize_unit("  sq ft  ") == "SF"

    def test_per_item_breakdown(self):
        """Each matched item should appear in by_item with correct fields."""
        items = [
            {"csi_code": "03 30 00", "quantity": 10, "unit": "CY", "description": "Test concrete"}
        ]
        result = calculate_embodied_carbon(items)
        assert len(result.by_item) == 1
        item = result.by_item[0]
        assert item["csi_code"] == "03 30 00"
        assert item["total_kgco2e"] == 3500.0
        assert item["data_source"] == "CLF"


# ---------------------------------------------------------------------------
# TestLEEDCreditEvaluation
# ---------------------------------------------------------------------------


class TestLEEDCreditEvaluation:
    """Tests for evaluate_leed_credits()."""

    def test_all_credits_evaluated(self):
        """All LEED credits should be evaluated."""
        credits = evaluate_leed_credits(project_data={})
        assert len(credits) == len(LEED_V5_CREDITS)

    def test_no_data_yields_not_achievable(self):
        """With no project data, most credits should be not_achievable."""
        credits = evaluate_leed_credits(project_data={})
        not_achievable = [c for c in credits if c.status == "not_achievable"]
        assert len(not_achievable) >= 10

    def test_mr_c1_high_reduction(self):
        """40% GWP reduction should earn full MR_c1 points."""
        ec = EmbodiedCarbonResult(
            total_kgco2e=100000,
            total_tonco2e=100,
            carbon_per_sf=20.0,  # vs 42.0 baseline = ~52% reduction
            by_division={},
            by_item=[],
            item_count=5,
            unmatched_items=[],
            gross_area_sf=5000,
        )
        credits = evaluate_leed_credits(
            project_data={"type": "commercial"},
            embodied_carbon=ec,
        )
        mr_c1 = next(c for c in credits if c.credit_id == "MR_c1")
        assert mr_c1.status == "achievable"
        assert mr_c1.earned_points == 5

    def test_mr_c1_partial_reduction(self):
        """15% GWP reduction should earn partial MR_c1 points."""
        ec = EmbodiedCarbonResult(
            total_kgco2e=180000,
            total_tonco2e=180,
            carbon_per_sf=35.7,  # vs 42.0 baseline = ~15% reduction
            by_division={},
            by_item=[],
            item_count=5,
            unmatched_items=[],
            gross_area_sf=5000,
        )
        credits = evaluate_leed_credits(
            project_data={"type": "commercial"},
            embodied_carbon=ec,
        )
        mr_c1 = next(c for c in credits if c.credit_id == "MR_c1")
        assert mr_c1.status == "partial"
        assert mr_c1.earned_points >= 2

    def test_mr_c3_recycled_and_salvaged(self):
        """Both recycled content and salvaged materials should earn MR_c3."""
        salvaged = [{"description": "Reclaimed lumber", "cost": 30000}]
        credits = evaluate_leed_credits(
            project_data={"total_material_cost": 500000},
            recycled_content_pct=25.0,
            salvaged_materials=salvaged,
        )
        mr_c3 = next(c for c in credits if c.credit_id == "MR_c3")
        assert mr_c3.earned_points == 2  # 1 for recycled + 1 for salvaged
        assert mr_c3.status == "achievable"

    def test_mr_c5_waste_diversion(self):
        """75% waste diversion should earn 2 points."""
        credits = evaluate_leed_credits(project_data={"waste_diversion_pct": 80})
        mr_c5 = next(c for c in credits if c.credit_id == "MR_c5")
        assert mr_c5.earned_points == 2

    def test_ea_c1_energy_reduction(self):
        """30% energy reduction should earn significant EA_c1 points."""
        credits = evaluate_leed_credits(project_data={"energy_reduction_pct": 30})
        ea_c1 = next(c for c in credits if c.credit_id == "EA_c1")
        assert ea_c1.earned_points >= 11
        assert ea_c1.status in ("achievable", "partial")

    def test_ss_c1_site_assessment(self):
        """Completed site assessment should earn 1 point."""
        credits = evaluate_leed_credits(project_data={"site_assessment_complete": True})
        ss_c1 = next(c for c in credits if c.credit_id == "SS_c1")
        assert ss_c1.earned_points == 1
        assert ss_c1.status == "achievable"

    def test_we_c2_indoor_water(self):
        """40% indoor water reduction should earn maximum WE_c2 points."""
        credits = evaluate_leed_credits(project_data={"indoor_water_reduction_pct": 40})
        we_c2 = next(c for c in credits if c.credit_id == "WE_c2")
        assert we_c2.earned_points == 6
        assert we_c2.status == "achievable"

    def test_ieq_c4_low_emitting(self):
        """All 6 low-emitting categories should earn 3 IEQ points."""
        credits = evaluate_leed_credits(project_data={"low_emitting_categories": 6})
        ieq_c4 = next(c for c in credits if c.credit_id == "IEQ_c4")
        assert ieq_c4.earned_points == 3

    def test_credit_has_reasoning(self):
        """Every credit evaluation should have non-empty reasoning."""
        credits = evaluate_leed_credits(project_data={"type": "commercial"})
        for c in credits:
            assert c.reasoning
            assert len(c.reasoning) > 0


# ---------------------------------------------------------------------------
# TestSalvagedMaterials
# ---------------------------------------------------------------------------


class TestSalvagedMaterials:
    """Tests for salvaged material tracking in LEED evaluation."""

    def test_salvaged_above_threshold(self):
        """Salvaged materials >= 5% should earn MR_c3 credit."""
        salvaged = [
            {"description": "Reclaimed steel", "cost": 30000},
            {"description": "Salvaged doors", "cost": 5000},
        ]
        credits = evaluate_leed_credits(
            project_data={"total_material_cost": 500000},
            salvaged_materials=salvaged,
        )
        mr_c3 = next(c for c in credits if c.credit_id == "MR_c3")
        # salvaged_pct = 35000/500000 = 7% >= 5%
        assert any("Salvaged materials >= 5%" in e for e in [mr_c3.reasoning])

    def test_salvaged_below_threshold(self):
        """Salvaged materials < 5% should not earn MR_c3 salvage credit."""
        salvaged = [{"description": "Small salvage", "cost": 1000}]
        credits = evaluate_leed_credits(
            project_data={"total_material_cost": 500000},
            salvaged_materials=salvaged,
        )
        mr_c3 = next(c for c in credits if c.credit_id == "MR_c3")
        # salvaged_pct = 1000/500000 = 0.2% < 5%
        assert "below 5%" in mr_c3.reasoning

    def test_empty_salvaged(self):
        """No salvaged materials should result in 0% salvaged."""
        credits = evaluate_leed_credits(
            project_data={"total_material_cost": 500000},
            salvaged_materials=[],
        )
        mr_c3 = next(c for c in credits if c.credit_id == "MR_c3")
        assert "below 5%" in mr_c3.reasoning


# ---------------------------------------------------------------------------
# TestRecycledContent
# ---------------------------------------------------------------------------


class TestRecycledContent:
    """Tests for recycled content percentage in LEED evaluation."""

    def test_recycled_above_20pct(self):
        """Recycled content >= 20% should earn MR_c3 credit."""
        credits = evaluate_leed_credits(
            project_data={},
            recycled_content_pct=25.0,
        )
        mr_c3 = next(c for c in credits if c.credit_id == "MR_c3")
        assert mr_c3.earned_points >= 1

    def test_recycled_below_20pct(self):
        """Recycled content < 20% should not earn recycled credit."""
        credits = evaluate_leed_credits(
            project_data={},
            recycled_content_pct=15.0,
        )
        mr_c3 = next(c for c in credits if c.credit_id == "MR_c3")
        assert "below 20%" in mr_c3.reasoning


# ---------------------------------------------------------------------------
# TestSustainabilityDashboard
# ---------------------------------------------------------------------------


class TestSustainabilityDashboard:
    """Tests for the full sustainability dashboard calculation."""

    def test_dashboard_structure(self):
        """Verify the dashboard dataclass has all required fields."""
        dashboard = SustainabilityDashboard(
            project_id="test-123",
            total_embodied_carbon_kgco2e=50000.0,
            carbon_per_sf=25.0,
            baseline_comparison_pct=40.0,
            embodied_carbon=EmbodiedCarbonResult(
                total_kgco2e=50000.0,
                total_tonco2e=50.0,
                carbon_per_sf=25.0,
                by_division={"Concrete": 30000.0, "Metals": 20000.0},
                by_item=[],
                item_count=5,
                unmatched_items=[],
                gross_area_sf=2000,
            ),
            leed_credits=[],
            salvaged_materials=[],
            recycled_content_pct=15.0,
            total_leed_points=12,
            max_possible_points=54,
            calculated_at="2026-03-15T00:00:00",
        )
        assert dashboard.project_id == "test-123"
        assert dashboard.total_embodied_carbon_kgco2e == 50000.0
        assert dashboard.carbon_per_sf == 25.0

    def test_baseline_comparison_commercial(self):
        """Commercial project should compare against CLF baseline of 42.0."""
        ec = EmbodiedCarbonResult(
            total_kgco2e=100000,
            total_tonco2e=100,
            carbon_per_sf=25.0,
            by_division={},
            by_item=[],
            item_count=5,
            unmatched_items=[],
            gross_area_sf=4000,
        )
        credits = evaluate_leed_credits(
            project_data={"type": "commercial"},
            embodied_carbon=ec,
        )
        mr_c1 = next(c for c in credits if c.credit_id == "MR_c1")
        # 25.0 vs 42.0 baseline = ~40.5% reduction
        assert mr_c1.earned_points == 5

    def test_baseline_comparison_healthcare(self):
        """Healthcare has higher baseline (65.0), so same carbon_per_sf earns more credit."""
        ec = EmbodiedCarbonResult(
            total_kgco2e=200000,
            total_tonco2e=200,
            carbon_per_sf=40.0,
            by_division={},
            by_item=[],
            item_count=5,
            unmatched_items=[],
            gross_area_sf=5000,
        )
        credits = evaluate_leed_credits(
            project_data={"type": "healthcare"},
            embodied_carbon=ec,
        )
        mr_c1 = next(c for c in credits if c.credit_id == "MR_c1")
        # 40.0 vs 65.0 baseline = ~38.5% reduction
        assert mr_c1.earned_points >= 4


# ---------------------------------------------------------------------------
# TestUnitConversion
# ---------------------------------------------------------------------------


class TestUnitConversion:
    """Tests for unit conversion helpers."""

    def test_same_unit(self):
        """Same unit should return 1.0."""
        assert _get_unit_conversion("CY", "CY") == 1.0

    def test_lb_to_ton(self):
        """2000 LB = 1 TON."""
        factor = _get_unit_conversion("LB", "TON")
        assert factor is not None
        assert abs(factor - 0.0005) < 0.0001

    def test_sy_to_sf(self):
        """1 SY = 9 SF."""
        factor = _get_unit_conversion("SY", "SF")
        assert factor is not None
        assert factor == 9.0

    def test_unsupported_conversion(self):
        """Unsupported conversion should return None."""
        assert _get_unit_conversion("EA", "TON") is None


# ---------------------------------------------------------------------------
# TestEndpoints (unit-test level, no actual HTTP)
# ---------------------------------------------------------------------------


class TestEndpoints:
    """Verify endpoint function signatures and imports are correct."""

    def test_sustainability_router_exists(self):
        """The sustainability router should be importable."""
        from app.api.v1.sustainability import router

        assert router is not None

    def test_route_count(self):
        """Router should have the expected number of routes."""
        from app.api.v1.sustainability import router

        routes = [r for r in router.routes if hasattr(r, "methods")]
        # carbon-factors (GET), project carbon-factors (GET), carbon/calculate (POST),
        # leed/evaluate (POST), dashboard (GET), summary (GET),
        # salvaged-materials (PUT), recycled-content (PUT), recalculate (POST)
        assert len(routes) >= 7

    def test_carbon_factors_dict_has_expected_entries(self):
        """Verify specific high-importance entries exist."""
        assert "03 30 00" in CARBON_FACTORS
        assert "05 12 00" in CARBON_FACTORS
        assert "22 11 00" in CARBON_FACTORS
        assert "26 05 00" in CARBON_FACTORS

    def test_leed_credits_dict_completeness(self):
        """All LEED credit IDs should be present."""
        assert "MR_c1" in LEED_V5_CREDITS
        assert "MR_c5" in LEED_V5_CREDITS
        assert "EA_c1" in LEED_V5_CREDITS
        assert "SS_c1" in LEED_V5_CREDITS
        assert "WE_c1" in LEED_V5_CREDITS
        assert "IEQ_c4" in LEED_V5_CREDITS

    def test_each_credit_has_required_fields(self):
        """Every LEED credit definition should have the required fields."""
        for credit_id, credit in LEED_V5_CREDITS.items():
            assert "id" in credit, f"{credit_id} missing 'id'"
            assert "name" in credit, f"{credit_id} missing 'name'"
            assert "category" in credit, f"{credit_id} missing 'category'"
            assert "max_points" in credit, f"{credit_id} missing 'max_points'"
            assert "requirements" in credit, f"{credit_id} missing 'requirements'"
            assert credit["max_points"] > 0, f"{credit_id} has 0 max_points"
