"""Phase 2: Site layout optimization tests.

Tests for the NSGA-II multi-objective site layout optimizer.
Uses DEAP for the genetic algorithm, no external API calls.
"""

from __future__ import annotations

import pytest

deap = pytest.importorskip("deap", reason="DEAP is required for site layout optimization tests")

from app.services.logistics.site_layout import evaluate_layout, optimize_site_layout
from tests.fixtures.precon_mock_responses import (
    MOCK_FACILITIES,
    MOCK_SITE_BOUNDARY,
)


class TestSiteLayout:
    """Tests for the site layout optimization service."""

    async def test_optimize_returns_layouts(self):
        """Optimization should return at least one layout solution."""
        result = await optimize_site_layout(
            MOCK_FACILITIES,
            MOCK_SITE_BOUNDARY,
            {},
            population_size=10,
            generations=5,
        )
        assert "layouts" in result
        assert len(result["layouts"]) > 0

    async def test_optimize_pareto_front(self):
        """Optimization should return Pareto front data."""
        result = await optimize_site_layout(
            MOCK_FACILITIES,
            MOCK_SITE_BOUNDARY,
            {},
            population_size=10,
            generations=5,
        )
        assert "pareto_front" in result
        assert len(result["pareto_front"]) > 0

    async def test_optimize_no_movable_facilities(self):
        """All-fixed facilities should return empty layouts."""
        all_fixed = [
            {
                "id": "office",
                "name": "Office",
                "type": "admin",
                "width": 12,
                "length": 6,
                "fixed": True,
                "x": 5,
                "y": 5,
            },
            {
                "id": "crane",
                "name": "Crane",
                "type": "equipment",
                "width": 4,
                "length": 4,
                "fixed": True,
                "x": 50,
                "y": 50,
            },
        ]
        result = await optimize_site_layout(
            all_fixed,
            MOCK_SITE_BOUNDARY,
            {},
            population_size=10,
            generations=5,
        )
        assert result["layouts"] == []

    def test_evaluate_layout_returns_three_objectives(self):
        """evaluate_layout should return a tuple of 3 objective values."""
        positions = [(30, 30), (70, 70), (20, 80)]
        scores = evaluate_layout(positions, MOCK_FACILITIES, MOCK_SITE_BOUNDARY, {})
        assert isinstance(scores, tuple)
        assert len(scores) == 3

    def test_evaluate_layout_all_positive(self):
        """All objective values should be non-negative."""
        positions = [(50, 50), (20, 20), (80, 80)]
        scores = evaluate_layout(positions, MOCK_FACILITIES, MOCK_SITE_BOUNDARY, {})
        assert all(s >= 0 for s in scores)

    async def test_optimize_returns_metadata(self):
        """Result should include generation count and population size."""
        result = await optimize_site_layout(
            MOCK_FACILITIES,
            MOCK_SITE_BOUNDARY,
            {},
            population_size=10,
            generations=5,
        )
        assert result["generations"] == 5
        assert result["population_size"] == 10
