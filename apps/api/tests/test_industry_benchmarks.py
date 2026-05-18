"""Tests for industry benchmark data and activity classification.

Pin the documented benchmark dictionaries (CPI / change-order rates /
duration uncertainty) and the keyword-matching ``classify_activity``
helper that drives Monte Carlo schedule risk simulation.
"""

from __future__ import annotations

import pytest

from app.services.controls.industry_benchmarks import (
    CHANGE_ORDER_RATES,
    CPI_BENCHMARKS,
    DURATION_UNCERTAINTY,
    classify_activity,
    get_duration_bounds,
)

# =========================================================================
# CPI_BENCHMARKS
# =========================================================================


def test_cpi_benchmarks_canonical_project_types():
    """Pin the documented project types — refactor must not silently
    drop coverage for one."""
    expected = {
        "commercial",
        "infrastructure",
        "residential",
        "institutional",
        "healthcare",
        "industrial",
    }
    assert set(CPI_BENCHMARKS.keys()) == expected


def test_cpi_benchmarks_each_has_required_stats():
    """Pin schema — every entry must have mean/std/p10/p50/p90."""
    for project_type, stats in CPI_BENCHMARKS.items():
        for key in ("mean", "std", "p10", "p50", "p90"):
            assert key in stats, f"{project_type} missing {key}"
        # Sanity: 0 < mean < 2, std > 0
        assert 0 < stats["mean"] < 2
        assert stats["std"] > 0


def test_cpi_benchmarks_percentiles_ordered():
    """P10 < P50 < P90 must hold for every project type."""
    for project_type, stats in CPI_BENCHMARKS.items():
        assert stats["p10"] < stats["p50"], f"{project_type}: p10 must be < p50"
        assert stats["p50"] < stats["p90"], f"{project_type}: p50 must be < p90"


def test_cpi_benchmarks_residential_has_lowest_variance():
    """Residential projects are the smallest, most-predictable type;
    pin that residential std is the smallest of the documented set."""
    res_std = CPI_BENCHMARKS["residential"]["std"]
    for project_type, stats in CPI_BENCHMARKS.items():
        if project_type != "residential":
            assert res_std <= stats["std"]


def test_cpi_benchmarks_healthcare_below_one():
    """Healthcare is notoriously over-budget — pin that mean CPI < 1.0."""
    assert CPI_BENCHMARKS["healthcare"]["mean"] < 1.0


# =========================================================================
# DURATION_UNCERTAINTY
# =========================================================================


def test_duration_uncertainty_has_default():
    """The default fallback must always be present."""
    assert "default" in DURATION_UNCERTAINTY


def test_duration_uncertainty_optimistic_negative_pessimistic_positive():
    """Pin the sign convention: optimistic_pct < 0 (durations shrink),
    pessimistic_pct > 0 (durations grow)."""
    for category, unc in DURATION_UNCERTAINTY.items():
        assert unc["optimistic_pct"] < 0, f"{category} optimistic must be negative"
        assert unc["pessimistic_pct"] > 0, f"{category} pessimistic must be positive"


def test_duration_uncertainty_site_work_widest_pessimistic():
    """Site work is the most schedule-sensitive (weather + utility
    discoveries) — its pessimistic_pct should be among the highest."""
    site_pess = DURATION_UNCERTAINTY["site_work"]["pessimistic_pct"]
    finishes_pess = DURATION_UNCERTAINTY["interior_finishes"]["pessimistic_pct"]
    # Site work has more uncertainty than predictable interior finishes.
    assert site_pess > finishes_pess


# =========================================================================
# CHANGE_ORDER_RATES
# =========================================================================


def test_change_order_rates_canonical_types():
    expected = {"commercial", "infrastructure", "residential", "institutional", "healthcare"}
    assert set(CHANGE_ORDER_RATES.keys()) == expected


def test_change_order_rates_healthcare_highest():
    """Healthcare has the highest documented CO rate (regulatory
    complexity, owner change requests)."""
    healthcare_mean = CHANGE_ORDER_RATES["healthcare"]["mean_pct"]
    for project_type, stats in CHANGE_ORDER_RATES.items():
        if project_type != "healthcare":
            assert healthcare_mean >= stats["mean_pct"]


def test_change_order_rates_residential_lowest():
    """Residential has the lowest documented CO rate."""
    residential_mean = CHANGE_ORDER_RATES["residential"]["mean_pct"]
    for project_type, stats in CHANGE_ORDER_RATES.items():
        if project_type != "residential":
            assert residential_mean <= stats["mean_pct"]


def test_change_order_rates_all_below_25_pct():
    """Sanity: all means should be < 25% (anything higher is
    pathological)."""
    for stats in CHANGE_ORDER_RATES.values():
        assert stats["mean_pct"] < 0.25


# =========================================================================
# classify_activity
# =========================================================================


@pytest.mark.parametrize(
    "name,expected_category",
    [
        ("Excavate building footprint", "site_work"),
        ("Site grading and prep", "site_work"),
        ("Spread footing", "foundations"),
        ("Concrete pile installation", "foundations"),
        ("Steel column erection", "structural_steel"),
        ("Concrete slab pour", "concrete_structure"),
        ("Concrete formwork", "concrete_structure"),
        ("HVAC ductwork rough-in", "mep_rough_in"),
        ("Electrical conduit", "mep_rough_in"),
        ("Plumbing piping", "mep_rough_in"),
        ("Curtain wall installation", "building_enclosure"),
        ("Roofing membrane", "building_enclosure"),
        ("Drywall installation", "interior_finishes"),
        ("Paint walls", "interior_finishes"),
        ("Carpet floor", "interior_finishes"),
        ("Building commissioning", "commissioning"),
        ("System startup and testing", "commissioning"),
        ("Punch list closeout", "commissioning"),
    ],
)
def test_classify_activity_keyword_match(name: str, expected_category: str):
    assert classify_activity(name) == expected_category


def test_classify_activity_unknown_falls_back_to_default():
    """A completely unrelated activity name → "default"."""
    assert classify_activity("alien activity") == "default"
    assert classify_activity("") == "default"


def test_classify_activity_uses_wbs_code_too():
    """If the WBS code contains keywords, those should also match."""
    out = classify_activity(name="task A1", wbs_code="03.30 concrete")
    assert out == "concrete_structure"


def test_classify_activity_case_insensitive():
    """Both name and WBS comparisons are lowercased internally."""
    assert classify_activity("CONCRETE Pour") == "concrete_structure"


def test_classify_activity_first_match_wins():
    """Keywords are checked in dict order — the first category whose
    keyword appears wins. Concrete-bearing site work text should match
    site_work first (since site_work comes before concrete_structure
    in dict order)."""
    # "site grading concrete" — both match, but order means site_work
    # gets the first hit on "site"/"grading":
    out = classify_activity("site grading and concrete pour")
    assert out == "site_work"


# =========================================================================
# get_duration_bounds
# =========================================================================


def test_duration_bounds_explicit_category():
    """Site work uncertainty: -15% / +50%."""
    opt, pess = get_duration_bounds(duration_days=10, category="site_work")
    assert opt == pytest.approx(8.5)  # 10 × (1 - 0.15)
    assert pess == pytest.approx(15.0)  # 10 × (1 + 0.50)


def test_duration_bounds_classifies_when_no_category():
    """Without explicit category, falls back to keyword classification."""
    opt, pess = get_duration_bounds(duration_days=10, name="HVAC rough-in")
    # MEP rough-in: -8% / +40%
    assert opt == pytest.approx(9.2)
    assert pess == pytest.approx(14.0)


def test_duration_bounds_unknown_uses_default():
    """Unknown activity → default uncertainty (-20% / +20%)."""
    opt, pess = get_duration_bounds(duration_days=10, name="alien activity")
    assert opt == pytest.approx(8.0)
    assert pess == pytest.approx(12.0)


def test_duration_bounds_optimistic_floored_at_one_day():
    """A 1-day activity with -20% optimistic = 0.8 days, but the
    floor enforces ≥ 1 day."""
    opt, _pess = get_duration_bounds(duration_days=1, category="default")
    assert opt == 1


def test_duration_bounds_returns_floats():
    """Pin the return type — tuple of (optimistic, pessimistic) floats."""
    opt, pess = get_duration_bounds(duration_days=10, category="commissioning")
    assert isinstance(opt, float)
    assert isinstance(pess, float)


def test_duration_bounds_zero_duration():
    """Zero duration: optimistic floored at 1, pessimistic = 0."""
    opt, pess = get_duration_bounds(duration_days=0, category="default")
    assert opt == 1
    assert pess == 0
