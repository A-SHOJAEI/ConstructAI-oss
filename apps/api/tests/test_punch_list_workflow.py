"""Tests for punch list field-data-capture workflow.

Covers:
- Auto-numbering (PLI-NNN)
- Create / update
- Bulk create / bulk status update
- Stats aggregation
- CSV export (grouped by company)
- Overdue detection
- GPS / drawing reference fields
- Pydantic schema validation
- API endpoints (mocked service/DB)
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_punch_item(**overrides):
    """Build a mock PunchListItem model object with sensible defaults."""
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "item_number": "PLI-001",
        "description": "Patch drywall in corridor B",
        "location": "Floor 3, Corridor B",
        "category": "drywall",
        "priority": "medium",
        "status": "open",
        "assigned_to": uuid.uuid4(),
        "created_by": uuid.uuid4(),
        "due_date": date.today() + timedelta(days=7),
        "completed_date": None,
        "photos": [],
        "notes": None,
        "gps_lat": Decimal("40.712776"),
        "gps_lon": Decimal("-74.005974"),
        "drawing_reference": "A-301",
        "company": "Acme Drywall",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# 1. Auto-numbering
# ---------------------------------------------------------------------------


class TestAutoNumbering:
    @pytest.mark.asyncio
    async def test_first_item_gets_001(self):
        from app.services.productivity.punch_list_service import generate_item_number

        mock_db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result_mock)

        num = await generate_item_number(mock_db, uuid.uuid4())
        assert num == "PLI-001"

    @pytest.mark.asyncio
    async def test_sequential_numbering(self):
        from app.services.productivity.punch_list_service import generate_item_number

        mock_db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = ["PLI-001", "PLI-002", "PLI-003"]
        mock_db.execute = AsyncMock(return_value=result_mock)

        num = await generate_item_number(mock_db, uuid.uuid4())
        assert num == "PLI-004"

    @pytest.mark.asyncio
    async def test_gaps_handled(self):
        from app.services.productivity.punch_list_service import generate_item_number

        mock_db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = ["PLI-001", "PLI-005"]
        mock_db.execute = AsyncMock(return_value=result_mock)

        num = await generate_item_number(mock_db, uuid.uuid4())
        assert num == "PLI-006"

    @pytest.mark.asyncio
    async def test_mixed_numbering_formats(self):
        from app.services.productivity.punch_list_service import generate_item_number

        mock_db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = ["PLI-003", "LEGACY-1", "PLI-010"]
        mock_db.execute = AsyncMock(return_value=result_mock)

        num = await generate_item_number(mock_db, uuid.uuid4())
        assert num == "PLI-011"


# ---------------------------------------------------------------------------
# 2. Create / Update
# ---------------------------------------------------------------------------


class TestCreateUpdate:
    @pytest.mark.asyncio
    async def test_create_auto_numbers(self):
        from app.services.productivity.punch_list_service import create_punch_list_item

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        # Mock the generate_item_number call
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result_mock)

        data = {"description": "Fix crack in wall", "priority": "high", "company": "Acme"}
        await create_punch_list_item(mock_db, uuid.uuid4(), data)
        added = mock_db.add.call_args[0][0]
        assert added.item_number == "PLI-001"
        assert added.status == "open"
        assert added.company == "Acme"

    @pytest.mark.asyncio
    async def test_create_with_gps(self):
        from app.services.productivity.punch_list_service import create_punch_list_item

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result_mock)

        data = {
            "description": "Paint touch-up",
            "gps_lat": 40.71,
            "gps_lon": -74.00,
            "drawing_reference": "A-201",
        }
        await create_punch_list_item(mock_db, uuid.uuid4(), data)
        added = mock_db.add.call_args[0][0]
        assert added.gps_lat == 40.71
        assert added.drawing_reference == "A-201"

    @pytest.mark.asyncio
    async def test_update_auto_completes_date(self):
        from app.services.productivity.punch_list_service import update_punch_list_item

        mock_db = AsyncMock()
        item = _make_punch_item(status="open", completed_date=None)

        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = item
        mock_db.execute = AsyncMock(return_value=result_mock)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        updated = await update_punch_list_item(
            mock_db, item.id, item.project_id, {"status": "resolved"}
        )
        assert updated.completed_date == date.today()


# ---------------------------------------------------------------------------
# 3. Bulk Operations
# ---------------------------------------------------------------------------


class TestBulkOperations:
    @pytest.mark.asyncio
    async def test_bulk_create(self):
        from app.services.productivity.punch_list_service import bulk_create

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        # First call returns [], second returns ["PLI-001"]
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result_mock = MagicMock()
            if call_count == 1:
                result_mock.scalars.return_value.all.return_value = []
            else:
                result_mock.scalars.return_value.all.return_value = [
                    f"PLI-{i:03d}" for i in range(1, call_count)
                ]
            return result_mock

        mock_db.execute = AsyncMock(side_effect=side_effect)

        items_data = [
            {"description": "Fix wall A"},
            {"description": "Fix wall B"},
        ]
        results = await bulk_create(mock_db, uuid.uuid4(), items_data)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_bulk_status_update(self):
        from app.services.productivity.punch_list_service import bulk_status_update

        mock_db = AsyncMock()
        items = [
            _make_punch_item(status="open", completed_date=None),
            _make_punch_item(status="in_progress", completed_date=None),
        ]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = items
        mock_db.execute = AsyncMock(return_value=result_mock)
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        updated = await bulk_status_update(
            mock_db, items[0].project_id, [i.id for i in items], "resolved"
        )
        assert all(i.status == "resolved" for i in updated)
        assert all(i.completed_date == date.today() for i in updated)

    @pytest.mark.asyncio
    async def test_bulk_status_invalid(self):
        from app.services.productivity.punch_list_service import bulk_status_update

        mock_db = AsyncMock()
        with pytest.raises(ValueError, match="Invalid status"):
            await bulk_status_update(mock_db, uuid.uuid4(), [uuid.uuid4()], "invalid")


# ---------------------------------------------------------------------------
# 4. Stats
# ---------------------------------------------------------------------------


class TestStats:
    @pytest.mark.asyncio
    async def test_counts_by_status(self):
        from app.services.productivity.punch_list_service import get_punch_list_stats

        mock_db = AsyncMock()
        items = [
            _make_punch_item(status="open", priority="high", company="Acme"),
            _make_punch_item(status="open", priority="medium", company="Acme"),
            _make_punch_item(status="resolved", priority="low", company="Beta"),
            _make_punch_item(status="verified", priority="high", company="Beta"),
        ]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = items
        mock_db.execute = AsyncMock(return_value=result_mock)

        stats = await get_punch_list_stats(mock_db, uuid.uuid4())
        assert stats["total"] == 4
        assert stats["open"] == 2
        assert stats["resolved"] == 1
        assert stats["verified"] == 1
        assert stats["by_company"]["Acme"] == 2
        assert stats["by_company"]["Beta"] == 2

    @pytest.mark.asyncio
    async def test_overdue_count(self):
        from app.services.productivity.punch_list_service import get_punch_list_stats

        mock_db = AsyncMock()
        items = [
            _make_punch_item(
                status="open",
                due_date=date.today() - timedelta(days=5),
                priority="high",
                company="Acme",
            ),
            _make_punch_item(
                status="resolved",  # resolved = not overdue
                due_date=date.today() - timedelta(days=5),
                priority="medium",
                company="Beta",
            ),
        ]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = items
        mock_db.execute = AsyncMock(return_value=result_mock)

        stats = await get_punch_list_stats(mock_db, uuid.uuid4())
        assert stats["overdue"] == 1


# ---------------------------------------------------------------------------
# 5. CSV Export
# ---------------------------------------------------------------------------


class TestCSVExport:
    def test_generates_valid_csv_grouped_by_company(self):
        from app.services.productivity.punch_list_service import export_punch_list_csv

        items = [
            _make_punch_item(item_number="PLI-002", company="Beta", description="Fix B"),
            _make_punch_item(item_number="PLI-001", company="Acme", description="Fix A"),
        ]
        csv_bytes = export_punch_list_csv(items)
        reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
        rows = list(reader)
        assert rows[0][0] == "Item #"  # header
        # Acme should come before Beta (sorted)
        assert rows[1][6] == "Acme"
        assert rows[2][6] == "Beta"

    def test_empty_export(self):
        from app.services.productivity.punch_list_service import export_punch_list_csv

        csv_bytes = export_punch_list_csv([])
        reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
        rows = list(reader)
        assert len(rows) == 1  # header only


# ---------------------------------------------------------------------------
# 6. Schemas
# ---------------------------------------------------------------------------


class TestPunchListSchemas:
    def test_create_v2_defaults(self):
        from app.schemas.field_management import PunchListItemCreateV2

        schema = PunchListItemCreateV2(description="Fix wall")
        assert schema.priority == "medium"
        assert schema.photos == []
        assert schema.gps_lat is None

    def test_update_v2_partial(self):
        from app.schemas.field_management import PunchListItemUpdateV2

        schema = PunchListItemUpdateV2(status="resolved")
        dumped = schema.model_dump(exclude_unset=True)
        assert dumped == {"status": "resolved"}

    def test_bulk_create_request(self):
        from app.schemas.field_management import PunchListBulkCreateRequest

        req = PunchListBulkCreateRequest(
            items=[
                {"description": "Fix A"},
                {"description": "Fix B"},
            ]
        )
        assert len(req.items) == 2

    def test_bulk_status_update(self):
        from app.schemas.field_management import PunchListBulkStatusUpdate

        req = PunchListBulkStatusUpdate(item_ids=[uuid.uuid4(), uuid.uuid4()], status="verified")
        assert len(req.item_ids) == 2

    def test_stats_response(self):
        from app.schemas.field_management import PunchListStatsResponse

        stats = PunchListStatsResponse(
            total=10,
            open=4,
            in_progress=3,
            resolved=2,
            verified=1,
            by_priority={"high": 4},
            by_company={"Acme": 6},
            overdue=2,
        )
        assert stats.total == 10

    def test_detail_response(self):
        from app.schemas.field_management import PunchListDetailResponse

        item = _make_punch_item()
        resp = PunchListDetailResponse.model_validate(item, from_attributes=True)
        assert resp.item_number == "PLI-001"
        assert resp.company == "Acme Drywall"


# ---------------------------------------------------------------------------
# 7. API Endpoints (mocked)
# ---------------------------------------------------------------------------


class TestPunchListAPIEndpoints:
    @pytest.mark.asyncio
    async def test_create_endpoint(self):
        from app.api.v1.punch_lists import create_item
        from app.schemas.field_management import PunchListItemCreateV2

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.punch_lists.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.punch_lists.create_punch_list_item", new_callable=AsyncMock
            ) as mock_create:
                mock_create.return_value = _make_punch_item()
                request = PunchListItemCreateV2(description="Fix wall")
                await create_item(uuid.uuid4(), request, mock_user, mock_db)
                mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_create_endpoint(self):
        from app.api.v1.punch_lists import bulk_create_items
        from app.schemas.field_management import PunchListBulkCreateRequest

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.punch_lists.verify_project_access", new_callable=AsyncMock):
            with patch("app.api.v1.punch_lists.bulk_create", new_callable=AsyncMock) as mock_bulk:
                mock_bulk.return_value = [
                    _make_punch_item(item_number="PLI-001"),
                    _make_punch_item(item_number="PLI-002"),
                ]
                req = PunchListBulkCreateRequest(
                    items=[
                        {"description": "A"},
                        {"description": "B"},
                    ]
                )
                result = await bulk_create_items(uuid.uuid4(), req, mock_user, mock_db)
                assert result["created"] == 2

    @pytest.mark.asyncio
    async def test_bulk_status_endpoint(self):
        from app.api.v1.punch_lists import bulk_update_status
        from app.schemas.field_management import PunchListBulkStatusUpdate

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.punch_lists.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.punch_lists.bulk_status_update", new_callable=AsyncMock
            ) as mock_bulk:
                mock_bulk.return_value = [_make_punch_item(), _make_punch_item()]
                req = PunchListBulkStatusUpdate(
                    item_ids=[uuid.uuid4(), uuid.uuid4()], status="resolved"
                )
                result = await bulk_update_status(uuid.uuid4(), req, mock_user, mock_db)
                assert result["updated"] == 2

    @pytest.mark.asyncio
    async def test_stats_endpoint(self):
        from app.api.v1.punch_lists import get_stats

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.punch_lists.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.punch_lists.get_punch_list_stats", new_callable=AsyncMock
            ) as mock_stats:
                mock_stats.return_value = {
                    "total": 10,
                    "open": 4,
                    "in_progress": 3,
                    "resolved": 2,
                    "verified": 1,
                    "by_priority": {},
                    "by_company": {},
                    "overdue": 0,
                }
                result = await get_stats(uuid.uuid4(), mock_user, mock_db)
                assert result["total"] == 10

    @pytest.mark.asyncio
    async def test_get_item_not_found(self):
        from app.api.v1.punch_lists import get_item

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.punch_lists.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.punch_lists.get_punch_list_item_detail", new_callable=AsyncMock
            ) as mock_get:
                mock_get.side_effect = ValueError("not found")
                with pytest.raises(Exception):
                    await get_item(uuid.uuid4(), uuid.uuid4(), mock_user, mock_db)

    @pytest.mark.asyncio
    async def test_list_endpoint(self):
        from app.api.v1.punch_lists import list_items

        mock_db = AsyncMock()
        mock_user = SimpleNamespace(id=uuid.uuid4())

        with patch("app.api.v1.punch_lists.verify_project_access", new_callable=AsyncMock):
            with patch(
                "app.api.v1.punch_lists.list_punch_list_items", new_callable=AsyncMock
            ) as mock_list:
                mock_list.return_value = {"data": [], "meta": {"cursor": None, "has_more": False}}
                result = await list_items(
                    uuid.uuid4(),
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    20,
                    mock_user,
                    mock_db,
                )
                assert result["data"] == []
