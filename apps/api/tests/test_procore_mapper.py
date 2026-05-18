"""Tests for Procore ↔ ConstructAI model mapping.

The mappers are pure (no DB, no API calls) — easy to fully cover.
Pin: every status / priority translation, the date parser fallback,
the address builder, and the budget aggregation that feeds EVM.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

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

# =========================================================================
# _parse_date
# =========================================================================


def test_parse_date_iso_format():
    assert _parse_date("2026-04-25") == date(2026, 4, 25)


def test_parse_date_iso_with_z_suffix():
    """Procore returns dates with Z suffix — parser must handle UTC."""
    assert _parse_date("2026-04-25T12:00:00Z") == date(2026, 4, 25)


def test_parse_date_none_returns_none():
    assert _parse_date(None) is None


def test_parse_date_empty_string_returns_none():
    assert _parse_date("") is None


def test_parse_date_garbage_returns_none():
    """Malformed date string → None (not raise)."""
    assert _parse_date("not-a-date") is None


# =========================================================================
# _build_address
# =========================================================================


def test_build_address_full():
    p = ProcoreProject(id=1, name="X", address="123 Main St", city="Springfield", state_code="IL")
    assert _build_address(p) == "123 Main St, Springfield, IL"


def test_build_address_partial():
    p = ProcoreProject(id=1, name="X", city="Springfield", state_code="IL")
    assert _build_address(p) == "Springfield, IL"


def test_build_address_empty_returns_none():
    p = ProcoreProject(id=1, name="X")
    assert _build_address(p) is None


# =========================================================================
# map_procore_project
# =========================================================================


def test_map_project_active_status():
    p = ProcoreProject(id=42, name="Alpha", status="Active")
    out = map_procore_project(p, org_id="org-1")
    assert out["status"] == "active"
    assert out["procore_id"] == 42
    assert out["data_source"] == "procore"
    assert out["name"] == "Alpha"


def test_map_project_inactive_to_archived():
    p = ProcoreProject(id=42, name="X", status="Inactive")
    assert map_procore_project(p, "org")["status"] == "archived"


def test_map_project_pending_to_preconstruction():
    p = ProcoreProject(id=42, name="X", status="Pending")
    assert map_procore_project(p, "org")["status"] == "preconstruction"


def test_map_project_unknown_status_falls_back_to_preconstruction():
    p = ProcoreProject(id=42, name="X", status="Cancelled")
    assert map_procore_project(p, "org")["status"] == "preconstruction"


def test_map_project_no_status_falls_back_to_preconstruction():
    """Empty status string also defaults — pin so nullable Procore
    records don't surface empty strings to the DB."""
    p = ProcoreProject(id=42, name="X")
    assert map_procore_project(p, "org")["status"] == "preconstruction"


def test_map_project_dates_parsed():
    p = ProcoreProject(
        id=42,
        name="X",
        start_date="2026-01-15",
        completion_date="2026-12-31",
    )
    out = map_procore_project(p, "org")
    assert out["start_date"] == date(2026, 1, 15)
    assert out["end_date"] == date(2026, 12, 31)


def test_map_project_metadata_carries_procore_fields():
    p = ProcoreProject(id=42, name="X", city="Boston", state_code="MA")
    out = map_procore_project(p, "org")
    assert out["metadata_"]["procore"]["city"] == "Boston"
    assert out["metadata_"]["procore"]["state_code"] == "MA"


# =========================================================================
# map_procore_rfi
# =========================================================================


def test_map_rfi_status_translation():
    """Open/Closed/Draft canonical mappings."""
    for procore_status, expected in [("Open", "open"), ("Closed", "closed"), ("Draft", "draft")]:
        r = ProcoreRFI(id=1, subject="x", status=procore_status)
        assert map_procore_rfi(r, "p")["status"] == expected


def test_map_rfi_priority_translation():
    for procore_pri, expected in [("High", "high"), ("Normal", "normal"), ("Low", "low")]:
        r = ProcoreRFI(id=1, subject="x", priority=procore_pri)
        assert map_procore_rfi(r, "p")["priority"] == expected


def test_map_rfi_unknown_status_defaults_open():
    r = ProcoreRFI(id=1, subject="x", status="Pending")
    assert map_procore_rfi(r, "p")["status"] == "open"


def test_map_rfi_unknown_priority_defaults_normal():
    r = ProcoreRFI(id=1, subject="x", priority="Critical")
    assert map_procore_rfi(r, "p")["priority"] == "normal"


def test_map_rfi_uses_number_when_present():
    r = ProcoreRFI(id=99, number=42, subject="x")
    out = map_procore_rfi(r, "p")
    assert out["rfi_number"] == "42"


def test_map_rfi_falls_back_to_id_when_number_missing():
    """[fallback] Procore RFI with no "number" field → use id as
    rfi_number so we always have a unique handle for display."""
    r = ProcoreRFI(id=99, subject="x")  # no number
    out = map_procore_rfi(r, "p")
    assert out["rfi_number"] == "99"


def test_map_rfi_question_mirrors_subject():
    """Procore doesn't expose question separately — we use subject as
    placeholder for both fields."""
    r = ProcoreRFI(id=1, subject="Where does the rebar go?")
    out = map_procore_rfi(r, "p")
    assert out["subject"] == out["question"]


# =========================================================================
# map_procore_document
# =========================================================================


def test_map_document_basic():
    d = ProcoreDocument(
        id=1,
        name="Drawing.pdf",
        filename="Drawing.pdf",
        document_type="drawing",
        file_size=12345,
    )
    out = map_procore_document(d, "p")
    assert out["procore_id"] == 1
    assert out["title"] == "Drawing.pdf"
    assert out["original_filename"] == "Drawing.pdf"
    assert out["file_size_bytes"] == 12345
    assert out["type"] == "drawing"
    assert out["processing_status"] == "pending"


def test_map_document_uses_name_when_filename_missing():
    """Some Procore documents have no filename — fall back to name."""
    d = ProcoreDocument(id=1, name="Spec Section 03.pdf")
    out = map_procore_document(d, "p")
    assert out["original_filename"] == "Spec Section 03.pdf"


def test_map_document_unknown_type_defaults_general():
    d = ProcoreDocument(id=1, name="X")  # no document_type
    assert map_procore_document(d, "p")["type"] == "general"


# =========================================================================
# map_procore_change_order
# =========================================================================


def test_map_change_order_basic():
    co = ProcoreChangeOrder(
        id=1, number=5, title="Add elevator", status="Approved", grand_total=50000.0
    )
    out = map_procore_change_order(co, "p")
    assert out["procore_id"] == 1
    assert out["co_number"] == "5"
    assert out["title"] == "Add elevator"
    assert out["status"] == "approved"  # lowercased
    assert out["cost_impact"] == Decimal("50000.0")
    assert out["change_type"] == "owner_request"


def test_map_change_order_falls_back_to_id_when_number_missing():
    co = ProcoreChangeOrder(id=99, title="X")
    out = map_procore_change_order(co, "p")
    assert out["co_number"] == "99"


def test_map_change_order_zero_grand_total():
    co = ProcoreChangeOrder(id=1, title="X")  # no grand_total
    out = map_procore_change_order(co, "p")
    assert out["cost_impact"] == Decimal("0")


def test_map_change_order_status_lowercased():
    co = ProcoreChangeOrder(id=1, title="X", status="PENDING REVIEW")
    out = map_procore_change_order(co, "p")
    assert out["status"] == "pending review"


# =========================================================================
# map_procore_daily_log
# =========================================================================


def test_map_daily_log_basic():
    dl = ProcoreDailyLog(
        id=1,
        log_date="2026-04-25",
        weather={"temp": 72, "conditions": "sunny"},
        notes="Slab pour completed.",
    )
    out = map_procore_daily_log(dl, "p")
    assert out["log_date"] == date(2026, 4, 25)
    assert out["weather"] == {"temp": 72, "conditions": "sunny"}
    assert out["notes"] == "Slab pour completed."


def test_map_daily_log_no_date_uses_today():
    """If Procore log has no log_date, default to today."""
    dl = ProcoreDailyLog(id=1)
    out = map_procore_daily_log(dl, "p")
    assert out["log_date"] == date.today()


def test_map_daily_log_no_weather_empty_dict():
    """[defensive] None weather → empty dict (not None) so DB JSON
    column doesn't store nullable."""
    dl = ProcoreDailyLog(id=1, log_date="2026-04-25")
    out = map_procore_daily_log(dl, "p")
    assert out["weather"] == {}


# =========================================================================
# map_procore_budget_to_evm
# =========================================================================


def test_map_budget_empty_zero():
    out = map_procore_budget_to_evm([])
    assert out["planned_value"] == Decimal(0)
    assert out["original_budget"] == Decimal(0)


def test_map_budget_aggregates_line_items():
    """Sum of all line items' original_budget_amount."""
    items = [
        ProcoreBudgetLineItem(id=1, original_budget_amount=100000.0),
        ProcoreBudgetLineItem(id=2, original_budget_amount=50000.0),
        ProcoreBudgetLineItem(id=3, original_budget_amount=25000.0),
    ]
    out = map_procore_budget_to_evm(items)
    assert out["planned_value"] == Decimal("175000.0")
    assert out["original_budget"] == Decimal("175000.0")


def test_map_budget_handles_none_amounts():
    """[defensive] Line items with no original_budget_amount → counted as 0."""
    items = [
        ProcoreBudgetLineItem(id=1, original_budget_amount=100000.0),
        ProcoreBudgetLineItem(id=2),  # no amount
    ]
    out = map_procore_budget_to_evm(items)
    assert out["planned_value"] == Decimal("100000.0")


# =========================================================================
# to_procore_project (reverse mapping)
# =========================================================================


def test_to_procore_project_basic():
    """Round-trip a ConstructAI project to Procore format."""

    class FakeProject:
        name = "Test Project"
        project_number = "TP-001"
        address = "123 Main St"
        start_date = date(2026, 1, 15)
        end_date = date(2026, 12, 31)

    out = to_procore_project(FakeProject())
    assert out["name"] == "Test Project"
    assert out["project_number"] == "TP-001"
    assert out["start_date"] == "2026-01-15"
    assert out["completion_date"] == "2026-12-31"


def test_to_procore_project_handles_no_dates():
    """No start/end date → None passthrough."""

    class FakeProject:
        name = "X"
        project_number = None
        address = None
        start_date = None
        end_date = None

    out = to_procore_project(FakeProject())
    assert out["start_date"] is None
    assert out["completion_date"] is None
