"""Tests for expanded cost database with DDC CWICR data.

Verifies:
    - Ingestion script transforms DDC data correctly
    - Collection-to-CSI mapping produces 2,500+ items across 14+ divisions
    - Unit normalization works for DDC metric units
    - DB query functions for cost lookup
    - Updated get_current_cost/match_costs/enrich_cost_item with DB parameter
    - CostItem model has new columns
    - Backward compatibility with REFERENCE_COSTS
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TESTING", "true")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear module-level caches before each test."""
    from app.services.estimating.cost_database import (
        _ppi_cache,
        _ppi_history_cache,
    )

    _ppi_cache.clear()
    _ppi_history_cache.clear()
    yield
    _ppi_cache.clear()
    _ppi_history_cache.clear()


# ---------------------------------------------------------------------------
# 1. Ingestion script: Collection mapping
# ---------------------------------------------------------------------------


class TestCollectionMapping:
    """Tests for DDC collection -> CSI MasterFormat mapping."""

    def test_mapping_has_at_least_60_collections(self):
        from scripts.ingest_ddc_cwicr import COLLECTION_TO_CSI

        assert len(COLLECTION_TO_CSI) >= 60, (
            f"Expected 60+ collection mappings, got {len(COLLECTION_TO_CSI)}"
        )

    def test_all_major_csi_divisions_covered(self):
        """Must cover divisions 03-33 (the 14 critical ones)."""
        from scripts.ingest_ddc_cwicr import COLLECTION_TO_CSI

        divisions = {v[0] for v in COLLECTION_TO_CSI.values()}
        for div in ["03", "04", "05", "06", "07", "08", "09", "22", "23", "26", "31", "32", "33"]:
            assert div in divisions, f"Missing CSI division {div}"

    def test_csi_code_format(self):
        """CSI codes should be in 'XX XX XX' format."""
        from scripts.ingest_ddc_cwicr import COLLECTION_TO_CSI

        for collection, (div, csi_code, _) in COLLECTION_TO_CSI.items():
            assert len(csi_code) == 8, f"Bad CSI code format for {collection}: {csi_code}"
            assert csi_code[2] == " " and csi_code[5] == " ", (
                f"CSI code {csi_code} should have spaces at positions 2,5"
            )
            assert csi_code[:2] == div, f"CSI code {csi_code} prefix doesn't match division {div}"


# ---------------------------------------------------------------------------
# 2. Ingestion: Unit normalization
# ---------------------------------------------------------------------------


class TestUnitNormalization:
    """Tests for DDC unit -> US construction unit mapping."""

    def test_metric_units_mapped(self):
        from scripts.ingest_ddc_cwicr import _normalize_unit

        unit, mult = _normalize_unit("m3")
        assert unit == "CY"
        assert mult == 1.0

    def test_multiplied_units(self):
        from scripts.ingest_ddc_cwicr import _normalize_unit

        unit, mult = _normalize_unit("100 m3")
        assert unit == "CY"
        assert mult == 0.01  # cost per 100 m3 -> per m3

    def test_1000_multiplier(self):
        from scripts.ingest_ddc_cwicr import _normalize_unit

        unit, mult = _normalize_unit("1000 m3")
        assert unit == "CY"
        assert mult == 0.001

    def test_pcs_to_each(self):
        from scripts.ingest_ddc_cwicr import _normalize_unit

        unit, mult = _normalize_unit("pcs")
        assert unit == "EA"
        assert mult == 1.0

    def test_ton_mapped(self):
        from scripts.ingest_ddc_cwicr import _normalize_unit

        unit, mult = _normalize_unit("t")
        assert unit == "TON"
        assert mult == 1.0

    def test_unknown_unit_passthrough(self):
        from scripts.ingest_ddc_cwicr import _normalize_unit

        unit, mult = _normalize_unit("something_weird")
        assert unit == "SOMETHING_WEIRD"
        assert mult == 1.0


# ---------------------------------------------------------------------------
# 3. Ingestion: Safe decimal conversion
# ---------------------------------------------------------------------------


class TestSafeDecimal:
    def test_normal_float(self):
        from scripts.ingest_ddc_cwicr import _safe_decimal

        result = _safe_decimal(123.456)
        assert result == Decimal("123.46")

    def test_nan_returns_none(self):
        from scripts.ingest_ddc_cwicr import _safe_decimal

        result = _safe_decimal(float("nan"))
        assert result is None

    def test_none_returns_none(self):
        from scripts.ingest_ddc_cwicr import _safe_decimal

        assert _safe_decimal(None) is None

    def test_inf_returns_none(self):
        from scripts.ingest_ddc_cwicr import _safe_decimal

        assert _safe_decimal(float("inf")) is None

    def test_zero(self):
        from scripts.ingest_ddc_cwicr import _safe_decimal

        assert _safe_decimal(0.0) == Decimal("0.0")


# ---------------------------------------------------------------------------
# 4. Ingestion: Full transform (uses actual parquet if available)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not pytest.importorskip("fastparquet", reason="fastparquet not installed"),
    reason="Requires fastparquet for parquet file support",
)
class TestIngestionTransform:
    """Tests for the full load_and_transform pipeline."""

    @pytest.fixture
    def parquet_path(self):
        path = (
            r"H:\ConstructAI\constructai\constructai-data\cost-data\ddc-cwicr"
            r"\OpenConstructionEstimate-DDC-CWICR\EN___DDC_CWICR"
            r"\ENG_TORONTO_workitems_costs_resources_DDC_CWICR.parquet"
        )
        if not os.path.exists(path):
            pytest.skip("DDC CWICR parquet file not available")
        return path

    def test_produces_at_least_2500_items(self, parquet_path):
        from scripts.ingest_ddc_cwicr import load_and_transform

        items = load_and_transform(parquet_path)
        assert len(items) >= 2500, f"Expected 2500+ items, got {len(items)}"

    def test_covers_14_csi_divisions(self, parquet_path):
        from scripts.ingest_ddc_cwicr import load_and_transform

        items = load_and_transform(parquet_path)
        divisions = {item["csi_code"][:2] for item in items if item.get("csi_code")}
        # At least the core building divisions
        assert len(divisions) >= 14, (
            f"Expected 14+ divisions, got {len(divisions)}: {sorted(divisions)}"
        )

    def test_items_have_required_fields(self, parquet_path):
        from scripts.ingest_ddc_cwicr import load_and_transform

        items = load_and_transform(parquet_path, limit=100)
        required = {"category", "description", "unit", "base_unit_cost", "csi_code", "data_source"}
        for item in items:
            for field in required:
                assert field in item, f"Missing field {field} in item"
            assert item["data_source"] == "ddc_cwicr"
            assert item["base_unit_cost"] > 0

    def test_items_have_cost_breakdown(self, parquet_path):
        from scripts.ingest_ddc_cwicr import load_and_transform

        items = load_and_transform(parquet_path, limit=100)
        has_material = sum(1 for i in items if i.get("material_cost") and i["material_cost"] > 0)
        has_labor = sum(1 for i in items if i.get("labor_cost") and i["labor_cost"] > 0)
        has_equipment = sum(1 for i in items if i.get("equipment_cost") and i["equipment_cost"] > 0)
        # At least some items should have each breakdown
        assert has_material > 0 or has_labor > 0 or has_equipment > 0, (
            "No items have any cost breakdown"
        )

    def test_limit_parameter(self, parquet_path):
        from scripts.ingest_ddc_cwicr import load_and_transform

        items = load_and_transform(parquet_path, limit=50)
        assert len(items) <= 50

    def test_metadata_contains_ddc_info(self, parquet_path):
        from scripts.ingest_ddc_cwicr import load_and_transform

        items = load_and_transform(parquet_path, limit=10)
        for item in items:
            meta = item.get("metadata", {})
            assert "ddc_rate_code" in meta
            assert "ddc_collection" in meta

    def test_uncertainty_bounds_set(self, parquet_path):
        from scripts.ingest_ddc_cwicr import load_and_transform

        items = load_and_transform(parquet_path, limit=50)
        for item in items:
            assert item["uncertainty_min"] is not None
            assert item["uncertainty_max"] is not None
            assert item["uncertainty_min"] > 0
            assert item["uncertainty_max"] > item["uncertainty_min"]


# ---------------------------------------------------------------------------
# 5. CostItem model: new columns
# ---------------------------------------------------------------------------


class TestCostItemModel:
    """Verify the CostItem model has all expanded columns."""

    def test_new_columns_exist(self):
        from app.models.estimating import CostItem

        new_cols = [
            "csi_code",
            "material_cost",
            "labor_cost",
            "equipment_cost",
            "unit_of_measure",
            "crew_size",
            "daily_output",
            "manhours_per_unit",
            "uncertainty_min",
            "uncertainty_max",
            "last_updated",
        ]
        mapper = CostItem.__table__.columns
        for col_name in new_cols:
            assert col_name in mapper, f"CostItem missing column: {col_name}"

    def test_existing_columns_preserved(self):
        from app.models.estimating import CostItem

        existing_cols = [
            "id",
            "category",
            "description",
            "unit",
            "base_unit_cost",
            "region",
            "bls_series_id",
            "data_source",
            "effective_date",
            "metadata",
            "created_at",
            "updated_at",
        ]
        mapper = CostItem.__table__.columns
        for col_name in existing_cols:
            assert col_name in mapper, f"CostItem missing existing column: {col_name}"

    def test_new_columns_are_nullable(self):
        from app.models.estimating import CostItem

        nullable_cols = [
            "csi_code",
            "material_cost",
            "labor_cost",
            "equipment_cost",
            "crew_size",
            "daily_output",
            "manhours_per_unit",
            "uncertainty_min",
            "uncertainty_max",
            "last_updated",
        ]
        for col_name in nullable_cols:
            col = CostItem.__table__.columns[col_name]
            assert col.nullable, f"{col_name} should be nullable"


# ---------------------------------------------------------------------------
# 6. Migration: structure
# ---------------------------------------------------------------------------


class TestMigration012:
    def test_migration_file_exists(self):
        from pathlib import Path

        migration_dir = Path(__file__).resolve().parent.parent / "alembic" / "versions"
        migration_file = migration_dir / "012_expand_cost_items.py"
        assert migration_file.exists(), f"Migration file not found: {migration_file}"

        content = migration_file.read_text()
        assert 'revision: str = "012"' in content
        assert 'down_revision: str = "011"' in content
        assert "csi_code" in content
        assert "material_cost" in content
        assert "labor_cost" in content
        assert "equipment_cost" in content


# ---------------------------------------------------------------------------
# 7. Updated public API — backward compatibility
# ---------------------------------------------------------------------------


class TestPublicAPIBackwardCompat:
    """Ensure the updated functions still work without a DB session."""

    @pytest.mark.asyncio
    async def test_get_current_cost_no_db(self):
        """get_current_cost without db= parameter should work as before."""
        from app.services.estimating.cost_database import get_current_cost

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = _mock_bls_response()
            result = await get_current_cost("concrete", "Ready-mix", "CY")
        assert result["unit_cost"] == 185.0
        assert result["data_source"] == "reference_costs"

    @pytest.mark.asyncio
    async def test_match_costs_no_db(self):
        """match_costs without db= parameter should work as before."""
        from app.services.estimating.cost_database import match_costs

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = _mock_bls_response()
            result = await match_costs(
                [
                    {"description": "Concrete slab", "csi_code": "03 30 00", "quantity": 100},
                ]
            )
        assert len(result) == 1
        assert result[0]["unit_cost"] > 0
        assert result[0]["data_source"] == "reference_costs"

    @pytest.mark.asyncio
    async def test_enrich_cost_item_no_db(self):
        """enrich_cost_item without db= parameter should work as before."""
        from app.services.estimating.cost_database import enrich_cost_item

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = _mock_bls_response()
            result = await enrich_cost_item("concrete", "CY")
        assert result["unit_cost"] == 185.0
        assert "uncertainty_low" in result
        assert "uncertainty_high" in result
        assert result["data_source"] == "reference_costs"

    @pytest.mark.asyncio
    async def test_get_current_cost_unknown_category(self):
        """Unknown category with no DB should still return zero gracefully."""
        from app.services.estimating.cost_database import get_current_cost

        result = await get_current_cost("nonexistent_xyz", "desc", "EA")
        assert result["unit_cost"] == 0.0
        assert result["data_source"] == "none"


# ---------------------------------------------------------------------------
# 8. DB query functions (mocked DB)
# ---------------------------------------------------------------------------


class TestDBQueryFunctions:
    """Test the DB query helpers with mocked sessions."""

    @pytest.mark.asyncio
    async def test_query_db_by_category_with_result(self):
        from app.services.estimating.cost_database import _query_db_by_category

        mock_item = _make_mock_cost_item(
            category="concrete",
            unit="CY",
            base_unit_cost=Decimal("185.00"),
            data_source="ddc_cwicr",
            csi_code="03 30 00",
            material_cost=Decimal("120.00"),
            labor_cost=Decimal("45.00"),
            equipment_cost=Decimal("20.00"),
        )
        db = _mock_db_session(scalar_result=mock_item)

        result = await _query_db_by_category("concrete", "CY", db=db)
        assert result is not None
        assert result["base_cost"] == 185.0
        assert result["material_cost"] == 120.0
        assert result["data_source"] == "ddc_cwicr"

    @pytest.mark.asyncio
    async def test_query_db_by_category_no_result(self):
        from app.services.estimating.cost_database import _query_db_by_category

        db = _mock_db_session(scalar_result=None)
        result = await _query_db_by_category("nonexistent", "EA", db=db)
        assert result is None

    @pytest.mark.asyncio
    async def test_query_db_no_session(self):
        from app.services.estimating.cost_database import _query_db_by_category

        result = await _query_db_by_category("concrete", "CY", db=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_query_db_by_csi(self):
        from app.services.estimating.cost_database import _query_db_by_csi

        mock_item = _make_mock_cost_item(
            category="concrete",
            unit="CY",
            base_unit_cost=Decimal("185.00"),
            csi_code="03 30 00",
        )
        db = _mock_db_session(scalars_result=[mock_item])

        result = await _query_db_by_csi("03 30", db=db)
        assert result is not None
        assert len(result) == 1
        assert result[0]["csi_code"] == "03 30 00"

    @pytest.mark.asyncio
    async def test_search_db_by_description(self):
        from app.services.estimating.cost_database import _search_db_by_description

        mock_item = _make_mock_cost_item(
            category="earthwork",
            description="Bulk excavation soil group 1",
            unit="CY",
            base_unit_cost=Decimal("12.50"),
            csi_code="31 23 00",
        )
        db = _mock_db_session(scalars_result=[mock_item])

        result = await _search_db_by_description("excavation soil", "31", db=db)
        assert result is not None
        assert len(result) == 1
        assert result[0]["category"] == "earthwork"


# ---------------------------------------------------------------------------
# 9. get_current_cost with DB (mocked)
# ---------------------------------------------------------------------------


class TestGetCurrentCostWithDB:
    @pytest.mark.asyncio
    async def test_db_item_preferred_over_reference(self):
        """When DB has a match, it should be used instead of REFERENCE_COSTS."""
        from app.services.estimating.cost_database import get_current_cost

        mock_item = _make_mock_cost_item(
            category="earthwork",
            unit="CY",
            base_unit_cost=Decimal("15.00"),
            data_source="ddc_cwicr",
            csi_code="31 23 00",
            material_cost=Decimal("2.00"),
            labor_cost=Decimal("5.00"),
            equipment_cost=Decimal("8.00"),
        )
        db = _mock_db_session(scalar_result=mock_item)

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = _mock_bls_response()
            result = await get_current_cost("earthwork", "bulk excavation", "CY", db=db)

        assert result["data_source"] == "ddc_cwicr"
        assert result["unit_cost"] == 15.0
        assert "material_cost" in result
        assert "labor_cost" in result
        assert "equipment_cost" in result

    @pytest.mark.asyncio
    async def test_fallback_to_reference_costs(self):
        """When DB returns nothing, should fall back to REFERENCE_COSTS."""
        from app.services.estimating.cost_database import get_current_cost

        db = _mock_db_session(scalar_result=None)

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value = _mock_bls_response()
            result = await get_current_cost("concrete", "Ready-mix", "CY", db=db)

        assert result["unit_cost"] == 185.0
        assert result["data_source"] == "reference_costs"


# ---------------------------------------------------------------------------
# 10. Ingestion statistics
# ---------------------------------------------------------------------------


class TestIngestionStatistics:
    def test_log_statistics_runs(self):
        from scripts.ingest_ddc_cwicr import log_statistics

        items = [
            {
                "category": "concrete",
                "csi_code": "03 30 00",
                "base_unit_cost": Decimal("185.00"),
            },
            {
                "category": "earthwork",
                "csi_code": "31 23 00",
                "base_unit_cost": Decimal("12.00"),
            },
        ]
        # Should not raise
        log_statistics(items)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_bls_response():
    """Create a mock httpx response for BLS API."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "status": "REQUEST_SUCCEEDED",
        "Results": {
            "series": [
                {
                    "seriesID": "WPUIP2300001",
                    "data": [
                        {
                            "year": "2026",
                            "period": "M01",
                            "periodName": "January",
                            "value": "120.0",
                        }
                    ],
                }
            ],
        },
    }
    return mock_resp


def _make_mock_cost_item(**kwargs):
    """Create a mock CostItem-like object."""
    defaults = {
        "id": uuid.uuid4(),
        "category": "concrete",
        "description": "Test item",
        "unit": "CY",
        "base_unit_cost": Decimal("100.00"),
        "region": "national",
        "bls_series_id": None,
        "data_source": "ddc_cwicr",
        "effective_date": date.today(),
        "csi_code": "03 30 00",
        "material_cost": None,
        "labor_cost": None,
        "equipment_cost": None,
        "unit_of_measure": None,
        "crew_size": None,
        "daily_output": None,
        "manhours_per_unit": None,
        "uncertainty_min": Decimal("0.08"),
        "uncertainty_max": Decimal("0.12"),
        "last_updated": datetime.now(UTC),
    }
    defaults.update(kwargs)
    item = MagicMock()
    for k, v in defaults.items():
        setattr(item, k, v)
    return item


def _mock_db_session(scalar_result=None, scalars_result=None):
    """Create a mock AsyncSession that returns the given result.

    Note: Result.scalar_one_or_none() and Result.scalars().all() are
    synchronous methods on the result object, so we use MagicMock for them,
    while session.execute() is async so we use AsyncMock.
    """
    db = AsyncMock()
    mock_result = MagicMock()

    if scalars_result is not None:
        mock_result.scalars.return_value.all.return_value = scalars_result
    else:
        mock_result.scalar_one_or_none.return_value = scalar_result

    db.execute = AsyncMock(return_value=mock_result)
    return db
