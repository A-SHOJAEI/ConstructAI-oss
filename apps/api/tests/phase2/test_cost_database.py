"""Phase 2: Cost database and BLS PPI integration tests.

Tests for cost lookup, regional adjustments, PPI factors, and cost matching.
Uses the mock PPI data built into the service module.
"""

from __future__ import annotations

import os

import pytest

from app.services.estimating.cost_database import (
    fetch_bls_ppi,
    get_current_cost,
    match_costs,
)


@pytest.fixture
def bls_api_key():
    """Return the BLS_API_KEY from environment, skip if not set."""
    key = os.environ.get("BLS_API_KEY")
    if not key:
        pytest.skip("BLS_API_KEY not set; skipping live API tests")
    return key


class TestCostDatabase:
    """Tests for the cost database service."""

    async def test_get_current_cost_concrete(self):
        """Should return a valid cost entry for concrete."""
        result = await get_current_cost("concrete", "Ready-mix", "CY")
        assert "unit_cost" in result
        assert result["unit_cost"] > 0
        assert result["data_source"] == "reference_costs"

    async def test_get_current_cost_region_adjustment(self):
        """Northeast region should have higher adjusted cost than national."""
        national = await get_current_cost("concrete", "Ready-mix", "CY", "national")
        northeast = await get_current_cost("concrete", "Ready-mix", "CY", "northeast")
        assert northeast["adjusted_cost"] > national["adjusted_cost"]

    async def test_get_current_cost_unknown_category(self):
        """Unknown category should return zero unit cost."""
        result = await get_current_cost("unobtanium", "Exotic material", "CY")
        assert result["unit_cost"] == 0.0
        assert result["data_source"] == "none"

    async def test_match_costs_returns_enriched_quantities(self):
        """match_costs should add unit_cost and total_cost to each quantity."""
        quantities = [
            {"csi_code": "03 30 00", "description": "concrete", "quantity": 100, "unit": "CY"},
        ]
        result = await match_costs(quantities)
        assert len(result) > 0
        assert "unit_cost" in result[0]
        assert "total_cost" in result[0]
        assert result[0]["total_cost"] > 0

    async def test_match_costs_multiple_items(self):
        """match_costs should process multiple line items."""
        quantities = [
            {"csi_code": "03 30 00", "description": "concrete", "quantity": 100, "unit": "CY"},
            {"csi_code": "05 12 00", "description": "steel", "quantity": 10, "unit": "TON"},
        ]
        result = await match_costs(quantities)
        assert len(result) == 2
        assert all("total_cost" in item for item in result)

    async def test_fetch_bls_ppi_returns_data(self, bls_api_key):
        """fetch_bls_ppi should return PPI data with a valid factor."""
        result = await fetch_bls_ppi("PCU236211236211")
        assert "ppi_factor" in result
        assert result["ppi_factor"] > 0
        assert result["series_id"] == "PCU236211236211"

    async def test_fetch_bls_ppi_unknown_series(self):
        """Unknown PPI series should raise BLSDataUnavailableError (no mock fallback)."""
        import pytest

        from app.services.estimating.cost_database import BLSDataUnavailableError

        with pytest.raises(BLSDataUnavailableError):
            await fetch_bls_ppi("UNKNOWN_SERIES")
