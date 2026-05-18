"""Tests for the WageGuard pure helpers (constants + classification fallback).

The full service is DB-bound and LLM-dependent; these tests pin the
documented Davis-Bacon classification list, the seed determinations
shape, and the fuzzy-match fallback that activates when the LLM is
unreachable.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.products.wageguard.service import (
    KNOWN_CLASSIFICATIONS,
    SEED_DETERMINATIONS,
    map_classification,
)

# =========================================================================
# KNOWN_CLASSIFICATIONS — pin the documented Davis-Bacon list
# =========================================================================


def test_known_classifications_includes_canonical_trades():
    """Pin the documented Davis-Bacon trade classifications — refactor
    must not silently drop a canonical trade."""
    canonical = [
        "Carpenter",
        "Electrician",
        "Ironworker",
        "Laborer",
        "Painter",
        "Plumber",
        "Roofer",
        "Sheet Metal Worker",
        "Operating Engineer",
        "Cement Mason",
    ]
    for trade in canonical:
        assert trade in KNOWN_CLASSIFICATIONS


def test_known_classifications_count_at_least_20():
    """Davis-Bacon coverage should span the major construction trades."""
    assert len(KNOWN_CLASSIFICATIONS) >= 20


def test_known_classifications_unique():
    """No duplicates — fuzzy match against duplicates would split votes."""
    assert len(KNOWN_CLASSIFICATIONS) == len(set(KNOWN_CLASSIFICATIONS))


# =========================================================================
# SEED_DETERMINATIONS — pin sample wage data shape
# =========================================================================


def test_seed_determinations_non_empty():
    assert SEED_DETERMINATIONS


def test_seed_determinations_each_has_required_fields():
    for det in SEED_DETERMINATIONS:
        for required in ("sam_gov_id", "state", "county", "project_type", "classifications"):
            assert required in det, f"determination missing {required}"


def test_seed_determinations_classifications_have_rates():
    for det in SEED_DETERMINATIONS:
        for cls in det["classifications"]:
            assert "title" in cls
            assert "base_rate" in cls
            assert "fringe_rate" in cls
            assert "total_rate" in cls


def test_seed_determinations_total_rate_sums_correctly():
    """[business invariant] total_rate must equal base_rate +
    fringe_rate. If a refactor breaks this, certified payroll
    calculations get wrong totals."""
    for det in SEED_DETERMINATIONS:
        for cls in det["classifications"]:
            base = cls["base_rate"]
            fringe = cls["fringe_rate"]
            total = cls["total_rate"]
            assert total == pytest.approx(base + fringe, abs=0.01), (
                f"{cls['title']}: total {total} != base {base} + fringe {fringe}"
            )


def test_seed_determinations_state_codes_are_two_chars():
    """USPS state codes should be 2 characters."""
    for det in SEED_DETERMINATIONS:
        assert len(det["state"]) == 2


# =========================================================================
# map_classification — fuzzy match fallback
# =========================================================================


@pytest.mark.asyncio
async def test_map_classification_falls_back_to_fuzzy_when_llm_fails():
    """[fallback] When LLM is unreachable, fuzzy match kicks in.
    "carpinter" (typo) should still map to "Carpenter" via fuzzy
    similarity."""
    # Force the LLM call to fail:
    with patch("anthropic.Anthropic", side_effect=ImportError("no anthropic")):
        out = await map_classification("carpinter")
    assert out["suggested_davis_bacon"] == "Carpenter"
    # Confidence is the SequenceMatcher ratio (< 1.0 for typo):
    assert 0.5 < out["confidence"] < 1.0


@pytest.mark.asyncio
async def test_map_classification_exact_match_high_confidence():
    """Exact match against KNOWN_CLASSIFICATIONS — fuzzy gives ratio 1.0."""
    with patch("anthropic.Anthropic", side_effect=ImportError("no anthropic")):
        out = await map_classification("Electrician")
    assert out["suggested_davis_bacon"] == "Electrician"
    assert out["confidence"] == 1.0


@pytest.mark.asyncio
async def test_map_classification_case_insensitive():
    """Fuzzy match lowercases both sides — "CARPENTER" should still
    match cleanly."""
    with patch("anthropic.Anthropic", side_effect=ImportError("no anthropic")):
        out = await map_classification("CARPENTER")
    assert out["suggested_davis_bacon"] == "Carpenter"
    assert out["confidence"] >= 0.9


@pytest.mark.asyncio
async def test_map_classification_picks_best_match_among_options():
    """For a real-world fuzzy input like "Iron Worker" (with space),
    should match "Ironworker"."""
    with patch("anthropic.Anthropic", side_effect=ImportError("no anthropic")):
        out = await map_classification("Iron Worker")
    assert out["suggested_davis_bacon"] == "Ironworker"


@pytest.mark.asyncio
async def test_map_classification_unrelated_input_low_confidence():
    """Wildly off input — fuzzy match returns the best of a bad lot
    with low confidence."""
    with patch("anthropic.Anthropic", side_effect=ImportError("no anthropic")):
        out = await map_classification("alien-job-title-xyz")
    # Whatever it picks, confidence should be < 0.5:
    assert out["confidence"] < 0.5


@pytest.mark.asyncio
async def test_map_classification_returns_dict_schema():
    with patch("anthropic.Anthropic", side_effect=ImportError("no anthropic")):
        out = await map_classification("plumber")
    assert "suggested_davis_bacon" in out
    assert "confidence" in out
    assert isinstance(out["confidence"], int | float)
