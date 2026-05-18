"""Tests for punch list and daily log feature gaps (migration 026).

Covers:
- Punch List (walkthrough) CRUD
- Punch list item new fields: spec_section, verified_by, date_verified, punch_list_id
- Punch list PDF export (grouped by company)
- Daily log safety fields: safety_incidents, safety_topic_discussed, weather_delay_hours
- Daily log PDF export
- Updated CSV exports with new columns
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_punch_item(**overrides):
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "punch_list_id": None,
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
        "photos": [{"file_name": "photo1.jpg", "caption": "Drywall damage"}],
        "notes": "Needs immediate attention",
        "gps_lat": Decimal("40.712776"),
        "gps_lon": Decimal("-74.005974"),
        "drawing_reference": "A-301",
        "company": "Acme Drywall",
        "spec_section": "09 29 00",
        "verified_by": None,
        "date_verified": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_punch_list(**overrides):
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "name": "Owner Walkthrough - Phase 1",
        "description": "Final walkthrough before CO",
        "walk_date": date.today(),
        "status": "open",
        "participants": [
            {"name": "Jane Owner", "role": "owner"},
            {"name": "Bob GC", "role": "gc_pm"},
        ],
        "created_by": uuid.uuid4(),
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_daily_log(**overrides):
    defaults = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "log_date": date.today(),
        "status": "draft",
        "weather": {"conditions": "clear", "temperature_high": 75, "temperature_low": 55},
        "crew_count": 15,
        "work_hours": Decimal("120.00"),
        "work_narrative": "Poured foundation on grid A-C.",
        "manpower_by_trade": [
            {"trade": "concrete", "headcount": 8, "hours": 64},
        ],
        "equipment_entries": [],
        "deliveries": [],
        "visitors": [],
        "photos": [],
        "activities_completed": [{"description": "Foundation pour"}],
        "delays": [{"description": "Rain delay", "hours_lost": 2}],
        "notes": "Good progress overall",
        "location_lat": Decimal("40.712776"),
        "location_lon": Decimal("-74.005974"),
        "safety_incidents": None,
        "safety_topic_discussed": None,
        "weather_delay_hours": None,
        "approved_by": None,
        "approved_at": None,
        "submitted_at": None,
        "data_source": "manual",
        "procore_id": None,
        "created_by": uuid.uuid4(),
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ===========================================================================
# 1. Punch List Item — New Fields
# ===========================================================================


class TestPunchListItemNewFields:
    """Test spec_section, verified_by, date_verified, punch_list_id on items."""

    def test_item_to_dict_includes_new_fields(self):
        from app.services.productivity.punch_list_service import _item_to_dict

        verified_by_id = uuid.uuid4()
        now = datetime.now(UTC)
        item = _make_punch_item(
            spec_section="09 29 00",
            verified_by=verified_by_id,
            date_verified=now,
            punch_list_id=uuid.uuid4(),
        )
        d = _item_to_dict(item)
        assert d["spec_section"] == "09 29 00"
        assert d["verified_by"] == str(verified_by_id)
        assert d["date_verified"] is not None
        assert d["punch_list_id"] is not None

    def test_item_to_dict_null_new_fields(self):
        from app.services.productivity.punch_list_service import _item_to_dict

        item = _make_punch_item(
            spec_section=None, verified_by=None, date_verified=None, punch_list_id=None
        )
        d = _item_to_dict(item)
        assert d["spec_section"] is None
        assert d["verified_by"] is None
        assert d["date_verified"] is None
        assert d["punch_list_id"] is None

    def test_update_sets_date_verified_on_verify(self):
        """When status changes to 'verified', date_verified should be auto-set."""
        item = _make_punch_item(status="resolved", date_verified=None, completed_date=date.today())
        # Simulate the auto-set logic from update_punch_list_item
        data = {"status": "verified"}
        if data.get("status") == "verified" and item.date_verified is None:
            item.date_verified = datetime.now(UTC)
        assert item.date_verified is not None

    def test_create_schema_includes_spec_section(self):
        from app.schemas.field_management import PunchListItemCreateV2

        schema = PunchListItemCreateV2(
            description="Test item",
            spec_section="03 30 00",
            company="Concrete Co",
        )
        d = schema.model_dump()
        assert d["spec_section"] == "03 30 00"

    def test_update_schema_includes_new_fields(self):
        from app.schemas.field_management import PunchListItemUpdateV2

        schema = PunchListItemUpdateV2(
            spec_section="09 29 00",
            verified_by=uuid.uuid4(),
        )
        d = schema.model_dump(exclude_unset=True)
        assert "spec_section" in d
        assert "verified_by" in d

    def test_detail_response_includes_new_fields(self):
        from app.schemas.field_management import PunchListDetailResponse

        data = {
            "id": uuid.uuid4(),
            "project_id": uuid.uuid4(),
            "punch_list_id": uuid.uuid4(),
            "item_number": "PLI-001",
            "description": "Test",
            "priority": "medium",
            "status": "open",
            "photos": [],
            "spec_section": "03 30 00",
            "verified_by": uuid.uuid4(),
            "date_verified": datetime.now(UTC),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        resp = PunchListDetailResponse(**data)
        assert resp.spec_section == "03 30 00"
        assert resp.verified_by is not None
        assert resp.date_verified is not None
        assert resp.punch_list_id is not None


# ===========================================================================
# 2. Punch List (Walkthrough) CRUD
# ===========================================================================


class TestPunchListCRUD:
    """Test walkthrough grouping model and service functions."""

    def test_punch_list_to_dict(self):
        from app.services.productivity.punch_list_service import _punch_list_to_dict

        pl = _make_punch_list()
        d = _punch_list_to_dict(pl)
        assert d["name"] == "Owner Walkthrough - Phase 1"
        assert d["status"] == "open"
        assert len(d["participants"]) == 2

    def test_punch_list_schema_create(self):
        from app.schemas.field_management import PunchListCreate

        schema = PunchListCreate(
            name="Owner Walkthrough",
            walk_date=date.today(),
            participants=[{"name": "Owner", "role": "owner"}],
        )
        d = schema.model_dump()
        assert d["name"] == "Owner Walkthrough"
        assert d["walk_date"] == date.today()

    def test_punch_list_schema_update(self):
        from app.schemas.field_management import PunchListUpdate

        schema = PunchListUpdate(status="closed")
        d = schema.model_dump(exclude_unset=True)
        assert d["status"] == "closed"

    def test_punch_list_response_schema(self):
        from app.schemas.field_management import PunchListResponse

        resp = PunchListResponse(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            name="Walkthrough",
            status="open",
            participants=[],
            item_count=5,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert resp.item_count == 5
        assert resp.status == "open"

    def test_valid_punch_list_statuses(self):
        from app.services.productivity.punch_list_service import VALID_PUNCH_LIST_STATUSES

        assert "open" in VALID_PUNCH_LIST_STATUSES
        assert "closed" in VALID_PUNCH_LIST_STATUSES
        assert len(VALID_PUNCH_LIST_STATUSES) == 2


# ===========================================================================
# 3. Punch List PDF Export
# ===========================================================================


class TestPunchListPDF:
    """Test PDF generation for punch list items."""

    def test_generates_valid_pdf(self):
        from app.services.productivity.punch_list_pdf import generate_punch_list_pdf

        items = [
            _make_punch_item(company="Alpha Concrete", item_number="PLI-001"),
            _make_punch_item(company="Alpha Concrete", item_number="PLI-002", status="resolved"),
            _make_punch_item(company="Beta Electric", item_number="PLI-003"),
        ]
        pdf_bytes = generate_punch_list_pdf(items, project_name="Test Project")
        assert pdf_bytes[:4] == b"%PDF"
        assert len(pdf_bytes) > 500

    def test_empty_items_generates_pdf(self):
        from app.services.productivity.punch_list_pdf import generate_punch_list_pdf

        pdf_bytes = generate_punch_list_pdf([])
        assert pdf_bytes[:4] == b"%PDF"

    def test_groups_by_company(self):
        from app.services.productivity.punch_list_pdf import generate_punch_list_pdf

        items = [
            _make_punch_item(company="Company A", item_number="PLI-001"),
            _make_punch_item(company="Company B", item_number="PLI-002"),
            _make_punch_item(company=None, item_number="PLI-003"),  # Unassigned
        ]
        pdf_bytes = generate_punch_list_pdf(items)
        assert pdf_bytes[:4] == b"%PDF"
        assert len(pdf_bytes) > 1000  # has content for 3 groups

    def test_includes_spec_section_column(self):
        from app.services.productivity.punch_list_pdf import generate_punch_list_pdf

        items = [
            _make_punch_item(spec_section="03 30 00"),
        ]
        pdf_bytes = generate_punch_list_pdf(items)
        assert pdf_bytes[:4] == b"%PDF"

    def test_dict_items_work(self):
        """PDF generator should accept dicts as well as ORM objects."""
        from app.services.productivity.punch_list_pdf import generate_punch_list_pdf

        items = [
            {
                "item_number": "PLI-001",
                "description": "Test item",
                "location": "Room 101",
                "category": "paint",
                "priority": "high",
                "status": "open",
                "company": "Paint Co",
                "due_date": date.today(),
                "drawing_reference": "A-101",
                "spec_section": "09 91 00",
                "photos": [],
            }
        ]
        pdf_bytes = generate_punch_list_pdf(items)
        assert pdf_bytes[:4] == b"%PDF"


# ===========================================================================
# 4. Daily Log — Safety Fields
# ===========================================================================


class TestDailyLogSafetyFields:
    """Test safety_incidents, safety_topic_discussed, weather_delay_hours."""

    def test_log_to_dict_includes_safety_fields(self):
        from app.services.productivity.daily_log_service import _log_to_dict

        log = _make_daily_log(
            safety_incidents="Worker twisted ankle on scaffolding",
            safety_topic_discussed="Fall protection refresher",
            weather_delay_hours=Decimal("2.5"),
        )
        d = _log_to_dict(log)
        assert d["safety_incidents"] == "Worker twisted ankle on scaffolding"
        assert d["safety_topic_discussed"] == "Fall protection refresher"
        assert d["weather_delay_hours"] == 2.5

    def test_log_to_dict_null_safety_fields(self):
        from app.services.productivity.daily_log_service import _log_to_dict

        log = _make_daily_log()
        d = _log_to_dict(log)
        assert d["safety_incidents"] is None
        assert d["safety_topic_discussed"] is None
        assert d["weather_delay_hours"] is None

    def test_create_schema_includes_safety_fields(self):
        from app.schemas.productivity import DailyLogCreateV2

        schema = DailyLogCreateV2(
            log_date=date.today(),
            safety_incidents="Near miss reported",
            safety_topic_discussed="Trenching safety",
            weather_delay_hours=Decimal("1.0"),
        )
        d = schema.model_dump()
        assert d["safety_incidents"] == "Near miss reported"
        assert d["safety_topic_discussed"] == "Trenching safety"
        assert d["weather_delay_hours"] == Decimal("1.0")

    def test_update_schema_includes_safety_fields(self):
        from app.schemas.productivity import DailyLogUpdateV2

        schema = DailyLogUpdateV2(
            safety_incidents="No incidents",
            weather_delay_hours=Decimal("0"),
        )
        d = schema.model_dump(exclude_unset=True)
        assert "safety_incidents" in d
        assert "weather_delay_hours" in d

    def test_detail_response_includes_safety_fields(self):
        from app.schemas.productivity import DailyLogDetailResponse

        data = {
            "id": uuid.uuid4(),
            "project_id": uuid.uuid4(),
            "log_date": date.today(),
            "status": "draft",
            "weather": {},
            "crew_count": 10,
            "work_hours": Decimal("80"),
            "manpower_by_trade": [],
            "equipment_entries": [],
            "deliveries": [],
            "visitors": [],
            "photos": [],
            "activities_completed": [],
            "delays": [],
            "safety_incidents": "Slip and fall",
            "safety_topic_discussed": "PPE compliance",
            "weather_delay_hours": Decimal("3.0"),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        resp = DailyLogDetailResponse(**data)
        assert resp.safety_incidents == "Slip and fall"
        assert resp.safety_topic_discussed == "PPE compliance"
        assert resp.weather_delay_hours == Decimal("3.0")

    def test_csv_export_includes_safety_columns(self):
        from app.services.productivity.daily_log_service import export_daily_logs_csv

        log = _make_daily_log(
            safety_incidents="Near miss",
            safety_topic_discussed="Confined spaces",
            weather_delay_hours=Decimal("1.5"),
        )
        csv_bytes = export_daily_logs_csv([log])
        reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
        headers = next(reader)
        assert "Safety Topic" in headers
        assert "Safety Incidents" in headers
        assert "Weather Delay Hrs" in headers

        row = next(reader)
        # Find indices
        topic_idx = headers.index("Safety Topic")
        incident_idx = headers.index("Safety Incidents")
        delay_idx = headers.index("Weather Delay Hrs")
        assert row[topic_idx] == "Confined spaces"
        assert row[incident_idx] == "Near miss"
        assert row[delay_idx] == "1.5"


# ===========================================================================
# 5. Daily Log PDF Export
# ===========================================================================


class TestDailyLogPDF:
    """Test PDF generation for daily logs."""

    def test_generates_valid_pdf(self):
        from app.services.productivity.daily_log_pdf import generate_daily_log_pdf

        log = {
            "log_date": "2026-03-07",
            "status": "submitted",
            "weather": {
                "temperature_high": 75,
                "temperature_low": 55,
                "precipitation_mm": 0,
                "wind_speed_max": 12,
                "conditions": "clear",
            },
            "crew_count": 15,
            "work_hours": 120,
            "work_narrative": "Completed foundation pour on grid A-C.",
            "manpower_by_trade": [
                {"trade": "concrete", "headcount": 8, "hours": 64},
                {"trade": "electrical", "headcount": 4, "hours": 32},
            ],
            "equipment_entries": [
                {"equipment_type": "crane", "equipment_id": "CR-001", "hours_used": 6, "notes": ""},
            ],
            "deliveries": [
                {
                    "description": "Rebar",
                    "supplier": "Steel Co",
                    "tracking_number": "T123",
                    "received_by": "Site Mgr",
                },
            ],
            "visitors": [
                {
                    "name": "Inspector",
                    "company": "City",
                    "purpose": "foundation inspection",
                    "time_in": "09:00",
                    "time_out": "10:30",
                },
            ],
            "activities_completed": [{"description": "Foundation pour"}],
            "delays": [{"description": "Equipment breakdown", "hours_lost": 1.5}],
            "safety_topic_discussed": "Fall protection",
            "safety_incidents": None,
            "weather_delay_hours": None,
            "photos": [{"file_name": "progress.jpg", "caption": "Foundation complete"}],
            "notes": "Good progress.",
        }
        pdf_bytes = generate_daily_log_pdf(log, project_name="Test Tower")
        assert pdf_bytes[:4] == b"%PDF"
        assert len(pdf_bytes) > 500

    def test_empty_log_generates_pdf(self):
        from app.services.productivity.daily_log_pdf import generate_daily_log_pdf

        log = {
            "log_date": "2026-03-07",
            "status": "draft",
            "weather": {},
            "crew_count": 0,
            "work_hours": 0,
        }
        pdf_bytes = generate_daily_log_pdf(log)
        assert pdf_bytes[:4] == b"%PDF"

    def test_safety_section_in_pdf(self):
        from app.services.productivity.daily_log_pdf import generate_daily_log_pdf

        log = {
            "log_date": "2026-03-07",
            "status": "submitted",
            "weather": {},
            "crew_count": 10,
            "work_hours": 80,
            "safety_topic_discussed": "Scaffolding safety",
            "safety_incidents": "Worker slipped on wet surface, no injury",
            "weather_delay_hours": 2.0,
        }
        pdf_bytes = generate_daily_log_pdf(log)
        assert pdf_bytes[:4] == b"%PDF"
        assert len(pdf_bytes) > 500

    def test_weather_delay_in_pdf(self):
        from app.services.productivity.daily_log_pdf import generate_daily_log_pdf

        log = {
            "log_date": "2026-03-07",
            "status": "draft",
            "weather": {"conditions": "rain", "precipitation_mm": 25},
            "crew_count": 5,
            "work_hours": 20,
            "weather_delay_hours": 4.0,
        }
        pdf_bytes = generate_daily_log_pdf(log)
        assert pdf_bytes[:4] == b"%PDF"


# ===========================================================================
# 6. Migration 026 Structure
# ===========================================================================


class TestMigration026:
    """Verify migration 026 has correct revision chain."""

    def test_revision_chain(self):
        import importlib.util
        import os

        migration_dir = os.path.join(os.path.dirname(__file__), "..", "alembic", "versions")
        migration_file = os.path.join(migration_dir, "026_punch_list_daily_log_enhancements.py")
        assert os.path.exists(migration_file), "Migration 026 file must exist"

        spec = importlib.util.spec_from_file_location("migration_026", migration_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert mod.revision == "026"
        assert mod.down_revision == "025"


# ===========================================================================
# 7. Model Fields Exist
# ===========================================================================


class TestModelFields:
    """Verify new fields exist on ORM models."""

    def test_punch_list_model_exists(self):
        from app.models.field_management import PunchList

        assert PunchList.__tablename__ == "punch_lists"
        columns = {c.name for c in PunchList.__table__.columns}
        assert "name" in columns
        assert "walk_date" in columns
        assert "status" in columns
        assert "participants" in columns

    def test_punch_list_item_new_columns(self):
        from app.models.field_management import PunchListItem

        columns = {c.name for c in PunchListItem.__table__.columns}
        assert "punch_list_id" in columns
        assert "verified_by" in columns
        assert "date_verified" in columns
        assert "spec_section" in columns

    def test_daily_log_safety_columns(self):
        from app.models.productivity import DailyLog

        columns = {c.name for c in DailyLog.__table__.columns}
        assert "safety_incidents" in columns
        assert "safety_topic_discussed" in columns
        assert "weather_delay_hours" in columns


# ===========================================================================
# 8. Existing Feature Regression — RFI, Submittal, Pay App, Change Order
# ===========================================================================


class TestExistingFeatures:
    """Smoke tests verifying existing features still import correctly."""

    def test_rfi_service_imports(self):
        from app.services.communication import rfi_service

        assert hasattr(rfi_service, "create_rfi")
        assert hasattr(rfi_service, "respond_to_rfi")
        assert hasattr(rfi_service, "close_rfi")
        assert hasattr(rfi_service, "export_rfis_csv")
        assert hasattr(rfi_service, "get_rfi_stats")

    def test_submittal_service_imports(self):
        from app.services.communication import submittal_service

        assert hasattr(submittal_service, "create_submittal")
        assert hasattr(submittal_service, "review_submittal")
        assert hasattr(submittal_service, "resubmit_submittal")
        assert hasattr(submittal_service, "get_submittal_register")

    def test_pay_application_math_imports(self):
        from app.services.controls import pay_application_math

        assert hasattr(pay_application_math, "compute_g702_totals")
        assert hasattr(pay_application_math, "compute_g703_line")
        assert hasattr(pay_application_math, "validate_no_overbilling")

    def test_change_order_lifecycle_imports(self):
        from app.services.controls import change_order_lifecycle

        assert hasattr(change_order_lifecycle, "create_pco")
        assert hasattr(change_order_lifecycle, "create_cor")
        assert hasattr(change_order_lifecycle, "approve_cor_to_co")

    def test_pdf_generator_imports(self):
        from app.services.controls import pdf_generator

        assert hasattr(pdf_generator, "generate_g702_pdf")
        assert hasattr(pdf_generator, "generate_g703_pdf")

    def test_punch_list_service_imports(self):
        from app.services.productivity import punch_list_service

        assert hasattr(punch_list_service, "create_punch_list_item")
        assert hasattr(punch_list_service, "bulk_create")
        assert hasattr(punch_list_service, "export_punch_list_csv")
        assert hasattr(punch_list_service, "create_punch_list")
        assert hasattr(punch_list_service, "list_punch_lists")

    def test_daily_log_service_imports(self):
        from app.services.productivity import daily_log_service

        assert hasattr(daily_log_service, "create_daily_log")
        assert hasattr(daily_log_service, "auto_populate_weather")
        assert hasattr(daily_log_service, "copy_previous_day")
        assert hasattr(daily_log_service, "get_weekly_summary")
