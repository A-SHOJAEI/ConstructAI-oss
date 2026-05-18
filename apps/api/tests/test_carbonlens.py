"""Comprehensive tests for CarbonLens — LEED v5 embodied carbon tracking.

Covers:
    - Model creation and defaults
    - Baseline GWP lookup from MATERIAL_BASELINES
    - Auto-categorization from CSI division and material type
    - Project configuration (create, update, baseline calculation)
    - Material inventory (add with auto GWP, total calc, improvement calc)
    - Material update with recalculation
    - EPD management (upload, parse with mocked LLM, verify)
    - GWP calculation (sum, per-sf, baseline comparison, hotspot, category breakdown)
    - What-if scenario modelling
    - MRp2 report generation
    - Dashboard aggregation
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TESTING", "true")

from app.models.carbon import (
    CarbonMaterialInventory,
    CarbonReport,
    EpdRecord,
    ProjectCarbonConfig,
)

from app.services.products.carbonlens.service import (
    BUILDING_TYPE_BASELINES,
    CSI_CATEGORY_MAP,
    MATERIAL_BASELINES,
    _calc_improvement_pct,
    _lookup_baseline_gwp,
    auto_categorize_material,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# 1. Model creation & defaults
# ---------------------------------------------------------------------------


class TestCarbonModels:
    """Tests for SQLAlchemy model instantiation and defaults."""

    def test_project_carbon_config_defaults(self):
        config = ProjectCarbonConfig(
            project_id=_uuid(),
            organization_id=_uuid(),
        )
        assert config.leed_version is None  # server_default, not Python default
        assert config.building_area_sf is None
        assert config.target_certification is None
        assert config.baseline_gwp_kgco2e is None

    def test_carbon_material_inventory_required_fields(self):
        m = CarbonMaterialInventory(
            project_id=_uuid(),
            organization_id=_uuid(),
            material_category="structure",
            material_type="ready-mix concrete 4000psi",
        )
        assert m.material_category == "structure"
        assert m.material_type == "ready-mix concrete 4000psi"
        # server_default values populate on flush+refresh, not at instantiation,
        # so the attribute is None on a fresh ORM instance.
        assert m.is_carbon_hotspot is None

    def test_epd_record_defaults(self):
        epd = EpdRecord(
            project_id=_uuid(),
            organization_id=_uuid(),
        )
        assert epd.verification_status is None  # server_default
        assert epd.gwp_a1_a3 is None
        assert epd.verified_by is None

    def test_carbon_report_required_field(self):
        r = CarbonReport(
            project_id=_uuid(),
            organization_id=_uuid(),
            report_type="mrp2_prerequisite",
        )
        assert r.report_type == "mrp2_prerequisite"
        assert r.total_gwp_kgco2e is None

    def test_carbon_material_all_fields(self):
        epd_id = _uuid()
        m = CarbonMaterialInventory(
            project_id=_uuid(),
            organization_id=_uuid(),
            material_category="enclosure",
            material_type="aluminum curtain wall",
            csi_division="08",
            quantity=Decimal("500.0000"),
            unit="SF",
            supplier="Kawneer",
            manufacturer="Kawneer",
            product_name="1600 System",
            epd_id=epd_id,
            gwp_per_unit=Decimal("55.0000"),
            total_gwp=Decimal("27500.0000"),
            baseline_gwp_per_unit=Decimal("55.0000"),
            improvement_pct=Decimal("0.00"),
            procurement_status="ordered",
        )
        assert m.epd_id == epd_id
        assert m.procurement_status == "ordered"


# ---------------------------------------------------------------------------
# 2. Material baselines
# ---------------------------------------------------------------------------


class TestMaterialBaselines:
    """Tests for baseline GWP lookup."""

    def test_lookup_existing_material(self):
        result = _lookup_baseline_gwp("ready-mix concrete 4000psi")
        assert result is not None
        assert result["gwp"] == 350.0
        assert result["unit"] == "CY"

    def test_lookup_case_insensitive(self):
        result = _lookup_baseline_gwp("Ready-Mix Concrete 4000psi")
        assert result is not None
        assert result["gwp"] == 350.0

    def test_lookup_missing_material(self):
        result = _lookup_baseline_gwp("exotic marble")
        assert result is None

    def test_carbon_sequestering_wood(self):
        result = _lookup_baseline_gwp("dimensional lumber")
        assert result is not None
        assert result["gwp"] < 0, "Wood products should have negative GWP (sequester carbon)"
        assert result["gwp"] == -350.0


# ---------------------------------------------------------------------------
# 3. Auto-categorization
# ---------------------------------------------------------------------------


class TestAutoCategorizeMaterial:
    """Tests for CSI division mapping and keyword fallback."""

    def test_csi_division_03_structure(self):
        assert auto_categorize_material(csi_division="03") == "structure"

    def test_csi_division_07_enclosure(self):
        assert auto_categorize_material(csi_division="07") == "enclosure"

    def test_csi_division_32_hardscape(self):
        assert auto_categorize_material(csi_division="32") == "hardscape"

    def test_keyword_fallback_concrete(self):
        assert auto_categorize_material(material_type="ready-mix concrete") == "structure"

    def test_keyword_fallback_insulation(self):
        assert auto_categorize_material(material_type="rigid insulation xps") == "enclosure"

    def test_keyword_fallback_paving(self):
        assert auto_categorize_material(material_type="asphalt paving") == "hardscape"

    def test_default_structure(self):
        """Unknown material with no CSI defaults to structure."""
        assert auto_categorize_material(material_type="unknown widget") == "structure"

    def test_csi_overrides_keyword(self):
        """CSI division takes priority over keyword."""
        assert (
            auto_categorize_material(csi_division="07", material_type="concrete sealer")
            == "enclosure"
        )


# ---------------------------------------------------------------------------
# 4. configure_project
# ---------------------------------------------------------------------------


class TestConfigureProject:
    """Tests for project carbon configuration."""

    @pytest.mark.asyncio
    async def test_create_config(self):
        db = AsyncMock()
        pid, oid = _uuid(), _uuid()

        # Simulate no existing config
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        from app.services.products.carbonlens.service import configure_project

        await configure_project(db, pid, oid, {"leed_version": "v5"})
        assert db.add.called
        assert db.flush.called

    @pytest.mark.asyncio
    async def test_update_existing_config(self):
        db = AsyncMock()
        existing = ProjectCarbonConfig(
            id=_uuid(),
            project_id=_uuid(),
            organization_id=_uuid(),
            leed_version="v5",
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute.return_value = mock_result

        from app.services.products.carbonlens.service import configure_project

        config = await configure_project(
            db,
            existing.project_id,
            existing.organization_id,
            {"target_certification": "gold"},
        )
        assert config.target_certification == "gold"

    @pytest.mark.asyncio
    async def test_auto_baseline_from_area(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        from app.services.products.carbonlens.service import configure_project

        config = await configure_project(
            db,
            _uuid(),
            _uuid(),
            {"building_area_sf": 50000},
        )
        # Should auto-calculate: 50000 * 35.0 (office default) = 1,750,000
        assert config.baseline_gwp_kgco2e == Decimal("1750000.0")

    @pytest.mark.asyncio
    async def test_no_auto_baseline_if_explicitly_set(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        from app.services.products.carbonlens.service import configure_project

        config = await configure_project(
            db,
            _uuid(),
            _uuid(),
            {"building_area_sf": 50000, "baseline_gwp_kgco2e": 1000000},
        )
        assert config.baseline_gwp_kgco2e == 1000000


# ---------------------------------------------------------------------------
# 5. add_material
# ---------------------------------------------------------------------------


class TestAddMaterial:
    """Tests for material creation with auto GWP lookup."""

    @pytest.mark.asyncio
    async def test_auto_gwp_from_baselines(self):
        db = AsyncMock()
        from app.services.products.carbonlens.service import add_material

        material = await add_material(
            db,
            _uuid(),
            _uuid(),
            {
                "material_type": "ready-mix concrete 4000psi",
                "material_category": "structure",
                "quantity": 100,
                "unit": "CY",
            },
        )
        assert material.gwp_per_unit == Decimal("350.0")
        assert material.total_gwp == Decimal("35000.0")
        assert material.baseline_gwp_per_unit == Decimal("350.0")

    @pytest.mark.asyncio
    async def test_improvement_pct_zero_for_baseline_match(self):
        db = AsyncMock()
        from app.services.products.carbonlens.service import add_material

        material = await add_material(
            db,
            _uuid(),
            _uuid(),
            {
                "material_type": "structural steel w-shapes",
                "material_category": "structure",
                "quantity": 50,
                "unit": "TON",
            },
        )
        # GWP matches baseline, so improvement = 0%
        assert material.improvement_pct == Decimal("0.0")

    @pytest.mark.asyncio
    async def test_auto_categorize_from_csi(self):
        db = AsyncMock()
        from app.services.products.carbonlens.service import add_material

        material = await add_material(
            db,
            _uuid(),
            _uuid(),
            {
                "material_type": "custom material",
                "csi_division": "07",
                "quantity": 200,
                "unit": "SF",
            },
        )
        assert material.material_category == "enclosure"

    @pytest.mark.asyncio
    async def test_unknown_material_no_baseline(self):
        db = AsyncMock()
        from app.services.products.carbonlens.service import add_material

        material = await add_material(
            db,
            _uuid(),
            _uuid(),
            {
                "material_type": "exotic tile",
                "material_category": "enclosure",
                "quantity": 500,
                "unit": "SF",
            },
        )
        assert material.gwp_per_unit is None
        assert material.total_gwp is None
        assert material.baseline_gwp_per_unit is None

    @pytest.mark.asyncio
    async def test_negative_gwp_wood(self):
        db = AsyncMock()
        from app.services.products.carbonlens.service import add_material

        material = await add_material(
            db,
            _uuid(),
            _uuid(),
            {
                "material_type": "dimensional lumber",
                "material_category": "structure",
                "quantity": 10,
                "unit": "TON",
            },
        )
        assert float(material.gwp_per_unit) < 0
        assert float(material.total_gwp) == -3500.0

    @pytest.mark.asyncio
    async def test_total_gwp_calculation(self):
        db = AsyncMock()
        from app.services.products.carbonlens.service import add_material

        material = await add_material(
            db,
            _uuid(),
            _uuid(),
            {
                "material_type": "steel rebar",
                "material_category": "structure",
                "quantity": 25,
                "unit": "TON",
            },
        )
        # 25 TON * 1100 kgCO2e/TON = 27,500
        assert float(material.total_gwp) == 27500.0


# ---------------------------------------------------------------------------
# 6. update_material
# ---------------------------------------------------------------------------


class TestUpdateMaterial:
    """Tests for material update with recalculation."""

    @pytest.mark.asyncio
    async def test_quantity_change_recalculates_total(self):
        db = AsyncMock()
        pid = _uuid()
        mid = _uuid()
        existing = CarbonMaterialInventory(
            id=mid,
            project_id=pid,
            organization_id=_uuid(),
            material_category="structure",
            material_type="ready-mix concrete 4000psi",
            quantity=Decimal("100"),
            unit="CY",
            gwp_per_unit=Decimal("350.0"),
            total_gwp=Decimal("35000.0"),
            baseline_gwp_per_unit=Decimal("350.0"),
            improvement_pct=Decimal("0.0"),
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute.return_value = mock_result

        from app.services.products.carbonlens.service import update_material

        updated = await update_material(db, mid, pid, {"quantity": Decimal("200")})
        assert float(updated.total_gwp) == 70000.0

    @pytest.mark.asyncio
    async def test_supplier_update_no_recalc_needed(self):
        db = AsyncMock()
        pid = _uuid()
        mid = _uuid()
        existing = CarbonMaterialInventory(
            id=mid,
            project_id=pid,
            organization_id=_uuid(),
            material_category="structure",
            material_type="steel rebar",
            quantity=Decimal("10"),
            unit="TON",
            gwp_per_unit=Decimal("1100.0"),
            total_gwp=Decimal("11000.0"),
            baseline_gwp_per_unit=Decimal("1100.0"),
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute.return_value = mock_result

        from app.services.products.carbonlens.service import update_material

        updated = await update_material(db, mid, pid, {"supplier": "ACME Steel"})
        assert updated.supplier == "ACME Steel"

    @pytest.mark.asyncio
    async def test_update_not_found_raises(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        from app.services.products.carbonlens.service import update_material

        with pytest.raises(ValueError, match="not found"):
            await update_material(db, _uuid(), _uuid(), {"quantity": Decimal("50")})


# ---------------------------------------------------------------------------
# 7. EPD management
# ---------------------------------------------------------------------------


class TestEpdManagement:
    """Tests for EPD upload, parse, and verify."""

    @pytest.mark.asyncio
    async def test_upload_epd(self):
        db = AsyncMock()
        from app.services.products.carbonlens.service import upload_epd

        epd = await upload_epd(db, _uuid(), _uuid(), "Holcim", "ECOPact", "epds/test.pdf")
        assert epd.supplier == "Holcim"
        assert epd.pdf_s3_key == "epds/test.pdf"
        assert db.add.called

    @pytest.mark.asyncio
    async def test_parse_epd_llm_failure_fallback(self):
        """When LLM fails, extraction should be empty dict."""
        db = AsyncMock()
        epd_id = _uuid()
        pid = _uuid()
        existing_epd = EpdRecord(
            id=epd_id,
            project_id=pid,
            organization_id=_uuid(),
            pdf_s3_key="epds/test.pdf",
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_epd
        db.execute.return_value = mock_result

        from app.services.products.carbonlens.service import parse_epd

        # openai import will fail in test env -> fallback to empty extraction
        epd = await parse_epd(db, epd_id, "Sample EPD text content", pid, _uuid())
        assert epd.ai_extracted_data == {}

    @pytest.mark.asyncio
    async def test_parse_epd_with_mock_llm(self):
        """When LLM returns valid JSON, extracted data should be applied."""
        db = AsyncMock()
        epd_id = _uuid()
        pid = _uuid()
        existing_epd = EpdRecord(
            id=epd_id,
            project_id=pid,
            organization_id=_uuid(),
            pdf_s3_key="epds/test.pdf",
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_epd
        db.execute.return_value = mock_result

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"gwp_a1_a3": 315.5, "declared_unit": "m3", "manufacturer": "Holcim"}'
                )
            )
        ]
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("app.services.products.carbonlens.service.openai") as mock_openai:
            mock_openai.AsyncOpenAI.return_value = mock_client

            from app.services.products.carbonlens.service import parse_epd

            epd = await parse_epd(db, epd_id, "GWP A1-A3: 315.5 kgCO2e/m3", pid, _uuid())

        assert epd.gwp_a1_a3 == Decimal("315.5")
        assert epd.declared_unit == "m3"
        assert epd.manufacturer == "Holcim"

    @pytest.mark.asyncio
    async def test_verify_epd(self):
        db = AsyncMock()
        epd_id = _uuid()
        user_id = _uuid()
        existing_epd = EpdRecord(
            id=epd_id,
            project_id=_uuid(),
            organization_id=_uuid(),
            verification_status="pending",
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_epd
        db.execute.return_value = mock_result

        from app.services.products.carbonlens.service import verify_epd

        epd = await verify_epd(db, epd_id, user_id)
        assert epd.verification_status == "verified"
        assert epd.verified_by == user_id
        assert epd.verified_at is not None

    @pytest.mark.asyncio
    async def test_verify_epd_not_found(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        from app.services.products.carbonlens.service import verify_epd

        with pytest.raises(ValueError, match="not found"):
            await verify_epd(db, _uuid(), _uuid())


# ---------------------------------------------------------------------------
# 8. GWP calculation
# ---------------------------------------------------------------------------


class TestCalculateGwp:
    """Tests for GWP aggregation."""

    @pytest.mark.asyncio
    async def test_sum_total_gwp(self):
        db = AsyncMock()
        pid = _uuid()
        oid = _uuid()

        materials = [
            CarbonMaterialInventory(
                id=_uuid(),
                project_id=pid,
                organization_id=oid,
                material_category="structure",
                material_type="concrete",
                total_gwp=Decimal("10000"),
            ),
            CarbonMaterialInventory(
                id=_uuid(),
                project_id=pid,
                organization_id=oid,
                material_category="structure",
                material_type="steel",
                total_gwp=Decimal("5000"),
            ),
        ]

        # Mock list_materials
        with patch(
            "app.services.products.carbonlens.service.list_materials",
            return_value=materials,
        ):
            config = ProjectCarbonConfig(project_id=pid, organization_id=oid, building_area_sf=None)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = config
            db.execute.return_value = mock_result

            from app.services.products.carbonlens.service import calculate_gwp

            result = await calculate_gwp(db, pid)

        assert result["total_gwp_kgco2e"] == 15000.0

    @pytest.mark.asyncio
    async def test_gwp_per_sf(self):
        db = AsyncMock()
        pid = _uuid()
        oid = _uuid()

        materials = [
            CarbonMaterialInventory(
                id=_uuid(),
                project_id=pid,
                organization_id=oid,
                material_category="structure",
                material_type="concrete",
                total_gwp=Decimal("100000"),
            ),
        ]

        with patch(
            "app.services.products.carbonlens.service.list_materials",
            return_value=materials,
        ):
            config = ProjectCarbonConfig(
                project_id=pid,
                organization_id=oid,
                building_area_sf=Decimal("5000"),
            )
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = config
            db.execute.return_value = mock_result

            from app.services.products.carbonlens.service import calculate_gwp

            result = await calculate_gwp(db, pid)

        assert result["gwp_per_sf"] == 20.0  # 100000 / 5000

    @pytest.mark.asyncio
    async def test_baseline_comparison(self):
        db = AsyncMock()
        pid = _uuid()
        oid = _uuid()

        materials = [
            CarbonMaterialInventory(
                id=_uuid(),
                project_id=pid,
                organization_id=oid,
                material_category="structure",
                material_type="concrete",
                total_gwp=Decimal("90000"),
            ),
        ]

        with patch(
            "app.services.products.carbonlens.service.list_materials",
            return_value=materials,
        ):
            config = ProjectCarbonConfig(
                project_id=pid,
                organization_id=oid,
                baseline_gwp_kgco2e=Decimal("100000"),
            )
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = config
            db.execute.return_value = mock_result

            from app.services.products.carbonlens.service import calculate_gwp

            result = await calculate_gwp(db, pid)

        assert result["baseline_comparison_pct"] == -10.0  # 10% below baseline

    @pytest.mark.asyncio
    async def test_hotspot_detection_top_3(self):
        db = AsyncMock()
        pid = _uuid()
        oid = _uuid()

        materials = [
            CarbonMaterialInventory(
                id=_uuid(),
                project_id=pid,
                organization_id=oid,
                material_category="structure",
                material_type=f"material_{i}",
                total_gwp=Decimal(str(gwp)),
            )
            for i, gwp in enumerate([1000, 5000, 3000, 500, 8000])
        ]

        with patch(
            "app.services.products.carbonlens.service.list_materials",
            return_value=materials,
        ):
            config = ProjectCarbonConfig(project_id=pid, organization_id=oid)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = config
            db.execute.return_value = mock_result

            from app.services.products.carbonlens.service import calculate_gwp

            result = await calculate_gwp(db, pid)

        hotspots = result["hotspot_materials"]
        assert len(hotspots) == 3
        # Should be ordered by abs(total_gwp) desc
        assert hotspots[0]["total_gwp"] == 8000.0
        assert hotspots[1]["total_gwp"] == 5000.0
        assert hotspots[2]["total_gwp"] == 3000.0

    @pytest.mark.asyncio
    async def test_category_breakdown(self):
        db = AsyncMock()
        pid = _uuid()
        oid = _uuid()

        materials = [
            CarbonMaterialInventory(
                id=_uuid(),
                project_id=pid,
                organization_id=oid,
                material_category="structure",
                material_type="concrete",
                total_gwp=Decimal("10000"),
            ),
            CarbonMaterialInventory(
                id=_uuid(),
                project_id=pid,
                organization_id=oid,
                material_category="enclosure",
                material_type="curtain wall",
                total_gwp=Decimal("5000"),
            ),
        ]

        with patch(
            "app.services.products.carbonlens.service.list_materials",
            return_value=materials,
        ):
            config = ProjectCarbonConfig(project_id=pid, organization_id=oid)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = config
            db.execute.return_value = mock_result

            from app.services.products.carbonlens.service import calculate_gwp

            result = await calculate_gwp(db, pid)

        breakdown = result["category_breakdown"]
        assert len(breakdown) == 2
        structure = next(b for b in breakdown if b["category"] == "structure")
        assert structure["total_gwp"] == 10000.0

    @pytest.mark.asyncio
    async def test_empty_materials(self):
        db = AsyncMock()
        pid = _uuid()

        with patch(
            "app.services.products.carbonlens.service.list_materials",
            return_value=[],
        ):
            config = ProjectCarbonConfig(project_id=pid, organization_id=_uuid())
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = config
            db.execute.return_value = mock_result

            from app.services.products.carbonlens.service import calculate_gwp

            result = await calculate_gwp(db, pid)

        assert result["total_gwp_kgco2e"] == 0
        assert result["hotspot_materials"] == []
        assert result["category_breakdown"] == []


# ---------------------------------------------------------------------------
# 9. Scenario modelling
# ---------------------------------------------------------------------------


class TestModelScenario:
    """Tests for what-if scenario substitution."""

    @pytest.mark.asyncio
    async def test_substitution_reduces_gwp(self):
        db = AsyncMock()
        pid = _uuid()
        mid = _uuid()

        material = CarbonMaterialInventory(
            id=mid,
            project_id=pid,
            organization_id=_uuid(),
            material_category="structure",
            material_type="ready-mix concrete 4000psi",
            quantity=Decimal("100"),
            unit="CY",
            gwp_per_unit=Decimal("350.0"),
            total_gwp=Decimal("35000.0"),
        )

        with patch(
            "app.services.products.carbonlens.service.calculate_gwp",
            return_value={
                "total_gwp_kgco2e": 50000.0,
                "gwp_per_sf": None,
                "baseline_comparison_pct": 0.0,
                "hotspot_materials": [],
                "category_breakdown": [],
            },
        ):
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = material
            db.execute.return_value = mock_result

            from app.services.products.carbonlens.service import model_scenario

            result = await model_scenario(db, pid, mid, 220.0)

        # Original: 50000, scenario: 50000 - 35000 + (220*100) = 37000
        assert result["original_gwp"] == 50000.0
        assert result["scenario_gwp"] == 37000.0
        assert result["delta_pct"] == -26.0  # 26% reduction

    @pytest.mark.asyncio
    async def test_substitution_increases_gwp(self):
        db = AsyncMock()
        pid = _uuid()
        mid = _uuid()

        material = CarbonMaterialInventory(
            id=mid,
            project_id=pid,
            organization_id=_uuid(),
            material_category="structure",
            material_type="low-carbon concrete",
            quantity=Decimal("100"),
            unit="CY",
            gwp_per_unit=Decimal("220.0"),
            total_gwp=Decimal("22000.0"),
        )

        with patch(
            "app.services.products.carbonlens.service.calculate_gwp",
            return_value={
                "total_gwp_kgco2e": 50000.0,
                "gwp_per_sf": None,
                "baseline_comparison_pct": 0.0,
                "hotspot_materials": [],
                "category_breakdown": [],
            },
        ):
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = material
            db.execute.return_value = mock_result

            from app.services.products.carbonlens.service import model_scenario

            result = await model_scenario(db, pid, mid, 400.0)

        # Original: 50000, scenario: 50000 - 22000 + 40000 = 68000
        assert result["scenario_gwp"] == 68000.0
        assert result["delta_pct"] == 36.0  # 36% increase

    @pytest.mark.asyncio
    async def test_scenario_material_not_found(self):
        db = AsyncMock()

        with patch(
            "app.services.products.carbonlens.service.calculate_gwp",
            return_value={
                "total_gwp_kgco2e": 50000.0,
                "gwp_per_sf": None,
                "baseline_comparison_pct": 0.0,
                "hotspot_materials": [],
                "category_breakdown": [],
            },
        ):
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            db.execute.return_value = mock_result

            from app.services.products.carbonlens.service import model_scenario

            with pytest.raises(ValueError, match="not found"):
                await model_scenario(db, _uuid(), _uuid(), 300.0)


# ---------------------------------------------------------------------------
# 10. MRp2 report generation
# ---------------------------------------------------------------------------


class TestMrp2Report:
    """Tests for LEED MRp2 prerequisite report generation."""

    @pytest.mark.asyncio
    async def test_report_with_high_epd_coverage(self):
        db = AsyncMock()
        pid = _uuid()
        oid = _uuid()
        epd_id = _uuid()

        materials = [
            CarbonMaterialInventory(
                id=_uuid(),
                project_id=pid,
                organization_id=oid,
                material_category="structure",
                material_type="concrete",
                total_gwp=Decimal("50000"),
                epd_id=epd_id,
            ),
        ]
        epds = [
            EpdRecord(
                id=epd_id,
                project_id=pid,
                organization_id=oid,
                verification_status="verified",
            ),
        ]

        with (
            patch(
                "app.services.products.carbonlens.service.calculate_gwp",
                return_value={
                    "total_gwp_kgco2e": 50000.0,
                    "gwp_per_sf": 10.0,
                    "baseline_comparison_pct": -15.0,
                    "hotspot_materials": [
                        {
                            "id": str(_uuid()),
                            "material_type": "concrete",
                            "total_gwp": 50000.0,
                            "percentage": 100.0,
                        }
                    ],
                    "category_breakdown": [
                        {
                            "category": "structure",
                            "total_gwp": 50000.0,
                            "percentage": 100.0,
                        }
                    ],
                },
            ),
            patch(
                "app.services.products.carbonlens.service.list_materials",
                return_value=materials,
            ),
            patch(
                "app.services.products.carbonlens.service.list_epds",
                return_value=epds,
            ),
        ):
            from app.services.products.carbonlens.service import generate_mrp2_report

            report = await generate_mrp2_report(db, pid, oid)

        assert report.report_type == "mrp2_prerequisite"
        assert report.epd_coverage_pct == Decimal("100.0")
        assert report.leed_credits_achieved["MRp2_prerequisite_met"] is True
        assert report.leed_credits_achieved["MRc2_points"] == 2  # -15% reduction

    @pytest.mark.asyncio
    async def test_report_low_epd_coverage(self):
        db = AsyncMock()
        pid = _uuid()
        oid = _uuid()

        materials = [
            CarbonMaterialInventory(
                id=_uuid(),
                project_id=pid,
                organization_id=oid,
                material_category="structure",
                material_type="concrete",
                total_gwp=Decimal("50000"),
                epd_id=None,
            ),
            CarbonMaterialInventory(
                id=_uuid(),
                project_id=pid,
                organization_id=oid,
                material_category="structure",
                material_type="steel",
                total_gwp=Decimal("30000"),
                epd_id=None,
            ),
        ]

        with (
            patch(
                "app.services.products.carbonlens.service.calculate_gwp",
                return_value={
                    "total_gwp_kgco2e": 80000.0,
                    "gwp_per_sf": None,
                    "baseline_comparison_pct": 5.0,
                    "hotspot_materials": [],
                    "category_breakdown": [],
                },
            ),
            patch(
                "app.services.products.carbonlens.service.list_materials",
                return_value=materials,
            ),
            patch(
                "app.services.products.carbonlens.service.list_epds",
                return_value=[],
            ),
        ):
            from app.services.products.carbonlens.service import generate_mrp2_report

            report = await generate_mrp2_report(db, pid, oid)

        assert report.leed_credits_achieved["MRp2_epd_coverage"] is False
        assert report.leed_credits_achieved["MRp2_prerequisite_met"] is False

    @pytest.mark.asyncio
    async def test_report_mitigation_narrative_included(self):
        db = AsyncMock()
        pid = _uuid()
        oid = _uuid()

        with (
            patch(
                "app.services.products.carbonlens.service.calculate_gwp",
                return_value={
                    "total_gwp_kgco2e": 100000.0,
                    "gwp_per_sf": 20.0,
                    "baseline_comparison_pct": 10.0,
                    "hotspot_materials": [
                        {
                            "id": str(_uuid()),
                            "material_type": "concrete",
                            "total_gwp": 80000.0,
                            "percentage": 80.0,
                        }
                    ],
                    "category_breakdown": [],
                },
            ),
            patch(
                "app.services.products.carbonlens.service.list_materials",
                return_value=[
                    CarbonMaterialInventory(
                        id=_uuid(),
                        project_id=pid,
                        organization_id=oid,
                        material_category="structure",
                        material_type="concrete",
                        total_gwp=Decimal("100000"),
                        epd_id=None,
                    )
                ],
            ),
            patch(
                "app.services.products.carbonlens.service.list_epds",
                return_value=[],
            ),
        ):
            from app.services.products.carbonlens.service import generate_mrp2_report

            report = await generate_mrp2_report(db, pid, oid)

        assert report.mitigation_narrative is not None
        assert "100,000 kgCO2e" in report.mitigation_narrative
        assert "concrete" in report.mitigation_narrative

    @pytest.mark.asyncio
    async def test_report_mrc2_credit_levels(self):
        """Test MRc2 credit point thresholds."""
        db = AsyncMock()
        pid = _uuid()
        oid = _uuid()

        # -5% reduction -> 1 point
        with (
            patch(
                "app.services.products.carbonlens.service.calculate_gwp",
                return_value={
                    "total_gwp_kgco2e": 95000.0,
                    "gwp_per_sf": None,
                    "baseline_comparison_pct": -5.0,
                    "hotspot_materials": [],
                    "category_breakdown": [],
                },
            ),
            patch(
                "app.services.products.carbonlens.service.list_materials",
                return_value=[],
            ),
            patch(
                "app.services.products.carbonlens.service.list_epds",
                return_value=[],
            ),
        ):
            from app.services.products.carbonlens.service import generate_mrp2_report

            report = await generate_mrp2_report(db, pid, oid)

        assert report.leed_credits_achieved["MRc2_points"] == 1


# ---------------------------------------------------------------------------
# 11. Dashboard
# ---------------------------------------------------------------------------


class TestCarbonDashboard:
    """Tests for dashboard aggregation."""

    @pytest.mark.asyncio
    async def test_full_dashboard(self):
        db = AsyncMock()
        pid = _uuid()
        oid = _uuid()
        epd_id = _uuid()

        materials = [
            CarbonMaterialInventory(
                id=_uuid(),
                project_id=pid,
                organization_id=oid,
                material_category="structure",
                material_type="concrete",
                total_gwp=Decimal("50000"),
                epd_id=epd_id,
            ),
            CarbonMaterialInventory(
                id=_uuid(),
                project_id=pid,
                organization_id=oid,
                material_category="enclosure",
                material_type="curtain wall",
                total_gwp=Decimal("10000"),
                epd_id=None,
            ),
        ]

        with (
            patch(
                "app.services.products.carbonlens.service.calculate_gwp",
                return_value={
                    "total_gwp_kgco2e": 60000.0,
                    "gwp_per_sf": 12.0,
                    "baseline_comparison_pct": -8.0,
                    "hotspot_materials": [
                        {
                            "id": str(_uuid()),
                            "material_type": "concrete",
                            "total_gwp": 50000.0,
                            "percentage": 83.3,
                        }
                    ],
                    "category_breakdown": [
                        {
                            "category": "structure",
                            "total_gwp": 50000.0,
                            "percentage": 83.3,
                        },
                        {
                            "category": "enclosure",
                            "total_gwp": 10000.0,
                            "percentage": 16.7,
                        },
                    ],
                },
            ),
            patch(
                "app.services.products.carbonlens.service.list_materials",
                return_value=materials,
            ),
        ):
            from app.services.products.carbonlens.service import get_dashboard

            result = await get_dashboard(db, pid)

        assert result["total_gwp_tco2e"] == 60.0  # 60000 / 1000
        assert result["baseline_pct"] == -8.0
        assert result["epd_coverage_pct"] == 50.0  # 1 of 2 has EPD
        assert result["material_count"] == 2
        assert len(result["hotspots"]) == 1

    @pytest.mark.asyncio
    async def test_empty_dashboard(self):
        db = AsyncMock()
        pid = _uuid()

        with (
            patch(
                "app.services.products.carbonlens.service.calculate_gwp",
                return_value={
                    "total_gwp_kgco2e": 0,
                    "gwp_per_sf": None,
                    "baseline_comparison_pct": 0.0,
                    "hotspot_materials": [],
                    "category_breakdown": [],
                },
            ),
            patch(
                "app.services.products.carbonlens.service.list_materials",
                return_value=[],
            ),
        ):
            from app.services.products.carbonlens.service import get_dashboard

            result = await get_dashboard(db, pid)

        assert result["total_gwp_tco2e"] == 0.0
        assert result["material_count"] == 0
        assert result["epd_coverage_pct"] == 0

    @pytest.mark.asyncio
    async def test_dashboard_conversion_tco2e(self):
        """Verify kgCO2e to tCO2e conversion."""
        db = AsyncMock()
        pid = _uuid()

        with (
            patch(
                "app.services.products.carbonlens.service.calculate_gwp",
                return_value={
                    "total_gwp_kgco2e": 1500000.0,
                    "gwp_per_sf": None,
                    "baseline_comparison_pct": -12.0,
                    "hotspot_materials": [],
                    "category_breakdown": [],
                },
            ),
            patch(
                "app.services.products.carbonlens.service.list_materials",
                return_value=[],
            ),
        ):
            from app.services.products.carbonlens.service import get_dashboard

            result = await get_dashboard(db, pid)

        assert result["total_gwp_tco2e"] == 1500.0


# ---------------------------------------------------------------------------
# Improvement percentage helper
# ---------------------------------------------------------------------------


class TestImprovementPct:
    """Tests for the improvement percentage calculation helper."""

    def test_zero_improvement(self):
        assert _calc_improvement_pct(350.0, 350.0) == 0.0

    def test_positive_improvement(self):
        # Using 220 vs baseline 350 = 37.14% improvement
        result = _calc_improvement_pct(220.0, 350.0)
        assert result > 0
        assert abs(result - 37.14) < 0.1

    def test_negative_improvement(self):
        # Using 400 vs baseline 350 = -14.29% (worse)
        result = _calc_improvement_pct(400.0, 350.0)
        assert result < 0

    def test_zero_baseline(self):
        assert _calc_improvement_pct(100.0, 0.0) == 0.0


# ---------------------------------------------------------------------------
# Constants integrity
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for constant data integrity."""

    def test_material_baselines_have_20_entries(self):
        assert len(MATERIAL_BASELINES) == 20

    def test_all_baselines_have_gwp_and_unit(self):
        for name, data in MATERIAL_BASELINES.items():
            assert "gwp" in data, f"{name} missing gwp"
            assert "unit" in data, f"{name} missing unit"

    def test_building_type_baselines_have_7_entries(self):
        assert len(BUILDING_TYPE_BASELINES) == 7

    def test_csi_category_map_covers_required_divisions(self):
        assert CSI_CATEGORY_MAP["03"] == "structure"
        assert CSI_CATEGORY_MAP["07"] == "enclosure"
        assert CSI_CATEGORY_MAP["32"] == "hardscape"
