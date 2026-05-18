"""Comprehensive tests for the ChangeFlow T&M product.

Tests cover:
- TmEntry and CorNegotiation model creation
- T&M entry CRUD operations
- T&M summary aggregation
- Pricing engine with markup cascade
- COR generation from T&M entries
- Negotiation tracking
- Dashboard analytics
- API endpoint integration
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.models.tm_entry import CorNegotiation, TmEntry

from app.schemas.changeflow import (
    ChangeFlowDashboardResponse,
    CorGenerateRequest,
    CorNegotiationResponse,
    NegotiationCreate,
    PricingSummaryResponse,
    TmEntryCreate,
    TmEntryResponse,
    TmSummaryResponse,
)
from app.services.products.changeflow.service import (
    add_tm_entry,
    calculate_pricing_summary,
    generate_cor,
    get_dashboard,
    get_tm_summary,
    list_negotiations,
    list_tm_entries,
    record_negotiation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ID = uuid.uuid4()
_ORG_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()
_EVENT_ID = uuid.uuid4()
_COR_ID = uuid.uuid4()


def _mock_db_scalars(entries: list):
    """Create a mock db with execute returning scalars().all() = entries.

    SQLAlchemy's ``result.scalars()`` is synchronous, so use MagicMock
    for the result, not AsyncMock.
    """
    db = AsyncMock()
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = entries
    mock_result.scalars.return_value = mock_scalars
    db.execute = AsyncMock(return_value=mock_result)
    return db


def _make_entry(
    entry_type: str = "labor",
    straight_hours: float | None = 8.0,
    overtime_hours: float | None = 0.0,
    labor_rate: float | None = 75.0,
    ot_rate: float | None = 112.5,
    quantity: float | None = None,
    unit_cost: float | None = None,
    equipment_hours: float | None = None,
    equipment_rate: float | None = None,
    sub_amount: float | None = None,
    **kwargs,
) -> SimpleNamespace:
    """Create a lightweight entry-like object for pricing tests."""
    return SimpleNamespace(
        entry_type=entry_type,
        straight_hours=straight_hours,
        overtime_hours=overtime_hours,
        labor_rate=labor_rate,
        ot_rate=ot_rate,
        quantity=quantity,
        unit_cost=unit_cost,
        equipment_hours=equipment_hours,
        equipment_rate=equipment_rate,
        sub_amount=sub_amount,
        **kwargs,
    )


# ===========================================================================
# Model tests (no DB required)
# ===========================================================================


class TestTmEntryModel:
    """Test TmEntry SQLAlchemy model instantiation."""

    def test_labor_entry(self):
        entry = TmEntry(
            project_id=_PROJECT_ID,
            organization_id=_ORG_ID,
            change_event_id=_EVENT_ID,
            entry_type="labor",
            worker_name="John Smith",
            classification="Electrician",
            straight_hours=Decimal("8.00"),
            overtime_hours=Decimal("2.00"),
            labor_rate=Decimal("75.00"),
            ot_rate=Decimal("112.50"),
        )
        assert entry.entry_type == "labor"
        assert entry.worker_name == "John Smith"
        assert entry.straight_hours == Decimal("8.00")

    def test_material_entry(self):
        entry = TmEntry(
            project_id=_PROJECT_ID,
            organization_id=_ORG_ID,
            entry_type="material",
            material_description="2x4 lumber",
            quantity=Decimal("500.0000"),
            unit="bf",
            unit_cost=Decimal("3.50"),
            vendor="Home Depot",
        )
        assert entry.entry_type == "material"
        assert entry.material_description == "2x4 lumber"
        assert entry.quantity == Decimal("500.0000")

    def test_equipment_entry(self):
        entry = TmEntry(
            project_id=_PROJECT_ID,
            organization_id=_ORG_ID,
            entry_type="equipment",
            equipment_type="Excavator CAT 320",
            equipment_hours=Decimal("6.50"),
            equipment_rate=Decimal("175.00"),
        )
        assert entry.entry_type == "equipment"
        assert entry.equipment_type == "Excavator CAT 320"

    def test_subcontractor_entry(self):
        entry = TmEntry(
            project_id=_PROJECT_ID,
            organization_id=_ORG_ID,
            entry_type="subcontractor",
            sub_name="ABC Electric",
            sub_scope="Panel upgrade",
            sub_amount=Decimal("4500.00"),
        )
        assert entry.entry_type == "subcontractor"
        assert entry.sub_amount == Decimal("4500.00")

    def test_entry_with_gps_and_photos(self):
        entry = TmEntry(
            project_id=_PROJECT_ID,
            organization_id=_ORG_ID,
            entry_type="labor",
            gps_lat=Decimal("40.712776"),
            gps_lng=Decimal("-74.005974"),
            photos=["s3://bucket/photo1.jpg", "s3://bucket/photo2.jpg"],
            voice_note_s3_key="s3://bucket/voice.webm",
            notes="Completed framing on 3rd floor",
        )
        assert entry.gps_lat == Decimal("40.712776")
        assert len(entry.photos) == 2
        assert entry.voice_note_s3_key is not None


class TestCorNegotiationModel:
    """Test CorNegotiation SQLAlchemy model instantiation."""

    def test_submitted_action(self):
        neg = CorNegotiation(
            cor_id=_COR_ID,
            action="submitted",
            amount=Decimal("15000.00"),
            notes="Initial submission based on T&M records",
            acted_by=_USER_ID,
        )
        assert neg.action == "submitted"
        assert neg.amount == Decimal("15000.00")

    def test_counter_offer(self):
        neg = CorNegotiation(
            cor_id=_COR_ID,
            action="counter_offer",
            amount=Decimal("12500.00"),
            notes="Owner counter-offer",
        )
        assert neg.action == "counter_offer"
        assert neg.amount == Decimal("12500.00")

    def test_approved_without_amount(self):
        neg = CorNegotiation(cor_id=_COR_ID, action="approved")
        assert neg.action == "approved"
        assert neg.amount is None
        assert neg.notes is None


# ===========================================================================
# Schema validation tests
# ===========================================================================


class TestSchemaValidation:
    """Test Pydantic schema validation rules."""

    def test_valid_tm_entry_create(self):
        schema = TmEntryCreate(
            entry_type="labor",
            worker_name="Jane Doe",
            straight_hours=8.0,
            labor_rate=65.0,
        )
        assert schema.entry_type == "labor"

    def test_invalid_entry_type_rejected(self):
        with pytest.raises(ValueError, match="entry_type must be one of"):
            TmEntryCreate(entry_type="unknown")

    def test_valid_negotiation_create(self):
        schema = NegotiationCreate(action="submitted", amount=10000.0)
        assert schema.action == "submitted"

    def test_invalid_negotiation_action_rejected(self):
        with pytest.raises(ValueError, match="action must be one of"):
            NegotiationCreate(action="invalid_action")

    def test_cor_generate_request(self):
        req = CorGenerateRequest(
            change_event_id=_EVENT_ID,
            subject="Extra electrical work",
        )
        assert req.change_event_id == _EVENT_ID
        assert req.subject == "Extra electrical work"


# ===========================================================================
# Pricing engine tests (pure functions, no DB)
# ===========================================================================


class TestPricingCalculation:
    """Test the markup cascade: burden, tax, overhead, profit, bond."""

    def test_labor_only(self):
        entries = [_make_entry("labor", straight_hours=8, labor_rate=100, overtime_hours=0)]
        result = calculate_pricing_summary(entries)
        assert result["labor_subtotal"] == 800.0
        assert result["labor_burden"] == 320.0  # 40% of 800
        assert result["labor_total"] == 1120.0
        assert result["material_subtotal"] == 0.0

    def test_material_with_tax(self):
        entries = [_make_entry("material", quantity=100, unit_cost=10.0)]
        result = calculate_pricing_summary(entries, material_tax_rate=0.08)
        assert result["material_subtotal"] == 1000.0
        assert result["material_tax"] == 80.0
        assert result["material_total"] == 1080.0

    def test_equipment_total(self):
        entries = [_make_entry("equipment", equipment_hours=10, equipment_rate=200)]
        result = calculate_pricing_summary(entries)
        assert result["equipment_total"] == 2000.0

    def test_subcontractor_total(self):
        entries = [_make_entry("subcontractor", sub_amount=5000)]
        result = calculate_pricing_summary(entries)
        assert result["sub_total"] == 5000.0

    def test_full_markup_cascade(self):
        """Verify the cascade: overhead on direct, profit on (direct+OH), bond on all."""
        entries = [
            _make_entry("labor", straight_hours=10, labor_rate=100, overtime_hours=0),
            _make_entry("material", quantity=50, unit_cost=20),
        ]
        result = calculate_pricing_summary(
            entries,
            overhead_pct=0.10,
            profit_pct=0.10,
            bond_pct=0.01,
            labor_burden_pct=0.40,
            material_tax_rate=0.0,
        )
        # Labor: 1000 + 400 burden = 1400
        assert result["labor_subtotal"] == 1000.0
        assert result["labor_burden"] == 400.0
        assert result["labor_total"] == 1400.0
        # Material: 1000, no tax
        assert result["material_subtotal"] == 1000.0
        assert result["material_total"] == 1000.0
        # Direct: 1400 + 1000 = 2400
        assert result["direct_cost_subtotal"] == 2400.0
        # Overhead: 2400 * 0.10 = 240
        assert result["overhead_amount"] == 240.0
        # Profit: (2400 + 240) * 0.10 = 264
        assert result["profit_amount"] == 264.0
        # Bond: (2400 + 240 + 264) * 0.01 = 29.04
        assert result["bond_amount"] == 29.04
        # Grand: 2400 + 240 + 264 + 29.04 = 2933.04
        assert result["grand_total"] == 2933.04

    def test_empty_entries(self):
        result = calculate_pricing_summary([])
        assert result["grand_total"] == 0.0
        assert result["labor_subtotal"] == 0.0


# ===========================================================================
# Service tests (with mocked DB)
# ===========================================================================


class TestAddTmEntry:
    """Test add_tm_entry service function."""

    @pytest.mark.asyncio
    async def test_add_labor_entry(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        data = {
            "entry_type": "labor",
            "worker_name": "Mike",
            "classification": "Carpenter",
            "straight_hours": 8.0,
            "labor_rate": 55.0,
            "overtime_hours": 2.0,
            "ot_rate": 82.5,
        }
        entry = await add_tm_entry(db, _PROJECT_ID, _ORG_ID, _EVENT_ID, data, _USER_ID)
        assert entry.entry_type == "labor"
        assert entry.worker_name == "Mike"
        db.add.assert_called_once()
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_material_entry(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        data = {
            "entry_type": "material",
            "material_description": "Rebar #5",
            "quantity": 200.0,
            "unit": "lf",
            "unit_cost": 1.25,
            "vendor": "Steel Supply Co",
        }
        entry = await add_tm_entry(db, _PROJECT_ID, _ORG_ID, _EVENT_ID, data)
        assert entry.entry_type == "material"
        assert entry.material_description == "Rebar #5"

    @pytest.mark.asyncio
    async def test_add_equipment_entry(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        data = {
            "entry_type": "equipment",
            "equipment_type": "Crane 40T",
            "equipment_hours": 4.0,
            "equipment_rate": 350.0,
        }
        entry = await add_tm_entry(db, _PROJECT_ID, _ORG_ID, _EVENT_ID, data)
        assert entry.entry_type == "equipment"
        assert entry.equipment_type == "Crane 40T"

    @pytest.mark.asyncio
    async def test_add_subcontractor_entry(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        data = {
            "entry_type": "subcontractor",
            "sub_name": "XYZ Plumbing",
            "sub_scope": "Rough-in 2nd floor restrooms",
            "sub_amount": 8750.0,
        }
        entry = await add_tm_entry(db, _PROJECT_ID, _ORG_ID, _EVENT_ID, data)
        assert entry.entry_type == "subcontractor"
        assert entry.sub_amount == 8750.0

    @pytest.mark.asyncio
    async def test_add_entry_without_change_event(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        data = {"entry_type": "labor", "straight_hours": 4.0, "labor_rate": 50.0}
        entry = await add_tm_entry(db, _PROJECT_ID, _ORG_ID, None, data)
        assert entry.change_event_id is None


class TestListTmEntries:
    """Test list_tm_entries service function."""

    @pytest.mark.asyncio
    async def test_returns_entries_for_event(self):
        mock_entries = [
            TmEntry(entry_type="labor", change_event_id=_EVENT_ID),
            TmEntry(entry_type="material", change_event_id=_EVENT_ID),
        ]
        db = _mock_db_scalars(mock_entries)

        entries = await list_tm_entries(db, _EVENT_ID)
        assert len(entries) == 2
        db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_empty_list(self):
        db = _mock_db_scalars([])

        entries = await list_tm_entries(db, uuid.uuid4())
        assert entries == []

    @pytest.mark.asyncio
    async def test_filters_by_change_event_id(self):
        """Verify that only entries for the given event are returned."""
        other_event = uuid.uuid4()
        db = _mock_db_scalars([TmEntry(entry_type="labor", change_event_id=other_event)])

        entries = await list_tm_entries(db, other_event)
        assert len(entries) == 1


class TestTmSummary:
    """Test get_tm_summary aggregation."""

    @pytest.mark.asyncio
    async def test_mixed_entry_summary(self):
        entries = [
            TmEntry(
                entry_type="labor",
                change_event_id=_EVENT_ID,
                straight_hours=Decimal("8"),
                labor_rate=Decimal("100"),
                overtime_hours=Decimal("2"),
                ot_rate=Decimal("150"),
            ),
            TmEntry(
                entry_type="material",
                change_event_id=_EVENT_ID,
                quantity=Decimal("50"),
                unit_cost=Decimal("20"),
            ),
            TmEntry(
                entry_type="equipment",
                change_event_id=_EVENT_ID,
                equipment_hours=Decimal("4"),
                equipment_rate=Decimal("200"),
            ),
            TmEntry(
                entry_type="subcontractor",
                change_event_id=_EVENT_ID,
                sub_amount=Decimal("3000"),
            ),
        ]

        db = _mock_db_scalars(entries)

        summary = await get_tm_summary(db, _EVENT_ID)
        assert summary["labor_subtotal"] == 1100.0  # 8*100 + 2*150
        assert summary["material_subtotal"] == 1000.0
        assert summary["equipment_subtotal"] == 800.0
        assert summary["sub_subtotal"] == 3000.0
        assert summary["entry_count"] == 4

    @pytest.mark.asyncio
    async def test_labor_only_summary(self):
        entries = [
            TmEntry(
                entry_type="labor",
                change_event_id=_EVENT_ID,
                straight_hours=Decimal("10"),
                labor_rate=Decimal("75"),
                overtime_hours=Decimal("0"),
                ot_rate=Decimal("0"),
            ),
        ]

        db = _mock_db_scalars(entries)

        summary = await get_tm_summary(db, _EVENT_ID)
        assert summary["labor_subtotal"] == 750.0
        assert summary["material_subtotal"] == 0.0
        assert summary["entry_count"] == 1

    @pytest.mark.asyncio
    async def test_empty_summary(self):
        db = _mock_db_scalars([])

        summary = await get_tm_summary(db, _EVENT_ID)
        assert summary["entry_count"] == 0
        assert summary["labor_subtotal"] == 0.0

    @pytest.mark.asyncio
    async def test_null_fields_treated_as_zero(self):
        entries = [
            TmEntry(
                entry_type="labor",
                change_event_id=_EVENT_ID,
                straight_hours=None,
                labor_rate=None,
                overtime_hours=None,
                ot_rate=None,
            ),
        ]

        db = _mock_db_scalars(entries)

        summary = await get_tm_summary(db, _EVENT_ID)
        assert summary["labor_subtotal"] == 0.0

    @pytest.mark.asyncio
    async def test_multiple_labor_entries(self):
        entries = [
            TmEntry(
                entry_type="labor",
                change_event_id=_EVENT_ID,
                straight_hours=Decimal("8"),
                labor_rate=Decimal("50"),
                overtime_hours=Decimal("0"),
                ot_rate=Decimal("0"),
            ),
            TmEntry(
                entry_type="labor",
                change_event_id=_EVENT_ID,
                straight_hours=Decimal("8"),
                labor_rate=Decimal("60"),
                overtime_hours=Decimal("1"),
                ot_rate=Decimal("90"),
            ),
        ]

        db = _mock_db_scalars(entries)

        summary = await get_tm_summary(db, _EVENT_ID)
        # 8*50 + (8*60 + 1*90) = 400 + 570 = 970
        assert summary["labor_subtotal"] == 970.0


class TestGenerateCor:
    """Test COR generation from T&M entries."""

    @pytest.mark.asyncio
    async def test_generate_cor_basic(self):
        entries = [
            TmEntry(
                entry_type="labor",
                change_event_id=_EVENT_ID,
                entry_date=date(2026, 3, 15),
                straight_hours=Decimal("8"),
                labor_rate=Decimal("75"),
                overtime_hours=Decimal("0"),
                ot_rate=Decimal("0"),
                worker_name="Bob",
            ),
        ]

        db = _mock_db_scalars(entries)

        cor_data = await generate_cor(db, _PROJECT_ID, _ORG_ID, _EVENT_ID)
        assert cor_data["change_event_id"] == str(_EVENT_ID)
        assert cor_data["entry_count"] == 1
        assert "pricing" in cor_data
        assert cor_data["pricing"]["labor_subtotal"] == 600.0

    @pytest.mark.asyncio
    async def test_generate_cor_with_subject(self):
        entries = [
            TmEntry(
                entry_type="material",
                change_event_id=_EVENT_ID,
                entry_date=date(2026, 3, 15),
                material_description="Concrete",
                quantity=Decimal("10"),
                unit_cost=Decimal("150"),
            ),
        ]

        db = _mock_db_scalars(entries)

        cor_data = await generate_cor(
            db, _PROJECT_ID, _ORG_ID, _EVENT_ID, subject="Foundation repair"
        )
        assert cor_data["subject"] == "Foundation repair"

    @pytest.mark.asyncio
    async def test_generate_cor_no_entries_raises(self):
        db = _mock_db_scalars([])

        with pytest.raises(ValueError, match="No T&M entries found"):
            await generate_cor(db, _PROJECT_ID, _ORG_ID, _EVENT_ID)

    @pytest.mark.asyncio
    async def test_generate_cor_includes_pricing(self):
        entries = [
            TmEntry(
                entry_type="labor",
                change_event_id=_EVENT_ID,
                entry_date=date(2026, 3, 15),
                straight_hours=Decimal("8"),
                labor_rate=Decimal("100"),
                overtime_hours=Decimal("0"),
                ot_rate=Decimal("0"),
            ),
            TmEntry(
                entry_type="equipment",
                change_event_id=_EVENT_ID,
                entry_date=date(2026, 3, 15),
                equipment_hours=Decimal("4"),
                equipment_rate=Decimal("250"),
            ),
        ]

        db = _mock_db_scalars(entries)

        cor_data = await generate_cor(db, _PROJECT_ID, _ORG_ID, _EVENT_ID)
        pricing = cor_data["pricing"]
        assert pricing["labor_subtotal"] == 800.0
        assert pricing["equipment_total"] == 1000.0
        assert pricing["grand_total"] > 0


class TestNegotiationTracking:
    """Test negotiation recording and listing."""

    @pytest.mark.asyncio
    async def test_record_submitted(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        neg = await record_negotiation(
            db, _COR_ID, "submitted", amount=15000.0, notes="Initial T&M submission"
        )
        assert neg.action == "submitted"
        assert neg.amount == Decimal("15000.0")
        db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_counter_offer(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        neg = await record_negotiation(
            db, _COR_ID, "counter_offer", amount=12000.0, user_id=_USER_ID
        )
        assert neg.action == "counter_offer"
        assert neg.acted_by == _USER_ID

    @pytest.mark.asyncio
    async def test_list_negotiations(self):
        negotiations = [
            CorNegotiation(cor_id=_COR_ID, action="submitted", amount=Decimal("15000")),
            CorNegotiation(cor_id=_COR_ID, action="counter_offer", amount=Decimal("12000")),
        ]
        db = _mock_db_scalars(negotiations)

        result = await list_negotiations(db, _COR_ID)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_record_approval_without_amount(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        neg = await record_negotiation(db, _COR_ID, "approved")
        assert neg.action == "approved"
        assert neg.amount is None


class TestChangeFlowDashboard:
    """Test dashboard aggregation."""

    @pytest.mark.asyncio
    async def test_dashboard_empty_project(self):
        db = AsyncMock()

        # Build sequential scalar_one returns for the multiple queries
        # pending_value, total_events, total_cors, approved, rejected, avg
        call_count = 0
        results = [0, 0, 0, 0, 0, None]

        async def mock_execute(query):
            nonlocal call_count
            idx = call_count
            call_count += 1
            mock_result = MagicMock()
            mock_result.scalar_one.return_value = results[idx] if idx < len(results) else 0
            return mock_result

        db.execute = mock_execute

        dashboard = await get_dashboard(db, _PROJECT_ID)
        assert dashboard["pending_value"] == 0.0
        assert dashboard["total_events"] == 0
        assert dashboard["total_cors"] == 0

    @pytest.mark.asyncio
    async def test_dashboard_with_data(self):
        db = AsyncMock()

        call_count = 0
        results = [5000.0, 3, 2, 12000.0, 3000.0, None]

        async def mock_execute(query):
            nonlocal call_count
            idx = call_count
            call_count += 1
            mock_result = MagicMock()
            mock_result.scalar_one.return_value = results[idx] if idx < len(results) else 0
            return mock_result

        db.execute = mock_execute

        dashboard = await get_dashboard(db, _PROJECT_ID)
        assert dashboard["pending_value"] == 5000.0
        assert dashboard["total_events"] == 3
        assert dashboard["total_cors"] == 2
        assert dashboard["approved_to_date"] == 12000.0
        assert dashboard["rejected_value"] == 3000.0

    @pytest.mark.asyncio
    async def test_dashboard_avg_processing_days(self):
        db = AsyncMock()

        call_count = 0
        # avg_seconds = 172800 (2 days)
        results = [0, 0, 0, 0, 0, 172800.0]

        async def mock_execute(query):
            nonlocal call_count
            idx = call_count
            call_count += 1
            mock_result = MagicMock()
            mock_result.scalar_one.return_value = results[idx] if idx < len(results) else 0
            return mock_result

        db.execute = mock_execute

        dashboard = await get_dashboard(db, _PROJECT_ID)
        assert dashboard["avg_processing_days"] == 2.0


# ===========================================================================
# Response schema tests
# ===========================================================================


class TestResponseSchemas:
    """Test that response schemas serialize correctly."""

    def test_tm_entry_response(self):
        resp = TmEntryResponse(
            id=uuid.uuid4(),
            project_id=_PROJECT_ID,
            organization_id=_ORG_ID,
            entry_date=date(2026, 3, 15),
            entry_type="labor",
            straight_hours=8.0,
            labor_rate=75.0,
            created_at=datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC),
        )
        assert resp.entry_type == "labor"

    def test_tm_summary_response(self):
        resp = TmSummaryResponse(
            labor_subtotal=1000.0,
            material_subtotal=500.0,
            equipment_subtotal=200.0,
            sub_subtotal=3000.0,
            entry_count=5,
            entries=[],
        )
        assert resp.entry_count == 5

    def test_pricing_summary_response(self):
        resp = PricingSummaryResponse(
            labor_subtotal=1000.0,
            labor_burden=400.0,
            labor_total=1400.0,
            material_subtotal=500.0,
            material_tax=0.0,
            material_total=500.0,
            equipment_total=200.0,
            sub_total=3000.0,
            direct_cost_subtotal=5100.0,
            overhead_amount=510.0,
            profit_amount=561.0,
            bond_amount=61.71,
            grand_total=6232.71,
        )
        assert resp.grand_total == 6232.71

    def test_cor_negotiation_response(self):
        resp = CorNegotiationResponse(
            id=uuid.uuid4(),
            cor_id=_COR_ID,
            action="submitted",
            amount=15000.0,
            acted_at=datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC),
        )
        assert resp.action == "submitted"

    def test_dashboard_response(self):
        resp = ChangeFlowDashboardResponse(
            pending_value=5000.0,
            approved_to_date=12000.0,
            rejected_value=3000.0,
            total_events=3,
            total_cors=2,
            avg_processing_days=1.5,
        )
        assert resp.total_events == 3
        assert resp.avg_processing_days == 1.5


# ===========================================================================
# API endpoint tests (httpx AsyncClient with mocked service layer)
# ===========================================================================


class TestChangeFlowAPI:
    """Test HTTP endpoints for the ChangeFlow routes."""

    @pytest.mark.asyncio
    async def test_create_tm_entry_endpoint(self, client, test_user, auth_headers, db_session):
        """Test POST /{project_id}/changeflow/tm-entries."""
        from app.models.project import Project

        project = Project(
            name="Test Project",
            org_id=test_user.org_id,
        )
        db_session.add(project)
        await db_session.flush()
        await db_session.refresh(project)

        response = await client.post(
            f"/api/v1/projects/{project.id}/changeflow/tm-entries",
            json={
                "entry_type": "labor",
                "worker_name": "Test Worker",
                "straight_hours": 8.0,
                "labor_rate": 75.0,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["entry_type"] == "labor"
        assert data["worker_name"] == "Test Worker"

    @pytest.mark.asyncio
    async def test_list_tm_entries_endpoint(self, client, test_user, auth_headers, db_session):
        """Test GET /{project_id}/changeflow/events/{event_id}/tm-entries."""
        from app.models.project import Project

        project = Project(name="Test Project", org_id=test_user.org_id)
        db_session.add(project)
        await db_session.flush()
        await db_session.refresh(project)

        event_id = uuid.uuid4()
        response = await client.get(
            f"/api/v1/projects/{project.id}/changeflow/events/{event_id}/tm-entries",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_negotiate_cor_endpoint(self, client, test_user, auth_headers, db_session):
        """Test POST /{project_id}/changeflow/cors/{cor_id}/negotiate."""
        from app.models.project import Project

        project = Project(name="Test Project", org_id=test_user.org_id)
        db_session.add(project)
        await db_session.flush()
        await db_session.refresh(project)

        cor_id = uuid.uuid4()
        response = await client.post(
            f"/api/v1/projects/{project.id}/changeflow/cors/{cor_id}/negotiate",
            json={"action": "submitted", "amount": 15000.0, "notes": "Initial submission"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["action"] == "submitted"

    @pytest.mark.asyncio
    async def test_dashboard_endpoint(self, client, test_user, auth_headers, db_session):
        """Test GET /{project_id}/changeflow/dashboard."""
        from app.models.project import Project

        project = Project(name="Test Project", org_id=test_user.org_id)
        db_session.add(project)
        await db_session.flush()
        await db_session.refresh(project)

        response = await client.get(
            f"/api/v1/projects/{project.id}/changeflow/dashboard",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "pending_value" in data
        assert "total_events" in data
        assert "total_cors" in data
