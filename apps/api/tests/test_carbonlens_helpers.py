"""Tests for the CarbonLens material categorization + GWP baseline helpers.

The carbon-tracking logic depends on three constants and three pure
helpers — pin them so embodied-carbon calculations stay correct.
"""

from __future__ import annotations

import pytest

from app.services.products.carbonlens.service import (
    BUILDING_TYPE_BASELINES,
    CSI_CATEGORY_MAP,
    MATERIAL_BASELINES,
    _calc_improvement_pct,
    _lookup_baseline_gwp,
    auto_categorize_material,
)

# =========================================================================
# MATERIAL_BASELINES — pin canonical GWP values
# =========================================================================


def test_material_baselines_includes_canonical_materials():
    """Pin canonical low-carbon construction materials — refactor must
    not silently drop the carbon-sequestering wood entries (their
    negative GWP is the whole point of mass timber)."""
    canonical = [
        "ready-mix concrete 3000psi",
        "structural steel w-shapes",
        "steel rebar",
        "dimensional lumber",
        "glulam beam",
        "clt panel",
    ]
    for material in canonical:
        assert material in MATERIAL_BASELINES


def test_wood_materials_have_negative_gwp():
    """[carbon invariant] Mass timber sequesters carbon — GWP must be
    NEGATIVE for the wood materials. Pin so a refactor doesn't
    accidentally flip the sign and report wood as a carbon emitter."""
    wood = ["dimensional lumber", "glulam beam", "clt panel"]
    for material in wood:
        gwp = MATERIAL_BASELINES[material]["gwp"]
        assert gwp < 0, f"{material} GWP {gwp} should be negative"


def test_low_carbon_concrete_lower_than_standard():
    """Low-carbon concrete must have LOWER GWP than standard 4000psi."""
    low_carbon = MATERIAL_BASELINES["low-carbon concrete"]["gwp"]
    standard = MATERIAL_BASELINES["ready-mix concrete 4000psi"]["gwp"]
    assert low_carbon < standard


def test_recycled_steel_lower_than_virgin():
    """Recycled structural steel must have LOWER GWP than virgin."""
    recycled = MATERIAL_BASELINES["recycled structural steel"]["gwp"]
    virgin = MATERIAL_BASELINES["structural steel w-shapes"]["gwp"]
    assert recycled < virgin


def test_each_baseline_has_gwp_and_unit():
    for material, data in MATERIAL_BASELINES.items():
        assert "gwp" in data, f"{material} missing gwp"
        assert "unit" in data, f"{material} missing unit"


# =========================================================================
# BUILDING_TYPE_BASELINES
# =========================================================================


def test_building_type_baselines_canonical():
    expected = {
        "office",
        "residential",
        "education",
        "healthcare",
        "retail",
        "industrial",
        "mixed_use",
    }
    assert set(BUILDING_TYPE_BASELINES.keys()) == expected


def test_healthcare_highest_carbon_per_sf():
    """Healthcare has the highest documented embodied-carbon density."""
    healthcare = BUILDING_TYPE_BASELINES["healthcare"]
    for building_type, gwp in BUILDING_TYPE_BASELINES.items():
        if building_type != "healthcare":
            assert gwp <= healthcare


# =========================================================================
# CSI_CATEGORY_MAP
# =========================================================================


def test_csi_category_map_canonical():
    """Pin Division 03-09 + 32 mapping to material categories."""
    assert CSI_CATEGORY_MAP["03"] == "structure"  # concrete
    assert CSI_CATEGORY_MAP["04"] == "structure"  # masonry
    assert CSI_CATEGORY_MAP["05"] == "structure"  # metals
    assert CSI_CATEGORY_MAP["06"] == "structure"  # wood
    assert CSI_CATEGORY_MAP["07"] == "enclosure"  # thermal/moisture
    assert CSI_CATEGORY_MAP["08"] == "enclosure"  # openings
    assert CSI_CATEGORY_MAP["09"] == "enclosure"  # finishes
    assert CSI_CATEGORY_MAP["32"] == "hardscape"  # exterior improvements


# =========================================================================
# auto_categorize_material
# =========================================================================


def test_categorize_by_csi_division_concrete():
    assert auto_categorize_material(csi_division="03 30 00") == "structure"


def test_categorize_by_csi_division_padding():
    """Single-digit division numbers are zero-padded — "3" → "03"."""
    assert auto_categorize_material(csi_division="3") == "structure"


def test_categorize_by_csi_division_with_leading_zeros():
    """Heavy padding in user input shouldn't trip — "0003" → "03"."""
    assert auto_categorize_material(csi_division="0003") == "structure"


def test_categorize_unknown_division_falls_through():
    """Unknown division (e.g. 22 plumbing) without keywords →
    documented "structure" default."""
    assert auto_categorize_material(csi_division="22") == "structure"


def test_categorize_by_keyword_concrete():
    """Without CSI division, fall back to keyword matching on type."""
    assert auto_categorize_material(material_type="ready-mix concrete") == "structure"


def test_categorize_by_keyword_wood():
    assert auto_categorize_material(material_type="GLT lumber beam") == "structure"


def test_categorize_by_keyword_curtain_wall():
    assert auto_categorize_material(material_type="aluminum curtain wall") == "enclosure"


def test_categorize_by_keyword_paving():
    assert auto_categorize_material(material_type="asphalt paving") == "hardscape"


def test_categorize_by_keyword_roofing():
    assert auto_categorize_material(material_type="standing seam roofing") == "enclosure"


def test_categorize_keyword_case_insensitive():
    """Case-insensitive keyword match — "CONCRETE PAD" still matches."""
    assert auto_categorize_material(material_type="CONCRETE PAD") == "structure"


def test_categorize_no_input_defaults_structure():
    """No CSI, no type → default "structure"."""
    assert auto_categorize_material() == "structure"


def test_categorize_csi_takes_priority_over_keyword():
    """If both CSI and material_type are given AND the bare division
    number matches CSI_CATEGORY_MAP, the CSI mapping wins. Pin: a
    Division 03 material with type "insulation" (which would
    normally match enclosure via keyword) is categorized as
    structure."""
    out = auto_categorize_material(csi_division="03", material_type="insulation")
    assert out == "structure"


def test_categorize_csi_with_spaces_falls_through_to_keyword():
    """[documented quirk] CSI codes with section/article spaces like
    "03 30 00" don't trip the lookup (the lstrip+zfill+slice picks up
    "3 " rather than "03"). The function falls through to keyword
    matching. Pinning so a refactor either fixes the parser or
    preserves the documented behavior."""
    out = auto_categorize_material(csi_division="03 30 00", material_type="insulation")
    # Falls through to keyword match → "insulation" matches enclosure.
    assert out == "enclosure"


# =========================================================================
# _lookup_baseline_gwp
# =========================================================================


def test_lookup_baseline_gwp_known_material():
    out = _lookup_baseline_gwp("Ready-Mix Concrete 4000psi")
    assert out is not None
    assert out["gwp"] == 350.0
    assert out["unit"] == "CY"


def test_lookup_baseline_gwp_unknown_material_returns_none():
    assert _lookup_baseline_gwp("alien_material_xyz") is None


def test_lookup_baseline_gwp_case_insensitive():
    """Material lookup must be case-insensitive — clients pass mixed
    case."""
    a = _lookup_baseline_gwp("STEEL REBAR")
    b = _lookup_baseline_gwp("steel rebar")
    assert a == b


def test_lookup_baseline_gwp_strips_whitespace():
    out = _lookup_baseline_gwp("  steel rebar  ")
    assert out is not None
    assert out["gwp"] == 1100.0


# =========================================================================
# _calc_improvement_pct
# =========================================================================


def test_improvement_pct_better_than_baseline():
    """Actual GWP 250 vs baseline 350 → 28.57% improvement."""
    out = _calc_improvement_pct(actual_gwp=250.0, baseline_gwp=350.0)
    assert out == pytest.approx(28.57, abs=0.01)


def test_improvement_pct_worse_than_baseline():
    """Actual GWP higher than baseline → NEGATIVE improvement %."""
    out = _calc_improvement_pct(actual_gwp=400.0, baseline_gwp=350.0)
    assert out < 0


def test_improvement_pct_matching_baseline_zero():
    """Equal → 0% improvement."""
    out = _calc_improvement_pct(actual_gwp=350.0, baseline_gwp=350.0)
    assert out == 0.0


def test_improvement_pct_zero_baseline_returns_zero():
    """[defensive] Division-by-zero guard — zero baseline → 0%
    (not raise, not infinity)."""
    out = _calc_improvement_pct(actual_gwp=100.0, baseline_gwp=0.0)
    assert out == 0.0


def test_improvement_pct_rounded_to_two_decimals():
    out = _calc_improvement_pct(actual_gwp=199.999, baseline_gwp=350.0)
    assert round(out, 2) == out
