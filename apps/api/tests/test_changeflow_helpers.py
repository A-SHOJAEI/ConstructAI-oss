"""Tests for ChangeFlow pure helpers (T&M pricing + scope narrative).

Pin the markup-cascade math (labor burden → material tax → overhead
→ profit → bond) and the scope narrative template fallback.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.products.changeflow.service import (
    _build_scope_narrative,
    calculate_pricing_summary,
)

# =========================================================================
# helpers
# =========================================================================


def _entry(entry_type: str, **fields):
    """Build a minimal T&M entry stub."""
    base = {
        "entry_type": entry_type,
        "straight_hours": None,
        "overtime_hours": None,
        "labor_rate": None,
        "ot_rate": None,
        "quantity": None,
        "unit_cost": None,
        "equipment_hours": None,
        "equipment_rate": None,
        "sub_amount": None,
        "worker_name": None,
        "material_description": None,
        "equipment_type": None,
        "sub_name": None,
    }
    base.update(fields)
    return SimpleNamespace(**base)


# =========================================================================
# calculate_pricing_summary — markup cascade
# =========================================================================


def test_pricing_empty_entries_zero_grand_total():
    out = calculate_pricing_summary([])
    assert out["grand_total"] == 0.0
    assert out["labor_total"] == 0.0
    assert out["material_total"] == 0.0


def test_pricing_labor_with_burden():
    """Labor: 10 straight × $50/hr + 2 OT × $75/hr = $650.
    Burden 40% → $910 total."""
    entries = [
        _entry(
            "labor",
            straight_hours=10,
            overtime_hours=2,
            labor_rate=50.0,
            ot_rate=75.0,
        )
    ]
    out = calculate_pricing_summary(
        entries,
        overhead_pct=0,
        profit_pct=0,
        bond_pct=0,
        labor_burden_pct=0.40,
    )
    assert out["labor_subtotal"] == pytest.approx(650.0)
    assert out["labor_burden"] == pytest.approx(260.0)
    assert out["labor_total"] == pytest.approx(910.0)


def test_pricing_material_with_tax():
    """Material: 100 × $5 = $500, 6% tax → $530 total."""
    entries = [_entry("material", quantity=100, unit_cost=5.0)]
    out = calculate_pricing_summary(
        entries,
        overhead_pct=0,
        profit_pct=0,
        bond_pct=0,
        labor_burden_pct=0,
        material_tax_rate=0.06,
    )
    assert out["material_subtotal"] == pytest.approx(500.0)
    assert out["material_tax"] == pytest.approx(30.0)
    assert out["material_total"] == pytest.approx(530.0)


def test_pricing_equipment():
    entries = [_entry("equipment", equipment_hours=8, equipment_rate=125.0)]
    out = calculate_pricing_summary(
        entries,
        overhead_pct=0,
        profit_pct=0,
        bond_pct=0,
        labor_burden_pct=0,
    )
    assert out["equipment_total"] == pytest.approx(1000.0)


def test_pricing_subcontractor():
    entries = [_entry("subcontractor", sub_amount=5000.0)]
    out = calculate_pricing_summary(
        entries,
        overhead_pct=0,
        profit_pct=0,
        bond_pct=0,
        labor_burden_pct=0,
    )
    assert out["sub_total"] == pytest.approx(5000.0)


def test_pricing_markup_cascade_order():
    """[business invariant] Cascade: direct → overhead on direct →
    profit on (direct+overhead) → bond on (direct+overhead+profit).
    Each stage compounds, never on raw direct cost."""
    # Direct cost = $1000 (1000 sub, no labor/material/equipment)
    entries = [_entry("subcontractor", sub_amount=1000.0)]
    out = calculate_pricing_summary(
        entries,
        overhead_pct=0.10,  # 10% on $1000 = $100
        profit_pct=0.10,  # 10% on $1100 = $110
        bond_pct=0.01,  # 1% on $1210 = $12.10
        labor_burden_pct=0,
    )
    assert out["overhead_amount"] == pytest.approx(100.0)
    assert out["profit_amount"] == pytest.approx(110.0)  # not 100!
    assert out["bond_amount"] == pytest.approx(12.10)
    assert out["grand_total"] == pytest.approx(1222.10)


def test_pricing_full_mixed_entries():
    """Realistic mix: labor + material + equipment + subcontractor."""
    entries = [
        _entry("labor", straight_hours=10, labor_rate=50.0),  # 500
        _entry("material", quantity=20, unit_cost=10.0),  # 200
        _entry("equipment", equipment_hours=5, equipment_rate=100.0),  # 500
        _entry("subcontractor", sub_amount=300.0),  # 300
    ]
    out = calculate_pricing_summary(
        entries,
        overhead_pct=0.10,
        profit_pct=0.10,
        bond_pct=0.01,
        labor_burden_pct=0.40,
        material_tax_rate=0.06,
    )
    # labor 500 + burden 200 = 700
    # material 200 + tax 12 = 212
    # equipment 500
    # sub 300
    # direct = 1712
    # overhead = 171.20
    # profit = (1712 + 171.20) * 0.10 = 188.32
    # bond = (1712 + 171.20 + 188.32) * 0.01 = 20.7152 ≈ 20.72
    assert out["direct_cost_subtotal"] == pytest.approx(1712.0)
    assert out["overhead_amount"] == pytest.approx(171.20)
    assert out["grand_total"] == pytest.approx(2092.24, abs=0.05)


def test_pricing_results_rounded_to_two_decimals():
    """Money values must round to 2 decimal places."""
    entries = [_entry("subcontractor", sub_amount=333.333)]
    out = calculate_pricing_summary(
        entries,
        overhead_pct=0.10,
        profit_pct=0.10,
        bond_pct=0.01,
        labor_burden_pct=0,
    )
    for key, val in out.items():
        assert round(val, 2) == val, f"{key}={val} not rounded to 2dp"


def test_pricing_none_fields_treated_as_zero():
    """[defensive] None values for hours/rates → treated as 0,
    no AttributeError or TypeError."""
    entries = [
        _entry("labor", straight_hours=None, labor_rate=None),
        _entry("material", quantity=None, unit_cost=None),
    ]
    out = calculate_pricing_summary(
        entries,
        overhead_pct=0,
        profit_pct=0,
        bond_pct=0,
        labor_burden_pct=0.40,
    )
    assert out["labor_subtotal"] == 0.0
    assert out["material_subtotal"] == 0.0
    assert out["grand_total"] == 0.0


def test_pricing_unknown_entry_type_skipped():
    """An unknown entry_type doesn't crash — just contributes 0."""
    entries = [
        _entry("alien_type", straight_hours=10, labor_rate=50.0),
        _entry("labor", straight_hours=10, labor_rate=50.0),
    ]
    out = calculate_pricing_summary(
        entries,
        overhead_pct=0,
        profit_pct=0,
        bond_pct=0,
        labor_burden_pct=0,
    )
    # Only the labor entry contributes:
    assert out["labor_subtotal"] == 500.0


# =========================================================================
# _build_scope_narrative
# =========================================================================


def test_scope_narrative_empty_entries_returns_default():
    out = _build_scope_narrative([], subject=None)
    assert "T&M change order" in out


def test_scope_narrative_with_subject():
    out = _build_scope_narrative([], subject="Foundation rework")
    assert "Foundation rework" in out


def test_scope_narrative_labor_section():
    entries = [
        _entry("labor", straight_hours=8, overtime_hours=2, worker_name="Alice"),
        _entry("labor", straight_hours=8, overtime_hours=0, worker_name="Bob"),
    ]
    out = _build_scope_narrative(entries, subject=None)
    assert "Labor" in out
    assert "16.0 straight hours" in out
    assert "2.0 OT hours" in out
    assert "2 worker(s)" in out


def test_scope_narrative_material_section():
    entries = [
        _entry("material", material_description="Steel rebar"),
        _entry("material", material_description="Concrete mix"),
        _entry("material", material_description="Forms"),
        _entry("material", material_description="Anchor bolts"),
    ]
    out = _build_scope_narrative(entries, subject=None)
    assert "Materials" in out
    # Includes first 3 descriptions:
    assert "Steel rebar" in out
    assert "Concrete mix" in out
    assert "Forms" in out


def test_scope_narrative_equipment_section():
    entries = [
        _entry("equipment", equipment_type="Excavator"),
        _entry("equipment", equipment_type="Loader"),
    ]
    out = _build_scope_narrative(entries, subject=None)
    assert "Equipment" in out
    assert "Excavator" in out or "Loader" in out


def test_scope_narrative_sub_section():
    entries = [
        _entry("subcontractor", sub_name="ABC Plumbing"),
        _entry("subcontractor", sub_name="XYZ Electric"),
    ]
    out = _build_scope_narrative(entries, subject=None)
    assert "Subcontractors" in out
    assert "ABC Plumbing" in out or "XYZ Electric" in out


def test_scope_narrative_sub_section_no_names_uses_count():
    """When sub names are missing, narrative falls back to count."""
    entries = [
        _entry("subcontractor", sub_name=None),
        _entry("subcontractor", sub_name=None),
    ]
    out = _build_scope_narrative(entries, subject=None)
    assert "2 entry(ies)" in out


def test_scope_narrative_combined_sections():
    """Multiple entry types — all appear in narrative."""
    entries = [
        _entry("labor", straight_hours=8),
        _entry("material", material_description="Steel"),
        _entry("equipment", equipment_type="Crane"),
        _entry("subcontractor", sub_name="ACME"),
    ]
    out = _build_scope_narrative(entries, subject="Combined work")
    assert "Combined work" in out
    assert "Labor" in out
    assert "Materials" in out
    assert "Equipment" in out
    assert "Subcontractors" in out
