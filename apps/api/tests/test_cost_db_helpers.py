"""Tests for the pure helpers in cost_database (no DB or BLS calls).

The full cost_database module also has DB-backed query helpers and a
BLS API client; those need integration coverage. This file pins the
local pure logic: uncertainty-range lookup, BLS series resolution,
match scoring, error class shape.
"""

from __future__ import annotations

import pytest

from app.services.estimating.cost_database import (
    MATERIAL_UNCERTAINTY_RANGES,
    REFERENCE_COSTS,
    BLSDataUnavailableError,
    _resolve_series_id,
    _score_match,
    get_uncertainty_range,
)

# ---- get_uncertainty_range ----------------------------------------------


def test_get_uncertainty_range_exact_category():
    """Direct hit returns the configured pair as-is."""
    assert get_uncertainty_range("concrete") == MATERIAL_UNCERTAINTY_RANGES["concrete"]


def test_get_uncertainty_range_case_insensitive():
    a = get_uncertainty_range("CONCRETE")
    b = get_uncertainty_range("Concrete")
    c = get_uncertainty_range("concrete")
    assert a == b == c


def test_get_uncertainty_range_substring_match():
    """Categories like ``concrete_foundation`` should fall back to the
    parent ``concrete`` band via substring containment."""
    assert get_uncertainty_range("concrete_foundation") == MATERIAL_UNCERTAINTY_RANGES["concrete"]


def test_get_uncertainty_range_unknown_falls_back_to_default():
    out = get_uncertainty_range("does-not-exist-anywhere")
    assert out == MATERIAL_UNCERTAINTY_RANGES["default"]


def test_get_uncertainty_range_returns_low_high_tuple():
    low, high = get_uncertainty_range("structural_steel")
    assert 0.0 <= low <= high <= 1.0


@pytest.mark.parametrize("category", ["concrete", "structural_steel", "lumber", "labor", "default"])
def test_uncertainty_ranges_documented_categories(category):
    """Pin the documented categories so a refactor can't drop one and
    silently fall back to the default band."""
    assert category in MATERIAL_UNCERTAINTY_RANGES


# ---- _resolve_series_id -------------------------------------------------


def test_resolve_series_id_known_category_returns_mapped_series():
    """Direct hit in _BLS_SERIES_MAP returns the mapped series ID."""
    from app.services.estimating.cost_database import _BLS_SERIES_MAP

    series = _resolve_series_id("structural_steel")
    assert series == _BLS_SERIES_MAP["structural_steel"]


def test_resolve_series_id_unknown_falls_back_to_default():
    """Unknown categories return the documented default series."""
    from app.services.estimating.cost_database import _BLS_SERIES_MAP

    assert _resolve_series_id("alien-material-xyz") == _BLS_SERIES_MAP["default"]


def test_resolve_series_id_substring_match():
    """A category name that contains a known key should resolve via the
    substring branch — eg ``custom_lumber_grade`` falls through to the
    ``lumber`` series."""
    from app.services.estimating.cost_database import _BLS_SERIES_MAP

    series = _resolve_series_id("custom_lumber_grade")
    assert series == _BLS_SERIES_MAP["lumber"]


# ---- _score_match -------------------------------------------------------


def test_score_match_csi_prefix_gives_strong_score():
    """A direct CSI prefix match (first 2 digits) is the strongest
    single signal — at least 100 points."""
    # 03 30 00 = concrete
    score = _score_match("Cast in place concrete", "03 30 00", ("concrete", "CY"))
    assert score >= 100


def test_score_match_category_name_in_description():
    """Even without a CSI code, category-name presence in the description
    should produce a viable match score (>= 10)."""
    score = _score_match("Need fresh concrete for slab pour", "", ("concrete", "CY"))
    assert score >= 50  # at least the category-name boost


def test_score_match_returns_zero_for_unrelated_input():
    """Description with no CSI overlap and no category match should
    score below the viable threshold of 10."""
    # 'window' description vs concrete reference — no overlap.
    score = _score_match("aluminum window frame", "08 50 00", ("concrete", "CY"))
    assert score < 10


def test_score_match_word_overlap_filters_stopwords():
    """Stopwords (``the``, ``of``, ``in``…) must not contribute to the
    overlap score — otherwise every description trivially matches every
    reference."""
    # Description with NO real words in common with concrete refs but
    # plenty of stopwords:
    score = _score_match("the and of in to on for", "", ("concrete", "CY"))
    assert score == 0


# ---- REFERENCE_COSTS ----------------------------------------------------


def test_reference_costs_concrete_present():
    """Concrete is the canonical CY-priced item — its absence would
    be a regression."""
    assert ("concrete", "CY") in REFERENCE_COSTS
    entry = REFERENCE_COSTS[("concrete", "CY")]
    assert entry["base_cost"] > 0
    assert "description" in entry


def test_reference_costs_each_entry_has_base_cost_and_description():
    for ref_key, entry in REFERENCE_COSTS.items():
        assert "base_cost" in entry, f"{ref_key} missing base_cost"
        assert "description" in entry, f"{ref_key} missing description"
        assert entry["base_cost"] > 0, f"{ref_key} has non-positive cost"


# ---- BLSDataUnavailableError -------------------------------------------


def test_bls_data_unavailable_is_exception_subclass():
    assert issubclass(BLSDataUnavailableError, Exception)


def test_bls_data_unavailable_carries_message():
    exc = BLSDataUnavailableError("BLS API quota exceeded")
    assert str(exc) == "BLS API quota exceeded"
