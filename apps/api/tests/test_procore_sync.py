"""Tests for the Procore data sync layer.

Tests use realistic mock Procore API responses to verify:
  1. Mapping functions produce correct ConstructAI model fields
  2. Sync functions handle creates and updates (upserts)
  3. Error handling for individual entity failures
  4. SyncLog progress tracking
  5. Document download and MinIO upload flow
  6. ML training exclusion (data_source='procore')
  7. Edge cases: empty projects, 500+ RFIs, download failure, partial sync
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.integrations.procore_api import (
    ProcoreBudgetLineItem,
    ProcoreChangeOrder,
    ProcoreDailyLog,
    ProcoreDocument,
    ProcoreProject,
    ProcoreRFI,
)
from app.services.integrations.procore_mapper import (
    _build_address,
    _parse_date,
    map_procore_budget_to_evm,
    map_procore_change_order,
    map_procore_daily_log,
    map_procore_document,
    map_procore_project,
    map_procore_rfi,
    to_procore_project,
)

# ---------------------------------------------------------------------------
# Realistic mock Procore API responses
# ---------------------------------------------------------------------------

MOCK_PROCORE_PROJECTS = [
    ProcoreProject(
        id=12345,
        name="Downtown Office Tower",
        project_number="PRJ-001",
        status="Active",
        address="123 Main St",
        city="Austin",
        state_code="TX",
        start_date="2025-03-01",
        completion_date="2026-12-31",
    ),
    ProcoreProject(
        id=12346,
        name="Suburban Mall Renovation",
        project_number="PRJ-002",
        status="Inactive",
        address="456 Oak Ave",
        city="Dallas",
        state_code="TX",
        start_date="2024-06-15",
        completion_date="2025-09-30",
    ),
    ProcoreProject(
        id=12347,
        name="Highway Bridge Replacement",
        project_number=None,
        status="Pending",
        address=None,
        city=None,
        state_code=None,
        start_date=None,
        completion_date=None,
    ),
]

MOCK_PROCORE_RFIS = [
    ProcoreRFI(
        id=1001,
        number=1,
        subject="Foundation footing depth clarification",
        status="Open",
        priority="High",
        assignee={"name": "John Smith"},
        due_date="2025-06-15",
        created_at="2025-05-01T10:00:00Z",
    ),
    ProcoreRFI(
        id=1002,
        number=2,
        subject="Structural steel connection detail",
        status="Closed",
        priority="Normal",
        assignee=None,
        due_date=None,
        created_at="2025-05-10T14:30:00Z",
    ),
    ProcoreRFI(
        id=1003,
        number=None,
        subject="HVAC ductwork routing conflict",
        status=None,
        priority=None,
        assignee=None,
        due_date=None,
        created_at=None,
    ),
]

MOCK_PROCORE_DOCUMENTS = [
    ProcoreDocument(
        id=5001,
        name="Structural Drawings Rev C",
        filename="structural_rev_c.pdf",
        description="Updated structural drawings",
        document_type="drawings",
        file_size=2_500_000,
        content_type="application/pdf",
        download_url="https://storage.procore.com/docs/5001/download",
        created_at="2025-04-01T09:00:00Z",
    ),
    ProcoreDocument(
        id=5002,
        name="Concrete Mix Design",
        filename="concrete_mix.pdf",
        description=None,
        document_type="specifications",
        file_size=500_000,
        content_type="application/pdf",
    ),
]

MOCK_PROCORE_CHANGE_ORDERS = [
    ProcoreChangeOrder(
        id=3001,
        number=1,
        title="Additional foundation piles",
        status="Approved",
        grand_total=45000.00,
        created_at="2025-05-20T10:00:00Z",
    ),
    ProcoreChangeOrder(
        id=3002,
        number=2,
        title="HVAC system upgrade",
        status="Pending",
        grand_total=125000.50,
        created_at="2025-06-01T15:00:00Z",
    ),
]

MOCK_PROCORE_BUDGET = [
    ProcoreBudgetLineItem(
        id=8001,
        cost_code="03-3000",
        description="Cast-in-Place Concrete",
        original_budget_amount=500000.00,
        approved_change_orders=45000.00,
        revised_budget=545000.00,
    ),
    ProcoreBudgetLineItem(
        id=8002,
        cost_code="05-1200",
        description="Structural Steel",
        original_budget_amount=750000.00,
        approved_change_orders=0.00,
        revised_budget=750000.00,
    ),
    ProcoreBudgetLineItem(
        id=8003,
        cost_code="23-0000",
        description="HVAC",
        original_budget_amount=300000.00,
        approved_change_orders=125000.50,
        revised_budget=425000.50,
    ),
]

MOCK_PROCORE_DAILY_LOGS = [
    ProcoreDailyLog(
        id=9001,
        log_date="2025-06-15",
        weather={"condition": "Clear", "temp_high": 95, "temp_low": 72},
        notes="Concrete pour completed for Level 3 slab.",
        created_at="2025-06-15T18:00:00Z",
    ),
    ProcoreDailyLog(
        id=9002,
        log_date="2025-06-16",
        weather=None,
        notes=None,
        created_at="2025-06-16T18:00:00Z",
    ),
]


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

ORG_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()


# ===========================================================================
# Test: Procore Mapper
# ===========================================================================


class TestProcoreMapper:
    """Tests for pure mapping functions."""

    def test_map_project_active(self):
        result = map_procore_project(MOCK_PROCORE_PROJECTS[0], ORG_ID)
        assert result["name"] == "Downtown Office Tower"
        assert result["project_number"] == "PRJ-001"
        assert result["status"] == "active"
        assert result["address"] == "123 Main St, Austin, TX"
        assert result["start_date"] == date(2025, 3, 1)
        assert result["end_date"] == date(2026, 12, 31)
        assert result["data_source"] == "procore"
        assert result["procore_id"] == 12345
        assert result["org_id"] == ORG_ID

    def test_map_project_inactive(self):
        result = map_procore_project(MOCK_PROCORE_PROJECTS[1], ORG_ID)
        assert result["status"] == "archived"
        assert result["address"] == "456 Oak Ave, Dallas, TX"

    def test_map_project_pending_with_none_fields(self):
        result = map_procore_project(MOCK_PROCORE_PROJECTS[2], ORG_ID)
        assert result["status"] == "preconstruction"
        assert result["address"] is None
        assert result["start_date"] is None
        assert result["end_date"] is None
        assert result["project_number"] is None

    def test_map_project_unknown_status_defaults_to_preconstruction(self):
        p = ProcoreProject(id=99, name="Test", status="SomeNewStatus")
        result = map_procore_project(p, ORG_ID)
        assert result["status"] == "preconstruction"

    def test_map_rfi_open_high_priority(self):
        result = map_procore_rfi(MOCK_PROCORE_RFIS[0], PROJECT_ID)
        assert result["rfi_number"] == "1"
        assert result["subject"] == "Foundation footing depth clarification"
        assert result["status"] == "open"
        assert result["priority"] == "high"
        assert result["due_date"] == date(2025, 6, 15)
        assert result["data_source"] == "procore"
        assert result["procore_id"] == 1001
        assert result["project_id"] == PROJECT_ID

    def test_map_rfi_closed(self):
        result = map_procore_rfi(MOCK_PROCORE_RFIS[1], PROJECT_ID)
        assert result["rfi_number"] == "2"
        assert result["status"] == "closed"
        assert result["priority"] == "normal"

    def test_map_rfi_with_none_number_uses_id(self):
        result = map_procore_rfi(MOCK_PROCORE_RFIS[2], PROJECT_ID)
        assert result["rfi_number"] == "1003"
        assert result["status"] == "open"
        assert result["priority"] == "normal"

    def test_map_document(self):
        result = map_procore_document(MOCK_PROCORE_DOCUMENTS[0], PROJECT_ID)
        assert result["title"] == "Structural Drawings Rev C"
        assert result["original_filename"] == "structural_rev_c.pdf"
        assert result["type"] == "drawings"
        assert result["file_size_bytes"] == 2_500_000
        assert result["data_source"] == "procore"
        assert result["procore_id"] == 5001
        assert result["processing_status"] == "pending"

    def test_map_document_no_filename_uses_name(self):
        doc = ProcoreDocument(id=99, name="Test Doc", filename=None)
        result = map_procore_document(doc, PROJECT_ID)
        assert result["original_filename"] == "Test Doc"
        assert result["type"] == "general"

    def test_map_change_order(self):
        result = map_procore_change_order(MOCK_PROCORE_CHANGE_ORDERS[0], PROJECT_ID)
        assert result["co_number"] == "1"
        assert result["title"] == "Additional foundation piles"
        assert result["status"] == "approved"
        assert result["cost_impact"] == Decimal("45000")
        assert result["change_type"] == "owner_request"
        assert result["data_source"] == "procore"
        assert result["procore_id"] == 3001

    def test_map_change_order_pending(self):
        result = map_procore_change_order(MOCK_PROCORE_CHANGE_ORDERS[1], PROJECT_ID)
        assert result["co_number"] == "2"
        assert result["cost_impact"] == Decimal("125000.5")

    def test_map_change_order_no_number_uses_id(self):
        co = ProcoreChangeOrder(id=99, number=None, title="Test CO")
        result = map_procore_change_order(co, PROJECT_ID)
        assert result["co_number"] == "99"

    def test_map_daily_log(self):
        result = map_procore_daily_log(MOCK_PROCORE_DAILY_LOGS[0], PROJECT_ID)
        assert result["log_date"] == date(2025, 6, 15)
        assert result["weather"]["condition"] == "Clear"
        assert result["notes"] == "Concrete pour completed for Level 3 slab."
        assert result["data_source"] == "procore"
        assert result["procore_id"] == 9001

    def test_map_daily_log_empty_weather(self):
        result = map_procore_daily_log(MOCK_PROCORE_DAILY_LOGS[1], PROJECT_ID)
        assert result["weather"] == {}
        assert result["notes"] is None

    def test_map_budget_to_evm(self):
        result = map_procore_budget_to_evm(MOCK_PROCORE_BUDGET)
        assert result["planned_value"] == Decimal("1550000")
        assert result["original_budget"] == Decimal("1550000")

    def test_map_budget_to_evm_empty(self):
        result = map_procore_budget_to_evm([])
        assert result["planned_value"] == Decimal("0")

    def test_map_budget_to_evm_none_amounts(self):
        items = [
            ProcoreBudgetLineItem(id=1, original_budget_amount=None),
            ProcoreBudgetLineItem(id=2, original_budget_amount=100.0),
        ]
        result = map_procore_budget_to_evm(items)
        assert result["planned_value"] == Decimal("100")

    def test_to_procore_project(self):
        project = MagicMock()
        project.name = "Test Project"
        project.project_number = "TP-001"
        project.address = "123 Main St"
        project.start_date = date(2025, 1, 1)
        project.end_date = date(2026, 6, 30)

        result = to_procore_project(project)
        assert result["name"] == "Test Project"
        assert result["project_number"] == "TP-001"
        assert result["start_date"] == "2025-01-01"
        assert result["completion_date"] == "2026-06-30"

    def test_to_procore_project_no_dates(self):
        project = MagicMock()
        project.name = "No Dates"
        project.project_number = None
        project.address = None
        project.start_date = None
        project.end_date = None

        result = to_procore_project(project)
        assert result["start_date"] is None
        assert result["completion_date"] is None

    def test_parse_date_valid(self):
        assert _parse_date("2025-06-15") == date(2025, 6, 15)
        assert _parse_date("2025-06-15T10:00:00Z") == date(2025, 6, 15)

    def test_parse_date_invalid(self):
        assert _parse_date(None) is None
        assert _parse_date("") is None
        assert _parse_date("not-a-date") is None

    def test_build_address_full(self):
        p = ProcoreProject(id=1, name="X", address="123 Main", city="Austin", state_code="TX")
        assert _build_address(p) == "123 Main, Austin, TX"

    def test_build_address_partial(self):
        p = ProcoreProject(id=1, name="X", address="123 Main", city=None, state_code="TX")
        assert _build_address(p) == "123 Main, TX"

    def test_build_address_none(self):
        p = ProcoreProject(id=1, name="X")
        assert _build_address(p) is None


# ===========================================================================
# Test: Sync Projects
# ===========================================================================


class TestSyncProjects:
    """Tests for sync_projects function."""

    @pytest.fixture
    def mock_api(self):
        api = MagicMock()
        api.list_projects_v1_1 = AsyncMock(return_value=MOCK_PROCORE_PROJECTS)
        return api

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.add = MagicMock()
        db.refresh = AsyncMock()
        return db

    async def test_creates_new_projects(self, mock_api, mock_db):
        # No existing projects
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_projects

        result = await sync_projects(mock_api, mock_db, ORG_ID, company_id=1)

        assert result["synced"] == 3
        assert result["errors"] == []
        assert mock_db.add.call_count == 3

    async def test_updates_existing_project(self, mock_api, mock_db):
        existing_project = MagicMock()
        existing_project.id = PROJECT_ID

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_project
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_projects

        result = await sync_projects(mock_api, mock_db, ORG_ID, company_id=1)

        assert result["synced"] == 3
        assert result["errors"] == []
        # Should update existing, not add new
        assert mock_db.add.call_count == 0
        assert existing_project.name == MOCK_PROCORE_PROJECTS[-1].name

    async def test_sets_data_source_procore(self, mock_api, mock_db):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_projects

        await sync_projects(mock_api, mock_db, ORG_ID, company_id=1)

        # Check that added projects have data_source='procore'
        for call in mock_db.add.call_args_list:
            project = call[0][0]
            assert project.data_source == "procore"

    async def test_handles_individual_project_error(self, mock_api, mock_db):
        # First call succeeds, second raises
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 2:
                result.scalar_one_or_none.side_effect = Exception("DB error")
            else:
                result.scalar_one_or_none.return_value = None
            return result

        mock_db.execute = AsyncMock(side_effect=side_effect)

        from app.services.integrations.procore_sync import sync_projects

        result = await sync_projects(mock_api, mock_db, ORG_ID, company_id=1)

        assert result["synced"] == 2
        assert len(result["errors"]) == 1
        assert result["errors"][0]["entity"] == "project"


# ===========================================================================
# Test: Sync RFIs
# ===========================================================================


class TestSyncRFIs:
    """Tests for sync_rfis including 500+ RFI edge case."""

    async def test_syncs_rfis(self):
        mock_api = MagicMock()
        mock_api.list_rfis = AsyncMock(return_value=MOCK_PROCORE_RFIS)

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_rfis

        result = await sync_rfis(mock_api, mock_db, PROJECT_ID, 12345, 1)

        assert result["synced"] == 3
        assert result["errors"] == []
        assert mock_db.add.call_count == 3

    async def test_500_plus_rfis(self):
        """Edge case: large number of RFIs."""
        large_rfi_list = [
            ProcoreRFI(id=i, number=i, subject=f"RFI #{i}", status="Open") for i in range(501)
        ]
        mock_api = MagicMock()
        mock_api.list_rfis = AsyncMock(return_value=large_rfi_list)

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_rfis

        result = await sync_rfis(mock_api, mock_db, PROJECT_ID, 12345, 1)

        assert result["synced"] == 501
        assert result["errors"] == []

    async def test_rfi_data_source_is_procore(self):
        mock_api = MagicMock()
        mock_api.list_rfis = AsyncMock(return_value=[MOCK_PROCORE_RFIS[0]])

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_rfis

        await sync_rfis(mock_api, mock_db, PROJECT_ID, 12345, 1)

        rfi = mock_db.add.call_args[0][0]
        assert rfi.data_source == "procore"


# ===========================================================================
# Test: Sync Documents
# ===========================================================================


class TestSyncDocuments:
    """Tests for sync_documents with download + MinIO upload flow."""

    async def test_syncs_new_documents(self):
        mock_api = MagicMock()
        mock_api.list_documents = AsyncMock(return_value=MOCK_PROCORE_DOCUMENTS)
        mock_api.download_document = AsyncMock(
            return_value=(b"fake-pdf-content", "application/pdf")
        )

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.refresh = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_kafka = MagicMock()
        mock_kafka.available = True
        mock_kafka.publish = AsyncMock(return_value="event-id")

        from app.services.integrations.procore_sync import sync_documents

        with patch("app.utils.s3.upload_file") as mock_upload:
            result = await sync_documents(
                mock_api,
                mock_db,
                PROJECT_ID,
                12345,
                1,
                mock_kafka,
            )

        assert result["synced"] == 2
        assert result["errors"] == []
        assert mock_upload.call_count == 2
        assert mock_kafka.publish.call_count == 2

    async def test_skips_existing_documents(self):
        existing_doc = MagicMock()
        existing_doc.id = uuid.uuid4()

        mock_api = MagicMock()
        mock_api.list_documents = AsyncMock(return_value=[MOCK_PROCORE_DOCUMENTS[0]])

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_doc
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_documents

        with patch("app.utils.s3.upload_file"):
            result = await sync_documents(
                mock_api,
                mock_db,
                PROJECT_ID,
                12345,
                1,
            )

        assert result["synced"] == 1
        # Should NOT have called download since doc exists
        mock_api.download_document.assert_not_called()

    async def test_download_failure_continues(self):
        """Download failure for one doc should not block others."""
        mock_api = MagicMock()
        mock_api.list_documents = AsyncMock(return_value=MOCK_PROCORE_DOCUMENTS)
        mock_api.download_document = AsyncMock(
            side_effect=[
                Exception("Network timeout"),
                (b"content", "application/pdf"),
            ]
        )

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.refresh = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_documents

        with patch("app.utils.s3.upload_file"):
            result = await sync_documents(
                mock_api,
                mock_db,
                PROJECT_ID,
                12345,
                1,
            )

        assert result["synced"] == 1
        assert len(result["errors"]) == 1
        assert "download failed" in result["errors"][0]["error"]

    async def test_kafka_event_published_for_new_docs(self):
        mock_api = MagicMock()
        mock_api.list_documents = AsyncMock(return_value=[MOCK_PROCORE_DOCUMENTS[0]])
        mock_api.download_document = AsyncMock(return_value=(b"content", "application/pdf"))

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.refresh = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_kafka = MagicMock()
        mock_kafka.available = True
        mock_kafka.publish = AsyncMock(return_value="event-id")

        from app.services.integrations.procore_sync import sync_documents

        with patch("app.utils.s3.upload_file"):
            await sync_documents(
                mock_api,
                mock_db,
                PROJECT_ID,
                12345,
                1,
                mock_kafka,
            )

        mock_kafka.publish.assert_called_once()
        call_kwargs = mock_kafka.publish.call_args
        assert call_kwargs[1]["event_type"] == "constructai.document.ingested"
        assert call_kwargs[1]["data"]["data_source"] == "procore"
        assert call_kwargs[1]["source"] == "/procore-sync"


# ===========================================================================
# Test: Sync Change Orders
# ===========================================================================


class TestSyncChangeOrders:
    """Tests for sync_change_orders."""

    async def test_syncs_change_orders(self):
        mock_api = MagicMock()
        mock_api.list_change_orders = AsyncMock(return_value=MOCK_PROCORE_CHANGE_ORDERS)

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_change_orders

        result = await sync_change_orders(mock_api, mock_db, PROJECT_ID, 12345, 1)

        assert result["synced"] == 2
        assert result["errors"] == []

    async def test_change_order_data_source(self):
        mock_api = MagicMock()
        mock_api.list_change_orders = AsyncMock(return_value=[MOCK_PROCORE_CHANGE_ORDERS[0]])

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_change_orders

        await sync_change_orders(mock_api, mock_db, PROJECT_ID, 12345, 1)

        co = mock_db.add.call_args[0][0]
        assert co.data_source == "procore"


# ===========================================================================
# Test: Sync Daily Logs
# ===========================================================================


class TestSyncDailyLogs:
    """Tests for sync_daily_logs."""

    async def test_syncs_daily_logs(self):
        mock_api = MagicMock()
        mock_api.list_daily_logs = AsyncMock(return_value=MOCK_PROCORE_DAILY_LOGS)

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_daily_logs

        result = await sync_daily_logs(mock_api, mock_db, PROJECT_ID, 12345, 1)

        assert result["synced"] == 2
        assert result["errors"] == []


# ===========================================================================
# Test: Sync Budget
# ===========================================================================


class TestSyncBudget:
    """Tests for sync_budget with EVM value calculation."""

    async def test_syncs_budget_updates_project(self):
        mock_api = MagicMock()
        mock_api.get_budget = AsyncMock(return_value=MOCK_PROCORE_BUDGET)

        mock_project = MagicMock()
        mock_project.metadata_ = {}
        mock_project.contract_value = None

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_project
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_budget

        result = await sync_budget(mock_api, mock_db, PROJECT_ID, 12345, 1)

        assert result["synced"] == 3
        assert result["errors"] == []
        # Check contract_value updated to planned_value
        assert mock_project.contract_value == Decimal("1550000")
        # Check metadata updated
        assert "procore_budget" in mock_project.metadata_
        budget_meta = mock_project.metadata_["procore_budget"]
        assert Decimal(budget_meta["planned_value"]) == Decimal("1550000")
        assert budget_meta["line_item_count"] == 3

    async def test_empty_budget(self):
        mock_api = MagicMock()
        mock_api.get_budget = AsyncMock(return_value=[])

        mock_project = MagicMock()
        mock_project.metadata_ = {}

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_project
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_budget

        result = await sync_budget(mock_api, mock_db, PROJECT_ID, 12345, 1)

        assert result["synced"] == 0
        assert mock_project.contract_value == Decimal("0")


# ===========================================================================
# Test: Sync Orchestrator
# ===========================================================================


class TestSyncOrchestrator:
    """Tests for sync_all full orchestration."""

    @pytest.fixture
    def mock_connection(self):
        conn = MagicMock()
        conn.procore_company_id = "42"
        conn.organization_id = ORG_ID
        conn.last_sync_at = None
        conn.sync_status = "connected"
        return conn

    @pytest.fixture
    def mock_project(self):
        project = MagicMock()
        project.id = PROJECT_ID
        project.org_id = ORG_ID
        project.procore_id = 12345
        project.data_source = "procore"
        project.metadata_ = {}
        project.contract_value = None
        return project

    async def test_sync_all_creates_sync_log(self, mock_connection, mock_project):
        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.refresh = AsyncMock()

        # Sequence of db.execute calls
        call_count = 0

        def make_result(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # ProcoreConnection lookup
                result.scalar_one_or_none.return_value = mock_connection
            elif call_count == 2:
                # sync_projects: list_projects_v1_1 → project lookup (no existing)
                result.scalar_one_or_none.return_value = None
            elif call_count == 3 or call_count == 4:
                result.scalar_one_or_none.return_value = None
            elif call_count == 5:
                # Get all procore-synced projects
                result.scalars.return_value.all.return_value = [mock_project]
            else:
                # All subsequent calls (entity syncs)
                result.scalar_one_or_none.return_value = None
            return result

        mock_db.execute = AsyncMock(side_effect=make_result)

        from app.services.integrations.procore_sync import sync_all

        with (
            patch("app.services.integrations.procore_sync.ProcoreAPI") as MockAPI,
            patch("app.utils.s3.upload_file"),
        ):
            api_instance = MagicMock()
            api_instance.list_projects_v1_1 = AsyncMock(return_value=MOCK_PROCORE_PROJECTS)
            api_instance.list_rfis = AsyncMock(return_value=[])
            api_instance.list_documents = AsyncMock(return_value=[])
            api_instance.list_change_orders = AsyncMock(return_value=[])
            api_instance.get_budget = AsyncMock(return_value=[])
            api_instance.list_daily_logs = AsyncMock(return_value=[])
            MockAPI.return_value = api_instance

            sync_log = await sync_all(mock_db, ORG_ID, triggered_by=uuid.uuid4())

        # SyncLog was added to DB
        assert mock_db.add.called
        assert sync_log.status in ("completed", "partial")
        assert sync_log.completed_at is not None

    async def test_sync_all_no_connection_fails(self):
        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.refresh = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        from app.services.integrations.procore_sync import sync_all

        sync_log = await sync_all(mock_db, ORG_ID)

        assert sync_log.status == "failed"
        assert sync_log.errors[0]["error"] == "No Procore connection found"

    async def test_partial_sync_on_entity_error(self, mock_connection):
        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.refresh = AsyncMock()

        call_count = 0

        def make_result(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none.return_value = mock_connection
            elif call_count <= 4:
                result.scalar_one_or_none.return_value = None
            elif call_count == 5:
                result.scalars.return_value.all.return_value = []
            else:
                result.scalar_one_or_none.return_value = None
            return result

        mock_db.execute = AsyncMock(side_effect=make_result)

        from app.services.integrations.procore_sync import sync_all

        with patch("app.services.integrations.procore_sync.ProcoreAPI") as MockAPI:
            api_instance = MagicMock()
            api_instance.list_projects_v1_1 = AsyncMock(return_value=MOCK_PROCORE_PROJECTS)
            MockAPI.return_value = api_instance

            sync_log = await sync_all(mock_db, ORG_ID)

        assert sync_log.status in ("completed", "partial")


# ===========================================================================
# Test: SyncLog Model
# ===========================================================================


class TestSyncLogModel:
    """Tests for the SyncLog model."""

    def test_sync_log_creation(self):
        from app.models.sync_log import SyncLog

        log = SyncLog(
            org_id=ORG_ID,
            sync_type="full",
            status="running",
        )
        assert log.sync_type == "full"
        assert log.status == "running"

    def test_sync_log_fields(self):
        from app.models.sync_log import SyncLog

        log = SyncLog(
            org_id=ORG_ID,
            sync_type="project",
            status="completed",
            entities_synced={"projects": 5, "rfis": 20},
            errors=[{"entity": "rfi", "procore_id": 123, "error": "bad data"}],
            project_id=PROJECT_ID,
        )
        assert log.entities_synced["projects"] == 5
        assert len(log.errors) == 1


# ===========================================================================
# Test: Data Source Exclusion (ML Training)
# ===========================================================================


class TestDataSourceExclusion:
    """Verify all synced records get data_source='procore' for ML exclusion."""

    def test_project_mapping_sets_data_source(self):
        result = map_procore_project(MOCK_PROCORE_PROJECTS[0], ORG_ID)
        assert result["data_source"] == "procore"

    def test_rfi_mapping_sets_data_source(self):
        result = map_procore_rfi(MOCK_PROCORE_RFIS[0], PROJECT_ID)
        assert result["data_source"] == "procore"

    def test_document_mapping_sets_data_source(self):
        result = map_procore_document(MOCK_PROCORE_DOCUMENTS[0], PROJECT_ID)
        assert result["data_source"] == "procore"

    def test_change_order_mapping_sets_data_source(self):
        result = map_procore_change_order(MOCK_PROCORE_CHANGE_ORDERS[0], PROJECT_ID)
        assert result["data_source"] == "procore"

    def test_daily_log_mapping_sets_data_source(self):
        result = map_procore_daily_log(MOCK_PROCORE_DAILY_LOGS[0], PROJECT_ID)
        assert result["data_source"] == "procore"

    def test_data_source_not_manual(self):
        """Ensure no mapping function accidentally defaults to 'manual'."""
        for mapper, data, ctx in [
            (map_procore_project, MOCK_PROCORE_PROJECTS[0], ORG_ID),
            (map_procore_rfi, MOCK_PROCORE_RFIS[0], PROJECT_ID),
            (map_procore_document, MOCK_PROCORE_DOCUMENTS[0], PROJECT_ID),
            (map_procore_change_order, MOCK_PROCORE_CHANGE_ORDERS[0], PROJECT_ID),
            (map_procore_daily_log, MOCK_PROCORE_DAILY_LOGS[0], PROJECT_ID),
        ]:
            result = mapper(data, ctx)
            assert result["data_source"] != "manual", (
                f"{mapper.__name__} should set data_source='procore', not 'manual'"
            )


# ===========================================================================
# Test: Pydantic Response Models (new ones)
# ===========================================================================


class TestNewPydanticModels:
    """Tests for ProcoreDocument and ProcoreDailyLog models."""

    def test_procore_document(self):
        doc = ProcoreDocument(
            id=1,
            name="Test Doc",
            filename="test.pdf",
            file_size=1024,
        )
        assert doc.id == 1
        assert doc.name == "Test Doc"
        assert doc.document_type is None

    def test_procore_document_all_fields(self):
        doc = ProcoreDocument(
            id=1,
            name="Full Doc",
            filename="full.pdf",
            description="A full document",
            document_type="drawings",
            file_size=2048,
            content_type="application/pdf",
            download_url="https://example.com/download",
            created_at="2025-01-01",
            updated_at="2025-01-02",
        )
        assert doc.document_type == "drawings"
        assert doc.content_type == "application/pdf"

    def test_procore_daily_log(self):
        dl = ProcoreDailyLog(
            id=1,
            log_date="2025-06-15",
            weather={"temp": 85},
            notes="Work completed",
        )
        assert dl.id == 1
        assert dl.weather["temp"] == 85

    def test_procore_daily_log_minimal(self):
        dl = ProcoreDailyLog(id=1)
        assert dl.log_date is None
        assert dl.weather is None
        assert dl.notes is None


# ===========================================================================
# Test: Empty Project Sync
# ===========================================================================


class TestEmptyProjectSync:
    """Edge case: syncing when Procore returns empty data."""

    async def test_empty_project_list(self):
        mock_api = MagicMock()
        mock_api.list_projects_v1_1 = AsyncMock(return_value=[])

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()

        from app.services.integrations.procore_sync import sync_projects

        result = await sync_projects(mock_api, mock_db, ORG_ID, company_id=1)

        assert result["synced"] == 0
        assert result["errors"] == []
        assert mock_db.add.call_count == 0

    async def test_empty_rfi_list(self):
        mock_api = MagicMock()
        mock_api.list_rfis = AsyncMock(return_value=[])

        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()

        from app.services.integrations.procore_sync import sync_rfis

        result = await sync_rfis(mock_api, mock_db, PROJECT_ID, 12345, 1)

        assert result["synced"] == 0
        assert result["errors"] == []

    async def test_empty_documents_list(self):
        mock_api = MagicMock()
        mock_api.list_documents = AsyncMock(return_value=[])

        mock_db = AsyncMock()

        from app.services.integrations.procore_sync import sync_documents

        result = await sync_documents(mock_api, mock_db, PROJECT_ID, 12345, 1)

        assert result["synced"] == 0
        assert result["errors"] == []
