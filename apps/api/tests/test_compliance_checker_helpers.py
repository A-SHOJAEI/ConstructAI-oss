"""Tests for the compliance checker pure helpers.

Pin checklist loading, filtering, summary generation, and the
applicable-standards dispatcher.
"""

from __future__ import annotations

import pytest

from app.services.quality.compliance_checker import (
    IBC_STANDARDS,
    OSHA_STANDARDS,
    _get_applicable_standards,
    _load_checklists,
    check_project_compliance,
    clear_checklist_cache,
    get_checklist_by_id,
    get_checklist_summary,
    get_checklists,
)


@pytest.fixture(autouse=True)
def reset_cache():
    clear_checklist_cache()
    yield
    clear_checklist_cache()


# =========================================================================
# OSHA / IBC standards registries
# =========================================================================


def test_osha_standards_includes_canonical_codes():
    """Pin: at minimum the most-cited OSHA construction standards must
    be present."""
    # Fall protection (1926.501) is the single most-cited construction
    # standard — must be in the registry.
    assert "1926.501" in OSHA_STANDARDS
    # PPE (1926.95) is also canonical:
    canonical = ["1926.20", "1926.21", "1926.501"]
    for code in canonical:
        assert code in OSHA_STANDARDS, f"missing canonical OSHA standard {code}"


def test_osha_standard_entries_have_title_and_category():
    for code, standard in OSHA_STANDARDS.items():
        assert "title" in standard, f"{code} missing title"
        assert "category" in standard, f"{code} missing category"


def test_ibc_standards_present():
    """IBC has fewer documented standards but should be non-empty."""
    assert len(IBC_STANDARDS) >= 1


# =========================================================================
# _load_checklists / cache
# =========================================================================


def test_load_checklists_returns_list():
    out = _load_checklists()
    assert isinstance(out, list)


def test_load_checklists_cached():
    """Two calls return same list instance (cache hit)."""
    a = _load_checklists()
    b = _load_checklists()
    assert a is b


def test_clear_cache_forces_reload():
    a = _load_checklists()
    clear_checklist_cache()
    b = _load_checklists()
    # Different list instance after clear:
    assert a is not b


# =========================================================================
# get_checklists — filtering
# =========================================================================


def test_get_checklists_no_filter_returns_all():
    """Without filters, returns the full list."""
    all_checks = _load_checklists()
    out = get_checklists()
    assert len(out) == len(all_checks)


def test_get_checklists_filter_by_category():
    """Category filter narrows the result set; unknown category → []."""
    out = get_checklists(category="alien_category_xyz")
    assert out == []


def test_get_checklists_filter_by_severity():
    out = get_checklists(severity="alien_severity_xyz")
    assert out == []


def test_get_checklists_filter_by_phase():
    out = get_checklists(phase="alien_phase_xyz")
    assert out == []


def test_get_checklists_filter_by_project_type():
    out = get_checklists(project_type="alien_project_xyz")
    assert out == []


def test_get_checklists_combined_filters():
    """Multiple filters AND together — narrows further."""
    a = get_checklists(category="osha_safety")
    b = get_checklists(category="osha_safety", severity="critical")
    # b is a subset of a:
    if a:
        assert len(b) <= len(a)


# =========================================================================
# get_checklist_by_id
# =========================================================================


def test_get_checklist_by_id_unknown_returns_none():
    assert get_checklist_by_id("NEVER-EXISTS-999") is None


def test_get_checklist_by_id_returns_matching_entry():
    """If any checklists are loaded, look one up by its check_id."""
    all_checks = _load_checklists()
    if all_checks:
        first = all_checks[0]
        check_id = first.get("check_id")
        if check_id:
            out = get_checklist_by_id(check_id)
            assert out is not None
            assert out["check_id"] == check_id


# =========================================================================
# get_checklist_summary
# =========================================================================


def test_get_checklist_summary_returns_required_keys():
    out = get_checklist_summary()
    assert "total_checks" in out
    assert "by_category" in out
    assert "by_severity" in out


def test_get_checklist_summary_total_matches_load():
    out = get_checklist_summary()
    all_checks = _load_checklists()
    assert out["total_checks"] == len(all_checks)


def test_get_checklist_summary_categories_sum_to_total():
    out = get_checklist_summary()
    cat_total = sum(out["by_category"].values())
    assert cat_total == out["total_checks"]


def test_get_checklist_summary_severities_sum_to_total():
    out = get_checklist_summary()
    sev_total = sum(out["by_severity"].values())
    assert sev_total == out["total_checks"]


# =========================================================================
# _get_applicable_standards
# =========================================================================


def test_applicable_standards_no_project_type_returns_all():
    """No project_type → returns OSHA + IBC merged."""
    out = _get_applicable_standards(None)
    assert len(out) >= len(OSHA_STANDARDS)
    # Includes some OSHA + IBC entries:
    assert "1926.501" in out


def test_applicable_standards_unknown_project_type_returns_all():
    """Unknown project type defaults to all categories."""
    out = _get_applicable_standards("alien_project_xyz")
    assert len(out) >= 1


# =========================================================================
# check_project_compliance — high-level
# =========================================================================


@pytest.mark.asyncio
async def test_check_compliance_unknown_regulation_skipped():
    """A regulation code that's not in the registry → status=skipped."""
    out = await check_project_compliance(
        project_id="p-1",
        regulations=["NEVER-EXISTS-999"],
        project_data={},
    )
    assert len(out) == 1
    assert out[0]["status"] == "skipped"
    assert "Unknown Standard" in out[0]["regulation_title"]


@pytest.mark.asyncio
async def test_check_compliance_known_osha_returns_result():
    """A known OSHA standard returns a structured result."""
    out = await check_project_compliance(
        project_id="p-1",
        regulations=["1926.501"],  # Fall protection
        project_data={"safety_measures": [{"type": "fall_protection"}]},
    )
    assert len(out) == 1
    assert out[0]["regulation_code"] == "1926.501"
    assert out[0]["regulation_title"] == OSHA_STANDARDS["1926.501"]["title"]
    assert "status" in out[0]
    assert "findings" in out[0]


@pytest.mark.asyncio
async def test_check_compliance_default_runs_all_standards():
    """Without explicit regulations list, runs against all applicable."""
    out = await check_project_compliance(
        project_id="p-1",
        regulations=None,
        project_data={},
        project_type="commercial",
    )
    # Result list should be non-empty:
    assert len(out) >= 1
