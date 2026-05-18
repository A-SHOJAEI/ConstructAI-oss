"""Tests for RFI service helpers (status sets, overdue logic, serialization).

Pin documented status/priority enums, the OVERDUE_DAYS thresholds
(priority-based deadlines), the Procore-owned field set (M-X RFI
sync invariant), the check_overdue rule, and the _rfi_to_dict
schema (UI rendering depends on this exact key set).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from app.services.communication.rfi_service import (
    _PROCORE_OWNED_FIELDS,
    OVERDUE_DAYS,
    VALID_PRIORITIES,
    VALID_STATUSES,
    _compute_days_open,
    _rfi_to_dict,
    check_overdue,
)

# =========================================================================
# Constants — pin documented enums
# =========================================================================


def test_valid_statuses_canonical():
    """[contract] 6 documented statuses (draft/open/pending_review/
    answered/closed/void). Pin: refactor must NOT silently add or
    drop a status — DB checks + UI filters depend on this."""
    expected = {"draft", "open", "pending_review", "answered", "closed", "void"}
    assert expected == VALID_STATUSES


def test_valid_priorities_canonical():
    """[contract] 4 priorities (urgent/high/normal/low) — drives
    overdue thresholds + dashboard color coding."""
    expected = {"urgent", "high", "normal", "low"}
    assert expected == VALID_PRIORITIES


def test_overdue_days_canonical_thresholds():
    """[business invariant] Priority-based overdue thresholds:
    urgent=3d, high=5d, normal=7d, low=14d. Pin: refactor must NOT
    relax thresholds (would mask schedule risk on RFI dashboard)."""
    assert OVERDUE_DAYS == {
        "urgent": 3,
        "high": 5,
        "normal": 7,
        "low": 14,
    }


def test_overdue_days_strictly_increasing_with_lower_priority():
    """[invariant] Higher priority -> shorter deadline.
    urgent < high < normal < low."""
    assert OVERDUE_DAYS["urgent"] < OVERDUE_DAYS["high"]
    assert OVERDUE_DAYS["high"] < OVERDUE_DAYS["normal"]
    assert OVERDUE_DAYS["normal"] < OVERDUE_DAYS["low"]


def test_procore_owned_fields_canonical():
    """[business invariant] M-X: subject/question/rfi_number are
    owned by Procore — local edits are blocked when data_source is
    Procore. Pin so a refactor doesn't accidentally allow local
    edits to drift from Procore-of-record."""
    assert {"subject", "question", "rfi_number"} == _PROCORE_OWNED_FIELDS


# =========================================================================
# check_overdue — priority-based deadlines
# =========================================================================


def _rfi(
    *,
    status: str = "open",
    priority: str = "normal",
    due_date: date | None = None,
    created_at: datetime | None = None,
):
    return SimpleNamespace(
        status=status,
        priority=priority,
        due_date=due_date,
        created_at=created_at,
    )


def test_overdue_false_for_closed_rfi():
    """[contract] Only 'open' and 'pending_review' RFIs can be
    overdue. Pin: closed/answered/draft/void RFIs are NOT overdue
    even if the deadline passed."""
    assert (
        check_overdue(
            _rfi(
                status="closed",
                priority="urgent",
                due_date=date(2020, 1, 1),
            )
        )
        is False
    )
    assert (
        check_overdue(
            _rfi(
                status="answered",
                priority="urgent",
                due_date=date(2020, 1, 1),
            )
        )
        is False
    )
    assert (
        check_overdue(
            _rfi(
                status="draft",
                priority="urgent",
                due_date=date(2020, 1, 1),
            )
        )
        is False
    )


def test_overdue_uses_explicit_due_date_over_priority():
    """[contract] When due_date is set, it overrides the priority
    threshold."""
    rfi = _rfi(
        status="open",
        priority="low",  # would be 14 days
        due_date=date(2020, 1, 1),  # past — overdue regardless of priority
    )
    assert check_overdue(rfi) is True


def test_overdue_uses_priority_threshold_when_no_due_date():
    """No explicit due_date -> falls back to created_at + threshold."""
    # Created 10 days ago, priority='high' (5-day threshold) -> overdue
    rfi = _rfi(
        status="open",
        priority="high",
        due_date=None,
        created_at=datetime.now(UTC) - timedelta(days=10),
    )
    assert check_overdue(rfi) is True


def test_overdue_just_under_threshold_returns_false():
    """[boundary] Priority='urgent' (3-day threshold), created 2 days
    ago -> NOT overdue yet."""
    rfi = _rfi(
        status="open",
        priority="urgent",
        due_date=None,
        created_at=datetime.now(UTC) - timedelta(days=2),
    )
    assert check_overdue(rfi) is False


def test_overdue_unknown_priority_uses_7_day_default():
    """[fallback] Unknown priority -> 7 day default. Pin: refactor
    must NOT relax this default to a longer window."""
    rfi = _rfi(
        status="open",
        priority="weird",  # not in OVERDUE_DAYS
        due_date=None,
        created_at=datetime.now(UTC) - timedelta(days=10),
    )
    assert check_overdue(rfi) is True


def test_overdue_no_deadline_no_created_at_returns_false():
    """[edge case] No due_date AND no created_at -> can't compute
    deadline -> NOT overdue (don't fabricate)."""
    rfi = _rfi(
        status="open",
        priority="urgent",
        due_date=None,
        created_at=None,
    )
    assert check_overdue(rfi) is False


def test_overdue_pending_review_status_eligible():
    """[business invariant] 'pending_review' status is also eligible
    for overdue check (they're awaiting answer too)."""
    rfi = _rfi(
        status="pending_review",
        priority="urgent",
        due_date=date(2020, 1, 1),
    )
    assert check_overdue(rfi) is True


# =========================================================================
# _compute_days_open
# =========================================================================


def test_days_open_no_created_at_returns_none():
    """[edge case] Missing created_at -> None (don't fabricate)."""
    rfi = SimpleNamespace(created_at=None, date_closed=None)
    assert _compute_days_open(rfi) is None


def test_days_open_uses_now_when_not_closed():
    """Open RFI -> days from created_at to today."""
    created = datetime.now(UTC) - timedelta(days=5)
    rfi = SimpleNamespace(created_at=created, date_closed=None)
    assert _compute_days_open(rfi) == 5


def test_days_open_uses_date_closed_when_closed():
    """Closed RFI -> days from created_at to date_closed (NOT today —
    a long-closed RFI shouldn't keep accumulating days)."""
    created = datetime(2026, 1, 1, tzinfo=UTC)
    closed = datetime(2026, 1, 11, tzinfo=UTC)
    rfi = SimpleNamespace(created_at=created, date_closed=closed)
    assert _compute_days_open(rfi) == 10


def test_days_open_zero_when_created_today():
    """Same-day RFI -> 0 days."""
    today = datetime.now(UTC)
    rfi = SimpleNamespace(created_at=today, date_closed=None)
    assert _compute_days_open(rfi) == 0


# =========================================================================
# _rfi_to_dict — serialization shape
# =========================================================================


def _full_rfi():
    """Build a fully-populated RFI namespace for serialization tests."""
    from decimal import Decimal

    return SimpleNamespace(
        id="rfi-1",
        project_id="proj-1",
        rfi_number="RFI-001",
        subject="Concrete strength",
        question="What PSI for foundation walls?",
        answer="4000 PSI per spec section 03 30 00",
        status="answered",
        priority="high",
        submitted_by="user-1",
        assigned_to="user-2",
        ball_in_court="user-2",
        response="See spec",
        ai_suggested_response="See spec section 03 30 00",
        due_date=date(2026, 5, 1),
        spec_section="03 30 00",
        drawing_reference="A-101",
        cost_impact=False,
        schedule_impact=False,
        cost_impact_amount=Decimal("1500.00"),
        schedule_impact_days=2,
        distribution_list=["a@x.com", "b@x.com"],
        date_sent=datetime(2026, 4, 1, tzinfo=UTC),
        date_answered=datetime(2026, 4, 5, tzinfo=UTC),
        date_closed=None,
        responded_at=datetime(2026, 4, 5, tzinfo=UTC),
        data_source="local",
        created_at=datetime(2026, 4, 1, tzinfo=UTC),
        updated_at=datetime(2026, 4, 5, tzinfo=UTC),
    )


def test_rfi_to_dict_canonical_keys():
    """[contract] Pin all 27 documented fields. UI rendering depends
    on these exact keys — refactor must NOT silently rename or drop."""
    out = _rfi_to_dict(_full_rfi())
    expected = {
        "id",
        "project_id",
        "rfi_number",
        "subject",
        "question",
        "answer",
        "status",
        "priority",
        "submitted_by",
        "assigned_to",
        "ball_in_court",
        "response",
        "ai_suggested_response",
        "due_date",
        "spec_section",
        "drawing_reference",
        "cost_impact",
        "schedule_impact",
        "cost_impact_amount",
        "schedule_impact_days",
        "distribution_list",
        "date_sent",
        "date_answered",
        "date_closed",
        "responded_at",
        "data_source",
        "created_at",
        "updated_at",
    }
    assert set(out) == expected


def test_rfi_to_dict_decimal_to_float_conversion():
    """[contract] Decimal cost_impact_amount -> float (JSON
    serializability). Pin: refactor must NOT leave it as Decimal
    (would break json.dumps in API)."""
    rfi = _full_rfi()
    out = _rfi_to_dict(rfi)
    assert isinstance(out["cost_impact_amount"], float)
    assert out["cost_impact_amount"] == 1500.0


def test_rfi_to_dict_none_cost_amount_stays_none():
    """No cost_impact_amount -> None (NOT 0.0 — distinguish 'unknown'
    from 'zero')."""
    from decimal import Decimal

    rfi = _full_rfi()
    rfi.cost_impact_amount = None
    out = _rfi_to_dict(rfi)
    assert out["cost_impact_amount"] is None

    # Zero Decimal -> falsy so also None per current logic:
    rfi.cost_impact_amount = Decimal("0")
    out = _rfi_to_dict(rfi)
    assert out["cost_impact_amount"] is None


def test_rfi_to_dict_preserves_status_and_priority():
    rfi = _full_rfi()
    out = _rfi_to_dict(rfi)
    assert out["status"] == "answered"
    assert out["priority"] == "high"


def test_rfi_to_dict_preserves_date_objects():
    """[contract] Date/datetime objects passed through unchanged
    (FastAPI serializes via Pydantic, not by stringifying here).
    Pin: refactor must NOT pre-stringify dates here."""
    rfi = _full_rfi()
    out = _rfi_to_dict(rfi)
    assert isinstance(out["due_date"], date)
    assert isinstance(out["created_at"], datetime)
